"""IngestRepository integration tests (hermetic-ish: cleans up its own rows).

Targets the local supabase DB (where the migrations are applied). Skips when the DB
is unreachable; CI (Phase 8) opts in.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, make_engine
from freight.interfaces.types import InboundMessage

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
PREFIX = "test-ingest-"


def _message(gmail_id: str) -> InboundMessage:
    return InboundMessage(
        gmail_message_id=gmail_id,
        thread_id=f"{gmail_id}-thread",
        sender="broker@example.com",
        subject="Rate request",
        body="dry van CHI->DAL",
        received_at=datetime.now(UTC),
    )


@pytest.fixture
def repo() -> Iterator[IngestRepository]:
    dsn = os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN)
    engine = make_engine(dsn)
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    try:
        yield IngestRepository(engine)
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("delete from email_messages where gmail_message_id like :p"),
                {"p": f"{PREFIX}%"},
            )
        engine.dispose()


def _fresh_id() -> str:
    return f"{PREFIX}{uuid.uuid4()}"


def test_claim_insert_wins_then_duplicate_loses(repo: IngestRepository) -> None:
    gmail_id = _fresh_id()
    assert repo.claim_insert(_message(gmail_id)) is True
    # Same id again => unique violation, caught and reported as a lost claim.
    assert repo.claim_insert(_message(gmail_id)) is False


def test_claim_sets_status_received(repo: IngestRepository) -> None:
    gmail_id = _fresh_id()
    repo.claim_insert(_message(gmail_id))
    record = repo.get_by_gmail_id(gmail_id)
    assert record is not None
    assert record.ingest_status == "received"
    assert record.gmail_message_id == gmail_id


def test_get_unknown_returns_none(repo: IngestRepository) -> None:
    assert repo.get_by_gmail_id(_fresh_id()) is None


def test_status_transition(repo: IngestRepository) -> None:
    gmail_id = _fresh_id()
    repo.claim_insert(_message(gmail_id))
    repo.set_ingest_status(gmail_id, "queued")
    record = repo.get_by_gmail_id(gmail_id)
    assert record is not None
    assert record.ingest_status == "queued"


def test_list_stuck_received_then_cleared(repo: IngestRepository) -> None:
    gmail_id = _fresh_id()
    repo.claim_insert(_message(gmail_id))
    horizon = datetime.now(UTC) + timedelta(days=1)
    assert gmail_id in repo.list_stuck_received(horizon)
    # Once queued, it is no longer stuck.
    repo.set_ingest_status(gmail_id, "queued")
    assert gmail_id not in repo.list_stuck_received(horizon)
