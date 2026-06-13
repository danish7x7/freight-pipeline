"""Global LLM-call budget guard (Phase 6.4).

A decorator over any ``LLMClient`` that caps how many model calls the system makes per
window, so a flood of inbound emails (or an attacker who gets past ingest) can't run up
unbounded Hugging Face inference cost. Wired in ``build_llm_client`` so EVERY call site
is guarded with no call-site change — honoring the "build against interfaces, swap by
config" invariant.

On a trip it raises ``LLMRateLimitError``. This is **transient backpressure**, not a
content failure: the exception propagates out of the consumer exactly like
``HFTransientError`` (uncaught by the /ingest route's ``except IngestError`` → 5xx →
QStash retries → DLQ on exhaustion). Retrying is the correct response — the budget
refills — whereas a content failure would route to ``needs_review``. FAIL-OPEN on a
Redis outage (delegate to the model), consistent with the limiter discipline.
"""

from pydantic import BaseModel

from freight.interfaces import LLMClient
from freight.interfaces.types import LLMResult
from freight.security.rate_limit import RateLimiter

_WINDOW_SECONDS = 60
_BUDGET_KEY = "llm:calls"


class LLMRateLimitError(Exception):
    """The global LLM-call budget is exhausted for this window (retry later)."""


class GuardedLLMClient:
    """Wrap an ``LLMClient`` with a global per-window call budget."""

    def __init__(
        self,
        inner: LLMClient,
        limiter: RateLimiter,
        *,
        limit: int,
        window_seconds: int = _WINDOW_SECONDS,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._limit = limit
        self._window = window_seconds

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        if not self._limiter.allow(_BUDGET_KEY, self._limit, self._window):
            raise LLMRateLimitError("LLM call budget exhausted for this window")
        return await self._inner.complete(prompt, schema=schema)
