"""Ingestion consumer + /ingest route (no DB needed — fakes the repo + LLM)."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from freight.api.main import app
from freight.api.routes.ingest import get_consumer
from freight.db.repository import (
    AttachmentRecord,
    EmailRecord,
    ExtractionStatus,
    Intent,
)
from freight.ingestion.consumer import IngestConsumer, IngestError
from freight.interfaces.types import LLMResult, QueueMessage
from freight.mocks.dispatcher import LocalDispatcher


class _FakeRepo:
    """Returns a preset record (or None); records process-once writes."""

    def __init__(self, record: EmailRecord | None, *, won: bool = True) -> None:
        self._record = record
        self._won = won
        self.writes: list[ExtractionStatus] = []

    def get_by_gmail_id(self, gmail_message_id: str) -> EmailRecord | None:
        return self._record

    def get_attachments(self, email_message_id: str) -> list[AttachmentRecord]:
        return []

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
        self.writes.append(status)
        return self._won


class _StubLLM:
    """Returns a fixed structured extraction."""

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        return LLMResult(
            data={"intent": "rate_request", "origin_state": "IL", "dest_state": "TX",
                  "equipment": "dry van", "weight_lbs": "42000"},
            raw="{}",
            confidence=0.9,
        )


def _record(sender: str = "broker@example.com") -> EmailRecord:
    return EmailRecord(
        id="row-1",
        gmail_message_id="m1",
        thread_id=None,
        sender=sender,
        subject="Rate request",
        body="Dry van CHI->DAL 42k",
        received_at=datetime.now(UTC),
        ingest_status="queued",
    )


async def test_handle_extracts_and_writes_processed() -> None:
    repo = _FakeRepo(_record())
    await IngestConsumer(repo, _StubLLM()).handle(QueueMessage(id="m1"))
    assert repo.writes == ["processed"]


async def test_handle_raises_when_no_row() -> None:
    consumer = IngestConsumer(_FakeRepo(None), _StubLLM())
    with pytest.raises(IngestError):
        await consumer.handle(QueueMessage(id="missing"))


async def test_handle_raises_on_empty_sender() -> None:
    consumer = IngestConsumer(_FakeRepo(_record(sender="")), _StubLLM())
    with pytest.raises(IngestError):
        await consumer.handle(QueueMessage(id="m1"))


async def test_poison_message_dead_letters_after_retries() -> None:
    consumer = IngestConsumer(_FakeRepo(None), _StubLLM())  # no row => always raises
    dispatcher = LocalDispatcher(consumer.handle, retries=2)
    message = QueueMessage(id="poison")

    await dispatcher.deliver(message)

    assert dispatcher.delivered == []
    assert dispatcher.dead_letter == [message]
    assert dispatcher.attempts == 3  # retries + 1


def _ok_consumer() -> IngestConsumer:
    return IngestConsumer(_FakeRepo(_record()), _StubLLM())


def _poison_consumer() -> IngestConsumer:
    return IngestConsumer(_FakeRepo(None), _StubLLM())


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
