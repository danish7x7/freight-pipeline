"""Per-client HTTP rate limiting for the public API (Phase 6.4).

Applied as a route-level dependency that runs BEFORE the auth gate on every externally
reachable POST route, so a flood is cheap-rejected (429) before any signature/secret/JWT
work. The auth gates remain the primary access control; this is defense-in-depth and is
fail-open (see ``RateLimiter``).

Each route gets its own ``RateLimit(scope=...)`` instance so the key namespaces don't
collide; the per-minute limit is shared config (``public_rate_limit_per_minute``).
"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from freight.config import get_settings
from freight.security.rate_limit import RateLimiter

logger = logging.getLogger("freight.security.http_rate_limit")

_WINDOW_SECONDS = 60


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    """Process-wide limiter (the counter must persist across requests).

    Overridden in tests to inject a fake-backed limiter.
    """
    return RateLimiter.from_url(get_settings().redis_url)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def _client_ip(request: Request) -> str:
    """The caller's IP for keying.

    Phase 8 carry-forward: behind the deploy proxy (Fly/Railway) this is the proxy's
    IP — wire a trusted ``X-Forwarded-For`` / platform client-IP header there for true
    per-client limiting. Until then per-IP limiting is coarse but still bounds volume.
    """
    return request.client.host if request.client else "unknown"


class RateLimit:
    """A route-level dependency that 429s a client over the per-window limit."""

    def __init__(self, scope: str) -> None:
        self._scope = scope

    def __call__(self, request: Request, limiter: RateLimiterDep) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        key = f"rl:{self._scope}:{_client_ip(request)}"
        if not limiter.allow(
            key, settings.public_rate_limit_per_minute, _WINDOW_SECONDS
        ):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
