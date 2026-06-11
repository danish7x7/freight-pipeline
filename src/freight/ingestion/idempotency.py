"""The idempotency claim for a single inbound message.

Owns the ordering that makes ingestion safe:
1. Redis ``SET NX`` pre-check (fast, non-authoritative, fail-open).
2. DB ``claim_insert`` — committed in its own transaction; the unique constraint on
   ``gmail_message_id`` is the authority.

The publish happens in the poller AFTER ``try_claim`` returns True, so the claim row is
always committed and visible before any queue message references it.
"""

from freight.cache.redis_client import DEFAULT_TTL_SECONDS, RedisCache
from freight.db.repository import IngestRepository
from freight.interfaces.types import InboundMessage


class ClaimGate:
    """Decide whether this poll run should publish a given message."""

    def __init__(
        self,
        repo: IngestRepository,
        cache: RedisCache,
        *,
        ttl: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._repo = repo
        self._cache = cache
        self._ttl = ttl

    def try_claim(self, message: InboundMessage) -> bool:
        """Return True if this run won the claim and should publish.

        False means skip: either the id was recently handled (Redis pre-check) or the
        committed row already exists (DB unique violation). Redis being down fails open
        to the DB claim — never a reason to skip.
        """
        key = f"ingest:{message.gmail_message_id}"
        if not self._cache.claim_pre_check(key, self._ttl):
            return False
        return self._repo.claim_insert(message)
