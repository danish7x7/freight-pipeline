"""Ingestion consumer + /ingest route (no DB needed — fakes the repo)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from freight.api.main import app
from freight.api.routes.ingest import get_consumer
from freight.db.repository import EmailRecord
from freight.ingestion.consumer import IngestConsumer, IngestError
from freight.interfaces.types import QueueMessage
from freight.mocks.dispatcher import LocalDispatcher


class _FakeRepo:
    """Returns a preset record (or None) regardless of id."""

    def __init__(self, record: EmailRecord | None) -> None:
        self._record = record

    def get_by_gmail_id(self, gmail_message_id: str) -> EmailRecord | None:
        return self._record


def _record(sender: str = "broker@example.com") -> EmailRecord:
    return EmailRecord(
        gmail_message_id="m1",
        thread_id=None,
        sender=sender,
        subject=None,
        body=None,
        received_at=datetime.now(UTC),
        ingest_status="queued",
    )


async def test_handle_acks_valid_envelope() -> None:
    consumer = IngestConsumer(_FakeRepo(_record()))
    await consumer.handle(QueueMessage(id="m1"))  # no raise == ack


async def test_handle_raises_when_no_row() -> None:
    consumer = IngestConsumer(_FakeRepo(None))
    with pytest.raises(IngestError):
        await consumer.handle(QueueMessage(id="missing"))


async def test_handle_raises_on_empty_sender() -> None:
    consumer = IngestConsumer(_FakeRepo(_record(sender="")))
    with pytest.raises(IngestError):
        await consumer.handle(QueueMessage(id="m1"))


async def test_poison_message_dead_letters_after_retries() -> None:
    consumer = IngestConsumer(_FakeRepo(None))  # no row => handle always raises
    dispatcher = LocalDispatcher(consumer.handle, retries=2)
    message = QueueMessage(id="poison")

    await dispatcher.deliver(message)

    assert dispatcher.delivered == []
    assert dispatcher.dead_letter == [message]
    assert dispatcher.attempts == 3  # retries + 1


def _ok_consumer() -> IngestConsumer:
    return IngestConsumer(_FakeRepo(_record()))


def _poison_consumer() -> IngestConsumer:
    return IngestConsumer(_FakeRepo(None))


def test_ingest_route_returns_2xx_on_success() -> None:
    app.dependency_overrides[get_consumer] = _ok_consumer
    try:
        response = TestClient(app).post("/ingest", json={"id": "m1", "payload": {}})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ingest_route_returns_5xx_on_poison() -> None:
    app.dependency_overrides[get_consumer] = _poison_consumer
    try:
        response = TestClient(app).post(
            "/ingest", json={"id": "missing", "payload": {}}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500
