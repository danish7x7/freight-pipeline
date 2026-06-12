"""The ingestion consumer — the permanent QStash target handler.

Per delivery:
1. re-fetch the committed envelope row by id; validate the envelope (row + sender);
2. run extraction (one LLM call → deterministic gate → routing);
3. write the outcome via the process-once conditional UPDATE (WHERE ingest_status=
   'queued'): the delivery that flips the row wins; 0 rows → already processed → skip.

Error mapping (the precise permanent-vs-transient form — see DECISIONS):
- envelope invalid / HFTransientError / DB error → RAISE → /ingest returns 5xx →
  QStash retries → DLQ on exhaustion (transient/infra);
- a content outcome (processed OR needs_review) is written and we return normally →
  /ingest returns 2xx → NOT retried (the consumer succeeded at routing).
"""

from typing import Any, Protocol

from freight.db.repository import (
    AttachmentRecord,
    EmailRecord,
    ExtractionStatus,
    Intent,
)
from freight.extraction import ExtractionOutcome, extract
from freight.interfaces import LLMClient
from freight.interfaces.types import QueueMessage
from freight.pdf import StorageReader, UnconfiguredStorageReader, extract_text


class IngestError(Exception):
    """Envelope validation failed; the delivery should be retried (then DLQ'd)."""


def _review(reason: str) -> ExtractionOutcome:
    """A needs_review outcome with no extracted record (content limitation)."""
    return ExtractionOutcome(
        status="needs_review",
        intent=None,
        confidence=0.0,
        extracted=None,
        review_reason=reason,
    )


class _ExtractionRepo(Protocol):
    """The slice of the repository the consumer depends on."""

    def get_by_gmail_id(self, gmail_message_id: str) -> EmailRecord | None: ...

    def get_attachments(self, email_message_id: str) -> list[AttachmentRecord]: ...

    def process_once_extraction(
        self,
        gmail_message_id: str,
        *,
        intent: Intent | None,
        confidence: float | None,
        extracted: dict[str, Any] | None,
        status: ExtractionStatus,
        review_reason: str | None = None,
    ) -> bool: ...


class IngestConsumer:
    """Validate the envelope, extract, and write the result process-once."""

    def __init__(
        self,
        repo: _ExtractionRepo,
        llm: LLMClient,
        storage: StorageReader | None = None,
    ) -> None:
        self._repo = repo
        self._llm = llm
        self._storage = storage or UnconfiguredStorageReader()

    async def handle(self, message: QueueMessage) -> None:
        """Process one delivery. Raises only on transient/infra faults (→ retry)."""
        record = self._repo.get_by_gmail_id(message.id)
        if record is None:
            raise IngestError(f"no committed row for id {message.id!r}")
        if not record.sender:
            raise IngestError(f"row {message.id!r} has empty sender")

        text, review_reason = self._resolve_content(record)
        if review_reason is not None:
            self._write(record.gmail_message_id, _review(review_reason))
            return

        # HFTransientError (cold-start/429/network) propagates → 5xx → retry.
        outcome = await extract(self._llm, record.subject, text)
        self._write(record.gmail_message_id, outcome)

    def _resolve_content(self, record: EmailRecord) -> tuple[str | None, str | None]:
        """Pick the extraction source. A PDF attachment takes priority over the body.

        Returns (text, review_reason). A non-None review_reason means skip extraction
        and route to needs_review (e.g. a PDF with no text layer).
        """
        pdfs = [
            a
            for a in self._repo.get_attachments(record.id)
            if a.file_type == "pdf"
        ]
        if pdfs:
            text = extract_text(self._storage.read(pdfs[0].storage_path))
            if text is None:
                return None, "no_text_layer"
            return text, None
        return record.body, None

    def _write(self, gmail_message_id: str, outcome: ExtractionOutcome) -> None:
        # Process-once: only the delivery that flips 'queued' does the write.
        self._repo.process_once_extraction(
            gmail_message_id,
            intent=outcome.intent,
            confidence=outcome.confidence,
            extracted=outcome.extracted,
            status=outcome.status,
            review_reason=outcome.review_reason,
        )
