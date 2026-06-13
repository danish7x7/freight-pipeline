"""Phase 6.4: the global LLM-call budget guard.

Under budget the guard delegates; over budget it raises ``LLMRateLimitError`` (transient
backpressure → the consumer retries). On a Redis outage it FAILS OPEN — never starves
the pipeline because the limiter can't reach Redis.
"""

import pytest
from pydantic import BaseModel
from redis.exceptions import RedisError

from freight.interfaces.types import LLMResult
from freight.security.llm_guard import GuardedLLMClient, LLMRateLimitError
from freight.security.rate_limit import RateLimiter


class CountingLLM:
    """Inner client that records how many times it was actually called."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        self.calls += 1
        return LLMResult(raw="ok")


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, seconds: int) -> bool:
        return True


class RaisingRedis:
    def incr(self, key: str) -> int:
        raise RedisError("simulated outage")

    def expire(self, key: str, seconds: int) -> bool:
        raise RedisError("simulated outage")


async def test_delegates_under_budget() -> None:
    inner = CountingLLM()
    guard = GuardedLLMClient(inner, RateLimiter(FakeRedis()), limit=3)  # type: ignore[arg-type]
    for _ in range(3):
        result = await guard.complete("hi")
        assert result.raw == "ok"
    assert inner.calls == 3


async def test_raises_over_budget_without_calling_inner() -> None:
    inner = CountingLLM()
    guard = GuardedLLMClient(inner, RateLimiter(FakeRedis()), limit=2)  # type: ignore[arg-type]
    await guard.complete("a")
    await guard.complete("b")
    with pytest.raises(LLMRateLimitError):
        await guard.complete("c")
    assert inner.calls == 2  # the tripped call never reached the model


async def test_fails_open_on_redis_outage() -> None:
    inner = CountingLLM()
    guard = GuardedLLMClient(inner, RateLimiter(RaisingRedis()), limit=1)  # type: ignore[arg-type]
    # Limit is 1, but the limiter can't reach Redis → delegate every time.
    for _ in range(3):
        await guard.complete("x")
    assert inner.calls == 3
