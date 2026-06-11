"""The inbox poller: list -> claim -> publish, plus a DB-driven reconciliation sweep.

Two independent steps:
- **Front door** (``_ingest_new``): list new messages, claim each (Redis pre-check +
  committed DB claim), and publish an id-only thin payload. The consumer re-fetches the
  committed row — the DB is the single source of truth, so the payload can't drift.
- **Reconciliation sweep** (``_reconcile``): re-publish rows stuck in ``received`` —
  committed claims whose publish never landed (crash between commit and publish). This
  runs from DB state, **bypassing the Redis pre-check** (a live key would block the
  recovery the sweep exists for), and runs **regardless of whether listing succeeded**
  (a Gmail outage must not also freeze recovery).

SWEEP THRESHOLD: a row legitimately sits in ``received`` for the brief window between
``claim_insert`` and ``set_ingest_status('queued')``. If the threshold were shorter than
the worst-case poll runtime, an overlapping poll's sweep could re-publish a freshly
claimed in-flight row — harmless (the consumer dedupes) but wasteful. So the default
(5 min) comfortably exceeds both the worst-case poll runtime and the ~2-min cron
interval. Too short -> spurious re-publishes; too long -> recovery latency. Correctness
holds either way (process-once at the consumer); the threshold only keeps the sweep from
stepping on normal operation.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from freight.cache.redis_client import DEFAULT_TTL_SECONDS, RedisCache
from freight.config import Settings, get_settings
from freight.db.repository import IngestRepository, make_engine
from freight.factories import build_gmail_client, build_queue
from freight.ingestion.idempotency import ClaimGate
from freight.interfaces import GmailClient, Queue
from freight.interfaces.types import QueueMessage

logger = logging.getLogger("freight.poller")

# Comfortably exceeds worst-case poll runtime and the ~2-min cron interval.
DEFAULT_SWEEP_THRESHOLD = timedelta(minutes=5)


@dataclass(frozen=True)
class PollResult:
    """Outcome of one poll cycle."""

    enqueued: int  # newly claimed + published via the front door
    recovered: int  # re-published from the reconciliation sweep


class Poller:
    """Orchestrates one poll cycle (front door + reconciliation sweep)."""

    def __init__(
        self,
        *,
        gmail: GmailClient,
        queue: Queue,
        repo: IngestRepository,
        cache: RedisCache,
        sweep_threshold: timedelta = DEFAULT_SWEEP_THRESHOLD,
        ttl: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._gmail = gmail
        self._queue = queue
        self._repo = repo
        self._gate = ClaimGate(repo, cache, ttl=ttl)
        self._sweep_threshold = sweep_threshold

    async def poll(self) -> PollResult:
        enqueued = await self._ingest_new()
        recovered = await self._reconcile()  # runs regardless of the front door
        return PollResult(enqueued=enqueued, recovered=recovered)

    async def _ingest_new(self) -> int:
        try:
            messages = self._gmail.list_messages()
        except Exception:
            logger.exception("list_messages failed; sweep still runs this cycle")
            return 0
        count = 0
        for message in messages:
            if self._gate.try_claim(message):
                await self._publish(message.gmail_message_id)
                count += 1
        return count

    async def _reconcile(self) -> int:
        cutoff = datetime.now(UTC) - self._sweep_threshold
        stuck = self._repo.list_stuck_received(cutoff)
        for gmail_message_id in stuck:
            await self._publish(gmail_message_id)  # from DB row; bypasses SET NX
        if stuck:
            logger.info("reconciliation re-enqueued %d stuck row(s)", len(stuck))
        return len(stuck)

    async def _publish(self, gmail_message_id: str) -> None:
        # Claim row is already committed; publish then mark queued.
        await self._queue.publish(QueueMessage(id=gmail_message_id, payload={}))
        self._repo.set_ingest_status(gmail_message_id, "queued")


def build_poller(settings: Settings) -> Poller:
    """Construct a Poller from config (mock or real backends, per settings)."""
    engine = make_engine(settings.database_url)
    return Poller(
        gmail=build_gmail_client(settings),
        queue=build_queue(settings),
        repo=IngestRepository(engine),
        cache=RedisCache.from_url(settings.redis_url),
    )


def run_poll_once(settings: Settings | None = None) -> PollResult:
    """Run a single poll cycle (entrypoint for the cron / worker)."""
    settings = settings or get_settings()
    return asyncio.run(build_poller(settings).poll())
