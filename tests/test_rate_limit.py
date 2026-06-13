"""Phase 6.4: the fail-open fixed-window limiter and its HTTP dependency.

Hermetic — a dict-backed fake Redis emulates INCR/EXPIRE so the tests are deterministic
whether or not a real Redis is reachable. The fail-open property is proven with a fake
that raises ``RedisError``.
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from freight.config import Settings
from freight.security import http_rate_limit
from freight.security.http_rate_limit import RateLimit, get_rate_limiter
from freight.security.rate_limit import RateLimiter


class FakeRedis:
    """Minimal INCR/EXPIRE counter backing the limiter."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expired: list[str] = []

    def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, seconds: int) -> bool:
        self.expired.append(key)
        return True


class RaisingRedis:
    """Every operation fails — the Redis-outage case."""

    def incr(self, key: str) -> int:
        raise RedisError("simulated outage")

    def expire(self, key: str, seconds: int) -> bool:
        raise RedisError("simulated outage")


# --- the primitive -----------------------------------------------------------------


def test_allow_permits_up_to_limit_then_blocks() -> None:
    limiter = RateLimiter(FakeRedis())  # type: ignore[arg-type]
    assert limiter.allow("k", limit=3, window_seconds=60) is True
    assert limiter.allow("k", limit=3, window_seconds=60) is True
    assert limiter.allow("k", limit=3, window_seconds=60) is True
    assert limiter.allow("k", limit=3, window_seconds=60) is False  # 4th over the cap


def test_allow_sets_expiry_only_on_first_hit() -> None:
    fake = FakeRedis()
    limiter = RateLimiter(fake)  # type: ignore[arg-type]
    limiter.allow("k", limit=5, window_seconds=60)
    limiter.allow("k", limit=5, window_seconds=60)
    assert fake.expired == ["k"]  # the window is armed once, not re-armed each hit


def test_allow_fails_open_when_redis_raises() -> None:
    limiter = RateLimiter(RaisingRedis())  # type: ignore[arg-type]
    # Even well past any nominal limit, an outage must never block.
    assert all(
        limiter.allow("k", limit=1, window_seconds=60) for _ in range(5)
    )


def test_zero_limit_disables() -> None:
    limiter = RateLimiter(RaisingRedis())  # type: ignore[arg-type]
    assert limiter.allow("k", limit=0, window_seconds=60) is True


# --- the HTTP dependency -----------------------------------------------------------


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/x", dependencies=[Depends(RateLimit("x"))])
    async def _x() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_http_dependency_429s_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        http_rate_limit,
        "get_settings",
        lambda: Settings(rate_limit_enabled=True, public_rate_limit_per_minute=2),
    )
    app = _app()
    limiter = RateLimiter(FakeRedis())  # type: ignore[arg-type]  # one instance => counter persists
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    with TestClient(app) as client:
        assert client.post("/x").status_code == 200
        assert client.post("/x").status_code == 200
        assert client.post("/x").status_code == 429


def test_http_dependency_fails_open_on_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_rate_limit,
        "get_settings",
        lambda: Settings(rate_limit_enabled=True, public_rate_limit_per_minute=1),
    )
    app = _app()
    app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(RaisingRedis())  # type: ignore[arg-type]
    with TestClient(app) as client:
        # Limit is 1, but Redis is down → fail open → never 429.
        for _ in range(3):
            assert client.post("/x").status_code == 200


def test_http_dependency_disabled_skips_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_rate_limit,
        "get_settings",
        lambda: Settings(rate_limit_enabled=False, public_rate_limit_per_minute=1),
    )
    app = _app()
    limiter = RateLimiter(FakeRedis())  # type: ignore[arg-type]
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    with TestClient(app) as client:
        for _ in range(3):
            assert client.post("/x").status_code == 200
