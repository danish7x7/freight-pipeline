"""RedisCache: non-authoritative pre-check + fail-open behavior.

The fail-open test is a pure unit test (no server needed). The NX/TTL tests are
integration and skip when Redis is unreachable; they target the compose redis service.
"""

import os
import time
import uuid

import pytest
from redis import Redis
from redis.exceptions import RedisError

from freight.cache import RedisCache

DEFAULT_URL = "redis://localhost:6379/0"


def _fresh_key() -> str:
    return f"test:ingest:{uuid.uuid4()}"


# --------------------------------------------------------------------------- #
# Fail open (unit — always runs)
# --------------------------------------------------------------------------- #
def test_claim_pre_check_fails_open_when_redis_down() -> None:
    # Port 1 refuses connections; with the short timeout this errors fast.
    cache = RedisCache.from_url("redis://127.0.0.1:1/0")
    # Must PROCEED (True), not raise, when Redis is unreachable.
    assert cache.claim_pre_check(_fresh_key()) is True


def test_claim_pre_check_fails_open_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = RedisCache.from_url(DEFAULT_URL)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RedisError("simulated outage")

    monkeypatch.setattr(cache._client, "set", _boom)
    assert cache.claim_pre_check(_fresh_key()) is True


# --------------------------------------------------------------------------- #
# NX / TTL semantics (integration — skip if Redis unreachable)
# --------------------------------------------------------------------------- #
@pytest.fixture
def cache() -> RedisCache:
    url = os.environ.get("REDIS_TEST_URL", DEFAULT_URL)
    client: Redis = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    try:
        client.ping()
    except RedisError as exc:
        pytest.skip(f"redis not reachable: {exc}")
    return RedisCache(client)


@pytest.mark.integration
def test_first_call_proceeds_second_skips(cache: RedisCache) -> None:
    key = _fresh_key()
    assert cache.claim_pre_check(key, ttl=60) is True   # newly set -> proceed
    assert cache.claim_pre_check(key, ttl=60) is False  # exists -> skip


@pytest.mark.integration
def test_ttl_expiry_allows_reproceed(cache: RedisCache) -> None:
    key = _fresh_key()
    assert cache.claim_pre_check(key, ttl=1) is True
    assert cache.claim_pre_check(key, ttl=1) is False
    time.sleep(1.5)  # key expires
    # After eviction, the cache "misses" and we proceed again (DB then dedupes).
    assert cache.claim_pre_check(key, ttl=1) is True
