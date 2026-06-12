"""Rate hot-route cache: miss→populate→hit, invalidate→refetch, Redis down→fail-open."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from redis import Redis
from redis.exceptions import RedisError

from freight.db.repository import RateKey, RateRecord
from freight.rates import CachedRateLookup

DEFAULT_REDIS = "redis://localhost:6379/15"

_KEY = RateKey(
    origin_city="Chicago",
    origin_state="IL",
    dest_city="Dallas",
    dest_state="TX",
    equipment="dry_van",
)


def _rate() -> RateRecord:
    return RateRecord(
        id="r1",
        amount_cents=125000,
        currency="USD",
        source="contracted",
        carrier_id=None,
        effective_from=datetime(2026, 6, 1, tzinfo=UTC),
    )


class _SpyLookup:
    def __init__(self, rate: RateRecord | None) -> None:
        self._rate = rate
        self.calls = 0

    def current_contracted_rate(
        self, key: RateKey, carrier_id: str | None = None
    ) -> RateRecord | None:
        self.calls += 1
        return self._rate


# --------------------------------------------------------------------------- #
# fail-open (unit — always runs)
# --------------------------------------------------------------------------- #
def test_fail_open_when_redis_down() -> None:
    spy = _SpyLookup(_rate())
    bad = Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=1, socket_timeout=1
    )
    cache = CachedRateLookup(spy, bad)
    assert cache.current_contracted_rate(_KEY) == _rate()  # served from DB
    assert spy.calls == 1  # went to DB, no crash


# --------------------------------------------------------------------------- #
# cache behavior (integration — dedicated Redis db, skip if unreachable)
# --------------------------------------------------------------------------- #
@pytest.fixture
def client() -> Iterator[Redis]:
    redis_client: Redis = Redis.from_url(
        os.environ.get("RATE_CACHE_TEST_REDIS", DEFAULT_REDIS),
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        redis_client.ping()
    except RedisError as exc:
        pytest.skip(f"redis not reachable: {exc}")
    try:
        yield redis_client
    finally:
        redis_client.flushdb()


@pytest.mark.integration
def test_miss_populates_then_hit_skips_db(client: Redis) -> None:
    spy = _SpyLookup(_rate())
    cache = CachedRateLookup(spy, client, ttl=60)

    assert cache.current_contracted_rate(_KEY) == _rate()  # miss → DB
    assert spy.calls == 1
    assert cache.current_contracted_rate(_KEY) == _rate()  # hit → no DB
    assert spy.calls == 1


@pytest.mark.integration
def test_invalidate_forces_refetch(client: Redis) -> None:
    spy = _SpyLookup(_rate())
    cache = CachedRateLookup(spy, client, ttl=60)

    cache.current_contracted_rate(_KEY)  # cached
    cache.invalidate(_KEY)
    cache.current_contracted_rate(_KEY)  # re-fetch after invalidation
    assert spy.calls == 2


@pytest.mark.integration
def test_invalidate_clears_carrier_specific_probe_too(client: Redis) -> None:
    spy = _SpyLookup(_rate())
    cache = CachedRateLookup(spy, client, ttl=60)

    cache.current_contracted_rate(_KEY, carrier_id="c1")  # carrier-specific cached
    cache.invalidate(_KEY)  # lane-prefix scan clears it
    cache.current_contracted_rate(_KEY, carrier_id="c1")
    assert spy.calls == 2
