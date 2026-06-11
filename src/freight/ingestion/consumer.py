"""The ingestion consumer — the permanent QStash target handler.

Phase 2 job ONLY: re-fetch the committed envelope row by id and validate the envelope
(row exists + non-empty sender). On success it acks; on failure it RAISES, which the
/ingest route maps to a non-2xx so QStash retries and ultimately DLQs.

This is the entrypoint Phase 3 EXTENDS (classify intent, extract fields, validate, then
process-once) — not a throwaway. In Phase 2 it performs NO writes, so a double delivery
is observationally harmless.
"""

from typing import Protocol

from freight.db.repository import EmailRecord
from freight.interfaces.types import QueueMessage


class IngestError(Exception):
    """Envelope validation failed; the delivery should be retried (then DLQ'd)."""


class _EmailLookup(Protocol):
    """The slice of the repository the consumer depends on."""

    def get_by_gmail_id(self, gmail_message_id: str) -> EmailRecord | None: ...


class IngestConsumer:
    """Validate the envelope of a delivered message."""

    def __init__(self, repo: _EmailLookup) -> None:
        self._repo = repo

    async def handle(self, message: QueueMessage) -> None:
        """Ack a valid envelope; raise IngestError on a poison message."""
        record = self._repo.get_by_gmail_id(message.id)
        if record is None:
            raise IngestError(f"no committed row for id {message.id!r}")
        if not record.sender:
            raise IngestError(f"row {message.id!r} has empty sender")
        # Envelope is valid — ack.
        # Phase 3: extraction extends here (classify, extract, validate, then
        # process-once via a conditional UPDATE on ingest_status). No writes in Phase 2.
