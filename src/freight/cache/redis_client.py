"""Redis-backed idempotency pre-check.

NON-AUTHORITATIVE by design. The DB unique constraint on
``email_messages.gmail_message_id`` is the source of truth; this cache only lets the
poller skip the DB round-trip for ids it has very recently handled, and guards against
overlapping poll runs.

Two correctness properties:
- **Fail open.** If Redis is unreachable (connection error / timeout),
  ``claim_pre_check`` returns "proceed" — never raises, never halts ingestion. A Redis
  outage just forces every message onto the slow path (INSERT → unique constraint
  catches dupes): no loss, no double-process. Failing closed would drop messages.
- **TTL is an optimization, not correctness.** Set it to comfortably exceed one poll
  cycle. Correctness NEVER depends on the TTL — only on the DB unique constraint.

USAGE BOUNDARY: ``claim_pre_check`` is the front-door dedupe for NEWLY-LISTED messages
only. The reconciliation sweep (``IngestRepository.list_stuck_received``) MUST NOT call
it — a still-live key from the original claim would return "skip" and block the very
recovery the sweep exists for. The sweep publishes straight from the DB row. Do not let
correctness depend on "TTL < sweep threshold"; that coupling is fragile.
"""

import logging

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("freight.cache")

# Comfortably exceeds one ~2-minute poll cycle; purely an overlapping-poll optimization.
DEFAULT_TTL_SECONDS = 300


class RedisCache:
    """Thin wrapper exposing the non-authoritative idempotency pre-check."""

    def __init__(self, client: Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> "RedisCache":
        # Short timeouts so a Redis outage fails open fast rather than stalling ingest.
        client: Redis = Redis.from_url(
            url, socket_connect_timeout=1, socket_timeout=1
        )
        return cls(client)

    def claim_pre_check(self, key: str, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
        """Return ``True`` to proceed to the DB claim, ``False`` to skip a known id.

        ``True``  — key was newly set (not seen recently) OR Redis is down (fail open).
        ``False`` — key already existed (recently handled); skip the DB round-trip.
        """
        try:
            was_set = self._client.set(key, "1", nx=True, ex=ttl)
        except RedisError as exc:
            logger.warning("redis unavailable, failing open (proceed): %s", exc)
            return True
        return bool(was_set)
