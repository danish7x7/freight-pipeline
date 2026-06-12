"""Consumer end-to-end vs the local DB: extract → process-once write.

Done-when (half 1): a queued email → consumer extracts → row 'processed' with the
validated record. Plus: double-delivery is a no-op; a transient LLM error raises.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, make_engine
from freight.ingestion.consumer import IngestConsumer
from freight.interfaces.types import InboundMessage, LLMResult, QueueMessage
from freight.llm import HFTransientError

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
PREFIX = "test-consumer-"


class _StubLLM:
    def __init__(
        self, result: LLMResult | None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _rate_request_result() -> LLMResult:
    return LLMResult(
        data={"intent": "rate_request", "origin_state": "IL", "dest_state": "TX",
              "equipment": "dry van", "weight_lbs": "42000"},
        raw="{}",
        confidence=0.9,
    )


@pytest.fixture
def repo() -> Iterator[IngestRepository]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
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


def _queued(repo: IngestRepository, gmail_id: str) -> None:
    repo.claim_insert(
        InboundMessage(
            gmail_message_id=gmail_id,
            thread_id="t",
            sender="broker@example.com",
            subject="Rate request",
            body="Dry van CHI->DAL 42,000 lbs",
            received_at=datetime.now(UTC),
        )
    )
    repo.set_ingest_status(gmail_id, "queued")


async def test_queued_email_becomes_processed_with_record(
    repo: IngestRepository,
) -> None:
    gmail_id = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gmail_id)

    await IngestConsumer(repo, _StubLLM(_rate_request_result())).handle(
        QueueMessage(id=gmail_id)
    )

    record = repo.get_by_gmail_id(gmail_id)
    assert record is not None
    assert record.ingest_status == "processed"


async def test_double_delivery_is_a_noop(repo: IngestRepository) -> None:
    gmail_id = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gmail_id)
    consumer = IngestConsumer(repo, _StubLLM(_rate_request_result()))

    await consumer.handle(QueueMessage(id=gmail_id))  # wins, row -> processed
    await consumer.handle(QueueMessage(id=gmail_id))  # 0 rows flipped, no-op (acks)

    record = repo.get_by_gmail_id(gmail_id)
    assert record is not None
    assert record.ingest_status == "processed"


async def test_transient_llm_error_raises(repo: IngestRepository) -> None:
    gmail_id = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gmail_id)
    consumer = IngestConsumer(repo, _StubLLM(None, error=HFTransientError("503")))

    with pytest.raises(HFTransientError):
        await consumer.handle(QueueMessage(id=gmail_id))

    # Row stays 'queued' so a retry can re-process it.
    record = repo.get_by_gmail_id(gmail_id)
    assert record is not None
    assert record.ingest_status == "queued"
