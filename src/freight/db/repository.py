"""email_messages repository (SQLAlchemy Core, sync).

Transaction boundaries are explicit and load-bearing for idempotency:
``claim_insert`` commits the claim row in its OWN transaction and returns BEFORE any
publish happens. That ordering guarantees the consumer can never fetch a thin-payload
id whose row isn't yet visible — the only remaining failure window is a crash between
the committed claim and the publish, which leaves a ``received`` row that the
reconciliation sweep (``list_stuck_received``) re-enqueues.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
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
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from freight.interfaces.types import InboundMessage

IngestStatus = Literal["received", "queued", "processed", "failed"]

_metadata = MetaData()

# Core mapping of the columns this repository touches. NOT a migration — the schema is
# owned by supabase/migrations. Columns the repo doesn't use are intentionally omitted.
email_messages = Table(
    "email_messages",
    _metadata,
    Column(
        "id", String, primary_key=True, server_default=text("gen_random_uuid()")
    ),
    Column("gmail_message_id", String, nullable=False, unique=True),
    Column("thread_id", String),
    Column("sender", String, nullable=False),
    Column("subject", Text),
    Column("body", Text),
    Column("received_at", DateTime(timezone=True), nullable=False),
    # Bound to the existing Postgres enum (owned by the migration, not created here).
    Column(
        "ingest_status",
        SAEnum(
            "received",
            "queued",
            "processed",
            "failed",
            name="email_ingest_status",
            create_type=False,
        ),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True)),
)


class EmailRecord(BaseModel):
    """A persisted email envelope as the ingestion path sees it."""

    gmail_message_id: str
    thread_id: str | None
    sender: str
    subject: str | None
    body: str | None
    received_at: datetime
    ingest_status: IngestStatus


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
        return EmailRecord.model_validate(dict(row))

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
