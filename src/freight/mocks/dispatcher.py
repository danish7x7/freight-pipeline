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

FAILURE TRIGGER: the ``Handler`` raises on failure here; in cloud the FastAPI route
translates a ``handle()`` exception into a non-2xx response (QStash retries on status,
not on a Python exception). Same trigger (``handle()`` raises), two transports.
"""

import logging

from freight.interfaces.queue import Handler
from freight.interfaces.types import QueueMessage

logger = logging.getLogger("freight.dispatcher")


class LocalDispatcher:
    """Deliver messages to a ``Handler`` with bounded retries, then dead-letter."""

    def __init__(self, handler: Handler, *, retries: int = 3) -> None:
        self._handler = handler
        self._retries = retries  # attempts AFTER the first; total = retries + 1
        self.delivered: list[QueueMessage] = []
        self.dead_letter: list[QueueMessage] = []
        self.attempts = 0

    async def deliver(self, message: QueueMessage) -> None:
        """Attempt delivery up to ``retries + 1`` times; dead-letter on exhaustion."""
        for _ in range(self._retries + 1):
            self.attempts += 1
            try:
                await self._handler(message)
            except Exception:
                # Transport retries on ANY handler failure (mirrors retry-on-non-2xx).
                logger.warning("delivery attempt failed for %s", message.id)
                continue
            self.delivered.append(message)
            return
        logger.error("message %s exhausted retries; dead-lettering", message.id)
        self.dead_letter.append(message)
