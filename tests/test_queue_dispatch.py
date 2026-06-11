"""LocalDispatcher retry/DLQ semantics and the QStashQueue slice (no network)."""

import json

import httpx
import pytest

from freight.interfaces.types import QueueMessage
from freight.mocks.dispatcher import LocalDispatcher
from freight.queue import QStashQueue


async def test_dispatcher_delivers_once_on_success() -> None:
    seen: list[QueueMessage] = []

    async def handler(message: QueueMessage) -> None:
        seen.append(message)

    dispatcher = LocalDispatcher(handler, retries=3)
    message = QueueMessage(id="m1")
    await dispatcher.deliver(message)

    assert dispatcher.delivered == [message]
    assert dispatcher.dead_letter == []
    assert dispatcher.attempts == 1
    assert seen == [message]


async def test_dispatcher_dead_letters_after_retries_plus_one() -> None:
    async def handler(message: QueueMessage) -> None:
        raise RuntimeError("poison")

    dispatcher = LocalDispatcher(handler, retries=3)
    message = QueueMessage(id="poison-1")
    await dispatcher.deliver(message)

    assert dispatcher.delivered == []
    assert dispatcher.dead_letter == [message]
    # QStash convention: retries counts attempts AFTER the first => 3 + 1 = 4 total.
    assert dispatcher.attempts == 4


async def test_dispatcher_recovers_on_a_later_attempt() -> None:
    calls = {"n": 0}

    async def flaky(message: QueueMessage) -> None:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")

    dispatcher = LocalDispatcher(flaky, retries=3)
    message = QueueMessage(id="m1")
    await dispatcher.deliver(message)

    assert dispatcher.delivered == [message]
    assert dispatcher.dead_letter == []
    assert dispatcher.attempts == 2


async def test_qstash_publish_issues_expected_request() -> None:
    captured: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"messageId": "x"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    queue = QStashQueue(
        token="tok",
        qstash_url="https://qstash.example",
        destination_url="https://app.example/ingest",
        retries=3,
        client=client,
    )
    message = QueueMessage(id="msg-1", payload={"gmail_message_id": "m1"})
    await queue.publish(message)
    await client.aclose()

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url).startswith("https://qstash.example/v2/publish/")
    assert "https://app.example/ingest" in str(request.url)
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.headers["Upstash-Retries"] == "3"
    assert json.loads(request.content) == message.model_dump()


async def test_qstash_publish_raises_on_error_status() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    queue = QStashQueue(
        token="t",
        qstash_url="https://q.example",
        destination_url="https://a.example/ingest",
        client=client,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await queue.publish(QueueMessage(id="m"))
    await client.aclose()
