"""LocalDispatcher — the local stand-in for QStash's delivery semantics.

QStash is push-based and at-least-once: it POSTs each message to the consumer endpoint
and retries on a non-2xx response, moving the message to a DLQ once retries are
exhausted. This dispatcher reproduces that locally so the poison -> DLQ done-when can be
proven against the mocks.

RETRY CONVENTION (matches QStash's ``Upstash-Retries`` header): ``retries`` counts the
attempts AFTER the first, so a message is attempted ``retries + 1`` times total before
landing in the dead-letter list. Keeping the same convention means the local
poison->DLQ test asserts the SAME attempt count the cloud path produces at Phase 8 — no
"3x locally, 4x in prod" drift.

BACKOFF (7.2): a BOUNDED capped-exponential delay between attempts —
``min(max_delay, base_delay * 2**i)``. It adds delay only; the attempt count and the
dead-letter semantics are unchanged (cloud parity preserved — QStash owns the real
schedule, this is a faithful stand-in). The ``sleep`` is injectable so tests record the
schedule with zero real waiting.

DLQ REPLAY (7.2): ``replay`` re-delivers dead-lettered messages through the SAME
``Handler`` — never a side path. In cloud that handler is ``/ingest -> consumer.handle
-> finalize -> flip_if_queued`` (the process-once conditional UPDATE on
``ingest_status``), so replay rides the same idempotency claim: a still-'queued'
(transiently-failed) message processes once; an already-'processed' message flips 0 rows
and no-ops. Replay is CONTROLLED re-delivery — it cannot reintroduce double-process. A
message that fails again is re-dead-lettered (bounded; no infinite loop).

FAILURE TRIGGER: the ``Handler`` raises on failure here; in cloud the FastAPI route
translates a ``handle()`` exception into a non-2xx response (QStash retries on status,
not on a Python exception). Same trigger (``handle()`` raises), two transports.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from freight.interfaces.queue import Handler
from freight.interfaces.types import QueueMessage
from freight.observability.metrics import DLQ_SIZE

logger = logging.getLogger("freight.dispatcher")

# Injectable async sleeper (default: real asyncio.sleep), so tests don't actually wait.
Sleeper = Callable[[float], Awaitable[None]]


async def _asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


@dataclass(frozen=True)
class ReplayResult:
    """Outcome of a DLQ replay pass."""

    replayed: int  # delivered successfully this pass
    re_dead_lettered: int  # failed again, back in the DLQ


class LocalDispatcher:
    """Deliver messages to a ``Handler`` with bounded backoff, then dead-letter."""

    def __init__(
        self,
        handler: Handler,
        *,
        retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        sleep: Sleeper = _asyncio_sleep,
    ) -> None:
        self._handler = handler
        self._retries = retries  # attempts AFTER the first; total = retries + 1
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._sleep = sleep
        self.delivered: list[QueueMessage] = []
        self.dead_letter: list[QueueMessage] = []
        self.attempts = 0

    def _backoff(self, attempt_index: int) -> float:
        """Bounded capped-exponential delay before retry ``attempt_index`` (0-based)."""
        return min(self._max_delay, self._base_delay * float(2**attempt_index))

    async def deliver(self, message: QueueMessage) -> bool:
        """Attempt delivery up to ``retries + 1`` times; dead-letter on exhaustion.

        Returns True if delivered, False if dead-lettered.
        """
        for attempt in range(self._retries + 1):
            self.attempts += 1
            try:
                await self._handler(message)
            except Exception:
                # Transport retries on ANY handler failure (mirrors retry-on-non-2xx).
                logger.warning("delivery attempt failed for %s", message.id)
                if attempt < self._retries:  # not the last attempt → back off, retry
                    await self._sleep(self._backoff(attempt))
                continue
            self.delivered.append(message)
            return True
        logger.error("message %s exhausted retries; dead-lettering", message.id)
        self.dead_letter.append(message)
        DLQ_SIZE.set(len(self.dead_letter))  # push the real local DLQ depth
        return False

    async def replay(self) -> ReplayResult:
        """Re-deliver every dead-lettered message through the SAME handler.

        Controlled re-delivery: the handler carries the process-once claim
        (``flip_if_queued``), so an already-processed message no-ops — replay can never
        reintroduce double-process. Messages that fail again are re-dead-lettered.
        """
        pending = self.dead_letter
        self.dead_letter = []
        replayed = 0
        for message in pending:
            if await self.deliver(message):  # re-dead-letters itself on repeat failure
                replayed += 1
        DLQ_SIZE.set(len(self.dead_letter))  # reflect the drained/re-lettered depth
        result = ReplayResult(
            replayed=replayed, re_dead_lettered=len(self.dead_letter)
        )
        logger.info(
            "dlq replay: %d replayed, %d re-dead-lettered",
            result.replayed,
            result.re_dead_lettered,
        )
        return result
