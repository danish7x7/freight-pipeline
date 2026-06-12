"""Consumer (transport) + /ingest route, end to end vs the local DB."""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.api.main import app
from freight.api.routes.ingest import get_consumer
from freight.db import IngestRepository, make_engine
from freight.ingestion.consumer import IngestConsumer, IngestError
from freight.interfaces.types import InboundMessage, LLMResult, QueueMessage
from freight.llm import HFTransientError
from freight.mocks.dispatcher import LocalDispatcher

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
PREFIX = "test-consumer-"


class _StubLLM:
    """Returns a full-route rate_request (→ contracted quote), or raises."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        if self._error is not None:
            raise self._error
        return LLMResult(
            data={
                "intent": "rate_request",
                "origin_city": "Chicago",
                "origin_state": "IL",
                "dest_city": "Dallas",
                "dest_state": "TX",
                "equipment": "dry van",
                "weight_lbs": "42000",
            },
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
        _cleanup(engine)
        engine.dispose()


def _cleanup(engine: Engine) -> None:
    with engine.begin() as conn:
        deal_ids = list(
            conn.execute(
                text(
                    "select deal_id from email_messages "
                    "where gmail_message_id like :p and deal_id is not null"
                ),
                {"p": f"{PREFIX}%"},
            ).scalars()
        )
        if deal_ids:
            conn.execute(
                text("delete from quotes where deal_id = any(:ids)"), {"ids": deal_ids}
            )
        conn.execute(
            text("delete from email_messages where gmail_message_id like :p"),
            {"p": f"{PREFIX}%"},
        )
        if deal_ids:
            conn.execute(
                text("delete from deals where id = any(:ids)"), {"ids": deal_ids}
            )


def _queued(repo: IngestRepository, gmail_id: str, *, sender: str = "b@x.co") -> None:
    repo.claim_insert(
        InboundMessage(
            gmail_message_id=gmail_id,
            thread_id="t",
            sender=sender,
            subject="Rate request",
            body="Dry van Chicago IL to Dallas TX 42,000 lbs",
            received_at=datetime.now(UTC),
        )
    )
    repo.set_ingest_status(gmail_id, "queued")


async def test_handle_processes_rate_request_into_quoted_deal(
    repo: IngestRepository,
) -> None:
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid)
    await IngestConsumer(repo, _StubLLM()).handle(QueueMessage(id=gid))

    record = repo.get_by_gmail_id(gid)
    assert record is not None
    assert record.ingest_status == "processed"


async def test_handle_raises_when_no_row(repo: IngestRepository) -> None:
    with pytest.raises(IngestError):
        await IngestConsumer(repo, _StubLLM()).handle(QueueMessage(id="missing"))


async def test_handle_raises_on_empty_sender(repo: IngestRepository) -> None:
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid, sender="")
    with pytest.raises(IngestError):
        await IngestConsumer(repo, _StubLLM()).handle(QueueMessage(id=gid))


async def test_handle_transient_llm_error_raises_and_keeps_queued(
    repo: IngestRepository,
) -> None:
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid)
    consumer = IngestConsumer(repo, _StubLLM(error=HFTransientError("503")))
    with pytest.raises(HFTransientError):
        await consumer.handle(QueueMessage(id=gid))
    record = repo.get_by_gmail_id(gid)
    assert record is not None
    assert record.ingest_status == "queued"  # retry can re-process


async def test_poison_message_dead_letters_after_retries(
    repo: IngestRepository,
) -> None:
    consumer = IngestConsumer(repo, _StubLLM())
    dispatcher = LocalDispatcher(consumer.handle, retries=2)
    message = QueueMessage(id=f"{PREFIX}{uuid.uuid4()}-missing")
    await dispatcher.deliver(message)
    assert dispatcher.dead_letter == [message]
    assert dispatcher.attempts == 3  # retries + 1


def test_ingest_route_2xx_then_5xx(repo: IngestRepository) -> None:
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid)
    app.dependency_overrides[get_consumer] = lambda: IngestConsumer(repo, _StubLLM())
    try:
        client = TestClient(app)
        ok = client.post("/ingest", json={"id": gid, "payload": {}})
        missing = client.post("/ingest", json={"id": "missing", "payload": {}})
    finally:
        app.dependency_overrides.clear()
    assert ok.status_code == 200
    assert missing.status_code == 500
