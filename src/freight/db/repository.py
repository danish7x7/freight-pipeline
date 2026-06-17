"""email_messages repository (SQLAlchemy Core, sync).

Transaction boundaries are explicit and load-bearing for idempotency:
``claim_insert`` commits the claim row in its OWN transaction and returns BEFORE any
publish happens. That ordering guarantees the consumer can never fetch a thin-payload
id whose row isn't yet visible — the only remaining failure window is a crash between
the committed claim and the publish, which leaves a ``received`` row that the
reconciliation sweep (``list_stuck_received``) re-enqueues.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from freight.interfaces.types import InboundMessage

IngestStatus = Literal["received", "queued", "processed", "failed", "needs_review"]
Intent = Literal["rate_request", "negotiation", "rc", "contract", "other"]
# Terminal extraction outcomes the consumer writes (both return 2xx — see DECISIONS).
ExtractionStatus = Literal["processed", "needs_review"]
AttachmentFileType = Literal["pdf", "image", "other"]
CarrierStatus = Literal["active", "blocked", "unknown"]
RateSource = Literal["contracted", "computed"]
UserRole = Literal["reviewer", "admin"]
SendStatus = Literal["claimed", "sent", "failed"]

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
    Column("deal_id", UUID(as_uuid=False)),
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

_USER_ROLE = SAEnum("reviewer", "admin", name="user_role", create_type=False)

users = Table(
    "users",
    _metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("email", String, nullable=False),
    Column("role", _USER_ROLE, nullable=False),
)

_CARRIER_STATUS = SAEnum(
    "active", "blocked", "unknown", name="carrier_status", create_type=False,
)

carriers = Table(
    "carriers",
    _metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("mc_number", String, nullable=False),
    Column("name", String, nullable=False),
    Column("status", _CARRIER_STATUS, nullable=False),
)

_EQUIPMENT_TYPE = SAEnum(
    "dry_van", "reefer", "flatbed", "step_deck", "power_only", "container", "other",
    name="equipment_type", create_type=False,
)
_RATE_SOURCE = SAEnum(
    "contracted", "computed", name="rate_source", create_type=False,
)

rates = Table(
    "rates",
    _metadata,
    Column(
        "id",
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("origin_city", String, nullable=False),
    Column("origin_state", String, nullable=False),
    Column("dest_city", String, nullable=False),
    Column("dest_state", String, nullable=False),
    Column("equipment", _EQUIPMENT_TYPE, nullable=False),
    Column("carrier_id", UUID(as_uuid=False)),
    Column("source", _RATE_SOURCE, nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("currency", String, nullable=False),
    Column("effective_from", DateTime(timezone=True), nullable=False),
    Column("created_by", UUID(as_uuid=False)),
    Column("created_at", DateTime(timezone=True)),
)

quotes = Table(
    "quotes",
    _metadata,
    Column(
        "id",
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("deal_id", UUID(as_uuid=False), nullable=False),
    Column("rate_id", UUID(as_uuid=False), nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("currency", String, nullable=False),
    Column("is_computed", Boolean, nullable=False),
)

_DEAL_STATE = SAEnum(
    "new_enquiry", "quoted", "negotiating", "rc_received", "contract_signed",
    "scheduled", "rejected", "on_hold", name="deal_state", create_type=False,
)

deals = Table(
    "deals",
    _metadata,
    Column(
        "id",
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("state", _DEAL_STATE, nullable=False),
    Column("assigned_reviewer", UUID(as_uuid=False)),
    Column("carrier_id", UUID(as_uuid=False)),
    Column("origin_city", String),
    Column("origin_state", String),
    Column("dest_city", String),
    Column("dest_state", String),
    Column("equipment", _EQUIPMENT_TYPE),
    Column("held_from", _DEAL_STATE),
    Column("accepted_quote_id", UUID(as_uuid=False)),
    Column("updated_at", DateTime(timezone=True)),
)

_SEND_STATUS = SAEnum(
    "claimed", "sent", "failed", name="send_status", create_type=False,
)

sends = Table(
    "sends",
    _metadata,
    Column(
        "id",
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("quote_id", UUID(as_uuid=False), nullable=False, unique=True),
    Column("deal_id", UUID(as_uuid=False), nullable=False),
    Column("to_email", String, nullable=False),
    Column("subject", String, nullable=False),
    Column("body", Text, nullable=False),
    Column("status", _SEND_STATUS, nullable=False),
    Column("gmail_message_id", String),
    Column("created_by", UUID(as_uuid=False)),
    Column("sent_at", DateTime(timezone=True)),
)

audit_log = Table(
    "audit_log",
    _metadata,
    Column(
        "id",
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
    Column("actor", UUID(as_uuid=False)),
    Column("actor_email", String),
    Column("action", String, nullable=False),
    Column("entity_type", String, nullable=False),
    Column("entity_id", UUID(as_uuid=False)),
    Column("detail", JSONB),
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


class UserRecord(BaseModel):
    """A persisted app user (for RBAC)."""

    id: str
    email: str
    role: UserRole


class CarrierRecord(BaseModel):
    """A persisted carrier row (for the MC eligibility gate)."""

    id: str
    mc_number: str
    status: CarrierStatus


@dataclass(frozen=True)
class RateKey:
    """The lane key a rate is looked up by."""

    origin_city: str
    origin_state: str
    dest_city: str
    dest_state: str
    equipment: str


class RateRecord(BaseModel):
    """A persisted rate version (the row a quote pins)."""

    id: str
    amount_cents: int
    currency: str
    source: RateSource
    carrier_id: str | None
    effective_from: datetime


class SendClaim(BaseModel):
    """An outbound-send claim (the idempotency row)."""

    id: str
    quote_id: str
    deal_id: str
    to_email: str
    subject: str
    body: str
    status: SendStatus
    gmail_message_id: str | None


class DealRecord(BaseModel):
    """A deal row (for send/reject authz)."""

    id: str
    state: str
    assigned_reviewer: str | None


class QuoteRecord(BaseModel):
    """A quote row (for send authz + the reply amount)."""

    id: str
    deal_id: str
    amount_cents: int
    currency: str


class LaneRate(BaseModel):
    """The current contracted rate for a lane key (for the surcharge job)."""

    origin_city: str
    origin_state: str
    dest_city: str
    dest_state: str
    equipment: str
    carrier_id: str | None
    amount_cents: int
    currency: str


def make_engine(database_url: str) -> Engine:
    """Build a sync Engine, normalizing the URL to the psycopg (v3) driver.

    ``prepare_threshold=None`` DISABLES psycopg3 server-side prepared statements. The
    Supabase transaction pooler (pgbouncer, transaction mode) rotates the backend
    connection between statements, so a prepared statement made on one statement is
    gone before the next runs and psycopg3's default raises ``InvalidSqlStatementName``.
    This is the single engine factory for the whole app, so disabling it here covers
    EVERY statement (claim_insert, finalize, send-claim, surcharge, readiness), not one.
    Set via ``connect_args`` (a psycopg3 connect kwarg), NOT a URL param.
    """
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
    )


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

    def count_ingest_backlog(self) -> int:
        """Inbound emails not yet terminal (received/queued) — the backlog gauge."""
        stmt = select(func.count()).where(
            email_messages.c.ingest_status.in_(("received", "queued"))
        )
        with self._engine.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def count_sends_claimed(self) -> int:
        """Sends claimed but not yet sent (the at-least-once stuck window) — gauge."""
        stmt = select(func.count()).where(sends.c.status == "claimed")
        with self._engine.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def begin(self) -> AbstractContextManager[Connection]:
        """Open a transaction; the caller owns commit/rollback (the finalize tx)."""
        return self._engine.begin()

    def flip_if_queued(
        self,
        conn: Connection,
        *,
        gmail_message_id: str,
        intent: Intent | None,
        confidence: float | None,
        extracted: dict[str, Any] | None,
        status: ExtractionStatus,
        review_reason: str | None = None,
    ) -> bool:
        """Conditional UPDATE on the caller's tx: write the result iff still 'queued'.

        Returns True if THIS delivery won (1 row). 0 rows => already processed => skip.
        This is the process-once guard (CLAUDE.md "never process twice").
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
        return conn.execute(stmt).rowcount == 1

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
        """Standalone process-once write in its own transaction (delegates flip)."""
        with self._engine.begin() as conn:
            return self.flip_if_queued(
                conn,
                gmail_message_id=gmail_message_id,
                intent=intent,
                confidence=confidence,
                extracted=extracted,
                status=status,
                review_reason=review_reason,
            )

    def create_deal(
        self, conn: Connection, *, state: str, extracted: dict[str, Any]
    ) -> str:
        """Create a deal from extracted route fields; return its id."""
        stmt = (
            insert(deals)
            .values(
                state=state,
                origin_city=extracted.get("origin_city"),
                origin_state=extracted.get("origin_state"),
                dest_city=extracted.get("dest_city"),
                dest_state=extracted.get("dest_state"),
                equipment=extracted.get("equipment"),
            )
            .returning(deals.c.id)
        )
        return str(conn.execute(stmt).scalar_one())

    def link_email(
        self, conn: Connection, *, gmail_message_id: str, deal_id: str
    ) -> None:
        """Link an email to its deal (sets email_messages.deal_id)."""
        conn.execute(
            update(email_messages)
            .where(email_messages.c.gmail_message_id == gmail_message_id)
            .values(deal_id=deal_id)
        )

    def advance_deal(
        self,
        conn: Connection,
        *,
        deal_id: str,
        state: str,
        held_from: str | None = None,
    ) -> None:
        """Set a deal's state (and held_from when moving to on_hold)."""
        conn.execute(
            update(deals)
            .where(deals.c.id == deal_id)
            .values(state=state, held_from=held_from, updated_at=func.now())
        )

    def current_contracted_rate(
        self, key: RateKey, carrier_id: str | None = None
    ) -> RateRecord | None:
        """Return the current contracted rate for a lane (Model A), or None.

        Filters source='contracted' and effective_from <= now(). Carrier precedence:
        a carrier-specific row wins over the lane-generic (carrier_id IS NULL) row; ties
        broken by effective_from DESC, created_at DESC. Computed rows are excluded.
        """
        conditions = [
            rates.c.origin_city == key.origin_city,
            rates.c.origin_state == key.origin_state,
            rates.c.dest_city == key.dest_city,
            rates.c.dest_state == key.dest_state,
            rates.c.equipment == key.equipment,
            rates.c.source == "contracted",
            rates.c.effective_from <= func.now(),
        ]
        order = [rates.c.effective_from.desc(), rates.c.created_at.desc()]
        if carrier_id is not None:
            conditions.append(
                or_(rates.c.carrier_id == carrier_id, rates.c.carrier_id.is_(None))
            )
            # carrier-specific (carrier_id IS NOT NULL) sorts before lane-generic.
            order.insert(0, rates.c.carrier_id.is_(None).asc())
        else:
            conditions.append(rates.c.carrier_id.is_(None))

        stmt = (
            select(
                rates.c.id,
                rates.c.amount_cents,
                rates.c.currency,
                rates.c.source,
                rates.c.carrier_id,
                rates.c.effective_from,
            )
            .where(and_(*conditions))
            .order_by(*order)
            .limit(1)
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return RateRecord.model_validate(dict(row)) if row is not None else None

    def insert_rate_version(
        self,
        conn: Connection,
        *,
        key: RateKey,
        source: RateSource,
        amount_cents: int,
        currency: str,
        carrier_id: str | None = None,
    ) -> str:
        """Append a rate version on the caller's transaction; return its id.

        REQUIRES a Connection — the caller owns the transaction and the commit. rates is
        append-only (effective_from defaults to now()); this is always an INSERT.
        """
        stmt = (
            insert(rates)
            .values(
                origin_city=key.origin_city,
                origin_state=key.origin_state,
                dest_city=key.dest_city,
                dest_state=key.dest_state,
                equipment=key.equipment,
                carrier_id=carrier_id,
                source=source,
                amount_cents=amount_cents,
                currency=currency,
            )
            .returning(rates.c.id)
        )
        return str(conn.execute(stmt).scalar_one())

    def insert_quote(
        self,
        conn: Connection,
        *,
        deal_id: str,
        rate_id: str,
        amount_cents: int,
        currency: str,
        is_computed: bool,
    ) -> str:
        """Insert a quote pinning ``rate_id`` on the caller's tx; return its id.

        ``amount_cents``/``currency`` are the snapshot copied from the pinned rate.
        """
        stmt = (
            insert(quotes)
            .values(
                deal_id=deal_id,
                rate_id=rate_id,
                amount_cents=amount_cents,
                currency=currency,
                is_computed=is_computed,
            )
            .returning(quotes.c.id)
        )
        return str(conn.execute(stmt).scalar_one())

    def list_contracted_lanes(self) -> list[LaneRate]:
        """Current contracted rate per lane key (DISTINCT ON key, newest version)."""
        key_cols = [
            rates.c.origin_state,
            rates.c.origin_city,
            rates.c.dest_state,
            rates.c.dest_city,
            rates.c.equipment,
            rates.c.carrier_id,
        ]
        stmt = (
            select(
                rates.c.origin_city,
                rates.c.origin_state,
                rates.c.dest_city,
                rates.c.dest_state,
                rates.c.equipment,
                rates.c.carrier_id,
                rates.c.amount_cents,
                rates.c.currency,
            )
            .where(rates.c.source == "contracted")
            .where(rates.c.effective_from <= func.now())
            .distinct(*key_cols)
            .order_by(
                *key_cols,
                rates.c.effective_from.desc(),
                rates.c.created_at.desc(),
            )
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [LaneRate.model_validate(dict(row)) for row in rows]

    def get_user(self, uid: str) -> UserRecord | None:
        """Look up an app user by id (for RBAC: reviewer vs admin)."""
        stmt = select(users.c.id, users.c.email, users.c.role).where(
            users.c.id == uid
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return UserRecord.model_validate(dict(row)) if row is not None else None

    def get_deal_email(self, deal_id: str) -> EmailRecord | None:
        """The inbound email a deal replies to (earliest linked message)."""
        stmt = (
            select(
                email_messages.c.id,
                email_messages.c.gmail_message_id,
                email_messages.c.thread_id,
                email_messages.c.sender,
                email_messages.c.subject,
                email_messages.c.body,
                email_messages.c.received_at,
                email_messages.c.ingest_status,
            )
            .where(email_messages.c.deal_id == deal_id)
            .order_by(email_messages.c.received_at)
            .limit(1)
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if row is None:
            return None
        data = dict(row)
        data["id"] = str(data["id"])
        return EmailRecord.model_validate(data)

    def get_deal(self, deal_id: str) -> DealRecord | None:
        """Fetch a deal (for send/reject authz)."""
        stmt = select(
            deals.c.id, deals.c.state, deals.c.assigned_reviewer
        ).where(deals.c.id == deal_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return DealRecord.model_validate(dict(row)) if row is not None else None

    def get_quote(self, quote_id: str) -> QuoteRecord | None:
        """Fetch a quote (for send authz + the reply amount)."""
        stmt = select(
            quotes.c.id, quotes.c.deal_id, quotes.c.amount_cents, quotes.c.currency
        ).where(quotes.c.id == quote_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return QuoteRecord.model_validate(dict(row)) if row is not None else None

    def claim_send(
        self,
        conn: Connection,
        *,
        quote_id: str,
        deal_id: str,
        to_email: str,
        subject: str,
        body: str,
        created_by: str | None,
    ) -> SendClaim:
        """Claim the send for a quote (UNIQUE(quote_id)); idempotent.

        Returns the claim — newly inserted, or the EXISTING row on conflict (so the
        caller can recover a 'claimed'-but-unsent row or reject an already-'sent' one).
        """
        cols = (
            sends.c.id,
            sends.c.quote_id,
            sends.c.deal_id,
            sends.c.to_email,
            sends.c.subject,
            sends.c.body,
            sends.c.status,
            sends.c.gmail_message_id,
        )
        stmt = (
            pg_insert(sends)
            .values(
                quote_id=quote_id,
                deal_id=deal_id,
                to_email=to_email,
                subject=subject,
                body=body,
                status="claimed",
                created_by=created_by,
            )
            .on_conflict_do_nothing(index_elements=[sends.c.quote_id])
            .returning(*cols)
        )
        row = conn.execute(stmt).mappings().first()
        if row is None:  # conflict → the send was already claimed
            row = conn.execute(
                select(*cols).where(sends.c.quote_id == quote_id)
            ).mappings().first()
        assert row is not None
        return SendClaim.model_validate(dict(row))

    def mark_sent(
        self, conn: Connection, *, send_id: str, gmail_message_id: str
    ) -> None:
        """Record a successful Gmail send on the claim row."""
        conn.execute(
            update(sends)
            .where(sends.c.id == send_id)
            .values(
                status="sent", gmail_message_id=gmail_message_id, sent_at=func.now()
            )
        )

    def insert_audit(
        self,
        conn: Connection,
        *,
        actor: str | None,
        actor_email: str | None,
        action: str,
        entity_type: str,
        entity_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit_log row on the caller's transaction (atomic with state)."""
        conn.execute(
            insert(audit_log).values(
                actor=actor,
                actor_email=actor_email,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                detail=detail if detail is not None else {},
            )
        )

    def get_carrier_by_mc(self, mc_number: str) -> CarrierRecord | None:
        """Look up a carrier by MC number (for the eligibility gate)."""
        stmt = select(
            carriers.c.id, carriers.c.mc_number, carriers.c.status
        ).where(carriers.c.mc_number == mc_number)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if row is None:
            return None
        return CarrierRecord.model_validate({**dict(row), "id": str(row["id"])})

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
