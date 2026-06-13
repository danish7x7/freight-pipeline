"""Fixed-window rate limiting (Phase 6.4), shared by the HTTP guard and the LLM guard.

SECONDARY and FAIL-OPEN by design (the decided fork): the auth gates are the primary
access control. A Redis outage must never block a legitimate request, so ``allow``
returns ``True`` (proceed) on any ``RedisError`` — same discipline as the idempotency
and rate caches. The limiter only caps abusive volume when Redis is healthy.

Fixed-window counter: ``INCR`` the per-window key; on the first hit in a window set the
``EXPIRE`` so the window rolls. Cheap and right-sized for low volume. (A sliding-window
log would be more precise at the window edge but is overkill here.)
"""

import logging

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("freight.security.rate_limit")


class RateLimiter:
    """Non-authoritative, fail-open fixed-window limiter over Redis."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> "RateLimiter":
        # Short timeouts so a Redis outage fails open FAST rather than stalling the
        # request path (mirrors the idempotency cache).
        client: Redis = Redis.from_url(
            url, socket_connect_timeout=1, socket_timeout=1
        )
        return cls(client)

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return ``True`` if this hit is within ``limit`` for the current window.

        ``True``  — under the limit, OR Redis is down (fail open).
        ``False`` — the limit is already reached for this window.

        A ``limit`` of 0 or less disables the limit (always allow).
        """
        if limit <= 0:
            return True
        try:
            count = self._client.incr(key)
            if count == 1:
                # First hit in this window — arm the expiry so the window rolls.
                self._client.expire(key, window_seconds)
            return int(count) <= limit
        except RedisError as exc:
            logger.warning("rate limiter unavailable, failing open (allow): %s", exc)
            return True
