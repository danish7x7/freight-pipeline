"""email_messages repository (SQLAlchemy Core, sync).

Transaction boundaries are explicit and load-bearing for idempotency:
``claim_insert`` commits the claim row in its OWN transaction and returns BEFORE any
publish happens. That ordering guarantees the consumer can never fetch a thin-payload
id whose row isn't yet visible — the only remaining failure window is a crash between
the committed claim and the publish, which leaves a ``received`` row that the
reconciliation sweep (``list_stuck_received``) re-enqueues.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
    text,
    update,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from freight.interfaces.types import InboundMessage

IngestStatus = Literal["received", "queued", "processed", "failed", "needs_review"]
Intent = Literal["rate_request", "negotiation", "rc", "contract", "other"]
# Terminal extraction outcomes the consumer writes (both return 2xx — see DECISIONS).
ExtractionStatus = Literal["processed", "needs_review"]
AttachmentFileType = Literal["pdf", "image", "other"]

_metadata = MetaData()

_EMAIL_INTENT = SAEnum(
    "rate_request", "negotiation", "rc", "contract", "other",
    name="email_intent", create_type=False,
)
_INGEST_STATUS = SAEnum(
    "received", "queued", "processed", "failed", "needs_review",
    name="email_ingest_status", create_type=False,
)

# Core mapping of the columns this repository touches. NOT a migration — the schema is
# owned by supabase/migrations. Columns the repo doesn't use are intentionally omitted.
email_messages = Table(
    "email_messages",
    _metadata,
    Column(
        "id",
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("gmail_message_id", String, nullable=False, unique=True),
    Column("thread_id", String),
    Column("sender", String, nullable=False),
    Column("subject", Text),
    Column("body", Text),
    Column("received_at", DateTime(timezone=True), nullable=False),
    # Enums are bound to the existing Postgres types (owned by migrations, not created).
    Column("ingest_status", _INGEST_STATUS, nullable=False),
    Column("intent", _EMAIL_INTENT),
    Column("confidence", Float),
    Column("extracted", JSONB),
    Column("review_reason", Text),
    Column("created_at", DateTime(timezone=True)),
)

_ATTACHMENT_FILE_TYPE = SAEnum(
    "pdf", "image", "other", name="attachment_file_type", create_type=False,
)

attachments = Table(
    "attachments",
    _metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("email_message_id", UUID(as_uuid=False), nullable=False),
    Column("storage_path", String, nullable=False),
    Column("file_type", _ATTACHMENT_FILE_TYPE, nullable=False),
    Column("mime_type", String),
)


class EmailRecord(BaseModel):
    """A persisted email envelope as the ingestion path sees it."""

    id: str
    gmail_message_id: str
    thread_id: str | None
    sender: str
    subject: str | None
    body: str | None
    received_at: datetime
    ingest_status: IngestStatus


class AttachmentRecord(BaseModel):
    """A persisted attachment row (for PDF intake)."""

    id: str
    storage_path: str
    file_type: AttachmentFileType
    mime_type: str | None


def make_engine(database_url: str) -> Engine:
    """Build a sync Engine, normalizing the URL to the psycopg (v3) driver."""
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    return create_engine(database_url, pool_pre_ping=True)


class IngestRepository:
    """Claim, fetch, and advance the status of email envelope rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim_insert(self, message: InboundMessage) -> bool:
        """Insert the envelope row as the idempotency claim.

        Returns ``True`` if this call won the claim, ``False`` if the
        ``gmail_message_id`` was already present (unique violation). Commits in its own
        transaction and returns before any publish — see module docstring.
        """
        stmt = insert(email_messages).values(
            gmail_message_id=message.gmail_message_id,
            thread_id=message.thread_id or None,
            sender=message.sender,
            subject=message.subject or None,
            body=message.body or None,
            received_at=message.received_at,
            ingest_status="received",
        )
        try:
            with self._engine.begin() as conn:
                conn.execute(stmt)
        except IntegrityError:
            return False
        return True

    def get_by_gmail_id(self, gmail_message_id: str) -> EmailRecord | None:
        """Fetch an envelope by its idempotency key, or ``None`` if absent."""
        stmt = select(
            email_messages.c.id,
            email_messages.c.gmail_message_id,
            email_messages.c.thread_id,
            email_messages.c.sender,
            email_messages.c.subject,
            email_messages.c.body,
            email_messages.c.received_at,
            email_messages.c.ingest_status,
        ).where(email_messages.c.gmail_message_id == gmail_message_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if row is None:
            return None
        data = dict(row)
        data["id"] = str(data["id"])  # uuid -> str
        return EmailRecord.model_validate(data)

    def set_ingest_status(self, gmail_message_id: str, status: IngestStatus) -> None:
        """Advance the ingest status (its own committed transaction)."""
        stmt = (
            update(email_messages)
            .where(email_messages.c.gmail_message_id == gmail_message_id)
            .values(ingest_status=status)
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def list_stuck_received(self, older_than: datetime) -> list[str]:
        """Return ``gmail_message_id``s stuck in 'received' before ``older_than``.

        Drives the reconciliation sweep (2.4): a committed claim whose publish never
        landed (crash between commit and publish) is recovered here, independent of
        whether Gmail's incremental list still returns the id.
        """
        stmt = (
            select(email_messages.c.gmail_message_id)
            .where(email_messages.c.ingest_status == "received")
            .where(email_messages.c.created_at < older_than)
            .order_by(email_messages.c.created_at)
        )
        with self._engine.connect() as conn:
            return [r[0] for r in conn.execute(stmt)]

    def process_once_extraction(
        self,
        gmail_message_id: str,
        *,
        intent: Intent | None,
        confidence: float | None,
        extracted: dict[str, Any] | None,
        status: ExtractionStatus,
        review_reason: str | None = None,
    ) -> bool:
        """Atomically write the extraction result iff the row is still 'queued'.

        Returns True if THIS delivery won (1 row updated). 0 rows => another delivery
        already processed it => the caller acks and skips. This conditional UPDATE is
        the process-once guard (CLAUDE.md "never process twice").
        """
        stmt = (
            update(email_messages)
            .where(email_messages.c.gmail_message_id == gmail_message_id)
            .where(email_messages.c.ingest_status == "queued")
            .values(
                intent=intent,
                confidence=confidence,
                extracted=extracted,
                ingest_status=status,
                review_reason=review_reason,
            )
        )
        with self._engine.begin() as conn:
            result = conn.execute(stmt)
        return result.rowcount == 1

    def get_attachments(self, email_message_id: str) -> list[AttachmentRecord]:
        """Return attachment rows for an email (used by PDF intake)."""
        stmt = select(
            attachments.c.id,
            attachments.c.storage_path,
            attachments.c.file_type,
            attachments.c.mime_type,
        ).where(attachments.c.email_message_id == email_message_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [
            AttachmentRecord.model_validate({**dict(row), "id": str(row["id"])})
            for row in rows
        ]
