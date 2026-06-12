"""process_once_extraction + get_attachments (integration vs local Supabase)."""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, make_engine
from freight.interfaces.types import InboundMessage

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
PREFIX = "test-ext-"


@pytest.fixture
def engine_repo() -> Iterator[tuple[object, IngestRepository]]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    try:
        yield engine, IngestRepository(engine)
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("delete from email_messages where gmail_message_id like :p"),
                {"p": f"{PREFIX}%"},
            )
        engine.dispose()


def _queued(repo: IngestRepository, gmail_id: str) -> None:
    repo.claim_insert(
        InboundMessage(
            gmail_message_id=gmail_id,
            thread_id="t",
            sender="broker@example.com",
            subject="s",
            body="b",
            received_at=datetime.now(UTC),
        )
    )
    repo.set_ingest_status(gmail_id, "queued")


def test_process_once_success_then_noop(
    engine_repo: tuple[object, IngestRepository],
) -> None:
    _, repo = engine_repo
    gmail_id = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gmail_id)

    won = repo.process_once_extraction(
        gmail_id,
        intent="rate_request",
        confidence=0.9,
        extracted={"origin_state": "IL", "dest_state": "TX"},
        status="processed",
    )
    assert won is True

    record = repo.get_by_gmail_id(gmail_id)
    assert record is not None
    assert record.ingest_status == "processed"

    # Second delivery: row is no longer 'queued' => loses the conditional UPDATE.
    again = repo.process_once_extraction(
        gmail_id,
        intent="rate_request",
        confidence=0.1,
        extracted={},
        status="processed",
    )
    assert again is False


def test_process_once_routes_to_needs_review(
    engine_repo: tuple[object, IngestRepository],
) -> None:
    _, repo = engine_repo
    gmail_id = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gmail_id)

    won = repo.process_once_extraction(
        gmail_id,
        intent=None,
        confidence=0.2,
        extracted=None,
        status="needs_review",
        review_reason="validation_failed",
    )
    assert won is True
    record = repo.get_by_gmail_id(gmail_id)
    assert record is not None
    assert record.ingest_status == "needs_review"


def test_get_attachments(
    engine_repo: tuple[object, IngestRepository],
) -> None:
    engine, repo = engine_repo
    gmail_id = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gmail_id)
    record = repo.get_by_gmail_id(gmail_id)
    assert record is not None

    att_id = str(uuid.uuid4())
    with engine.begin() as conn:  # type: ignore[attr-defined]
        conn.execute(
            text(
                "insert into attachments"
                " (id, email_message_id, storage_path, file_type, mime_type)"
                " values (:id, :eid, :path, 'pdf', 'application/pdf')"
            ),
            {"id": att_id, "eid": record.id, "path": "storage://x/rc.pdf"},
        )

    attachments = repo.get_attachments(record.id)
    assert len(attachments) == 1
    assert attachments[0].file_type == "pdf"
    assert attachments[0].storage_path == "storage://x/rc.pdf"
