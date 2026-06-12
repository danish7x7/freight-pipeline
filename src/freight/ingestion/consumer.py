"""The ingestion consumer — the permanent QStash target handler (transport only).

Per delivery:
1. re-fetch the committed envelope row; validate the envelope (row + sender);
2. resolve content (PDF attachment takes priority over the body) and run extraction;
3. pre-tx: cached contracted lookup (Redis OUT of the transaction);
4. open the finalize transaction and call ``deals.finalize`` — it owns the process-once
   flip + deal/quote logic. The context manager commits/rolls back.

Error mapping (see DECISIONS): envelope invalid / HFTransientError / DB error → RAISE →
/ingest 5xx → QStash retries → DLQ. A written outcome (processed or needs_review)
returns normally → 2xx → not retried.
"""

from freight.db.repository import EmailRecord, IngestRepository, RateRecord
from freight.deals import finalize, rate_key_from
from freight.extraction import ExtractionOutcome, extract
from freight.interfaces import LLMClient
from freight.interfaces.types import QueueMessage
from freight.pdf import StorageReader, UnconfiguredStorageReader, extract_text
from freight.rates.lookup import RateLookup


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


class IngestConsumer:
    """Validate the envelope, extract, then finalize (deal/quote) process-once."""

    def __init__(
        self,
        repo: IngestRepository,
        llm: LLMClient,
        *,
        storage: StorageReader | None = None,
        rate_lookup: RateLookup | None = None,
    ) -> None:
        self._repo = repo
        self._llm = llm
        self._storage = storage or UnconfiguredStorageReader()
        # The cached lookup (or the repo itself) for the pre-tx contracted read.
        self._rate_lookup: RateLookup = rate_lookup or repo

    async def handle(self, message: QueueMessage) -> None:
        """Process one delivery. Raises only on transient/infra faults (→ retry)."""
        record = self._repo.get_by_gmail_id(message.id)
        if record is None:
            raise IngestError(f"no committed row for id {message.id!r}")
        if not record.sender:
            raise IngestError(f"row {message.id!r} has empty sender")

        text, review_reason = self._resolve_content(record)
        if review_reason is not None:
            outcome = _review(review_reason)
        else:
            # HFTransientError (cold-start/429/network) propagates → 5xx → retry.
            outcome = await extract(self._llm, record.subject, text)

        contracted = self._lookup_contracted(outcome)

        with self._repo.begin() as conn:
            finalize(
                conn,
                self._repo,
                gmail_message_id=record.gmail_message_id,
                outcome=outcome,
                contracted_rate=contracted,
            )

    def _lookup_contracted(self, outcome: ExtractionOutcome) -> RateRecord | None:
        """Pre-tx contracted rate read (only for a quotable rate_request)."""
        if outcome.status != "processed" or outcome.intent != "rate_request":
            return None
        key = rate_key_from(outcome.extracted or {})
        return self._rate_lookup.current_contracted_rate(key)

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
