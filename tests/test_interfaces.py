"""Mocks satisfy the interface contracts and behave as documented."""

from freight.interfaces import (
    GmailClient,
    LLMClient,
    OutboundMessage,
    Queue,
    QueueMessage,
)
from freight.mocks.gmail import MockGmailClient
from freight.mocks.llm import MockLLMClient
from freight.mocks.queue import InMemoryQueue


def test_mocks_satisfy_protocols() -> None:
    assert isinstance(MockGmailClient(), GmailClient)
    assert isinstance(MockLLMClient(), LLMClient)
    assert isinstance(InMemoryQueue(), Queue)


def test_gmail_list_and_get_roundtrip() -> None:
    client = MockGmailClient()
    messages = client.list_messages()
    assert messages
    first = messages[0]
    assert client.get_message(first.gmail_message_id) == first


def test_gmail_send_records_and_returns_id() -> None:
    client = MockGmailClient()
    msg = OutboundMessage(to="a@b.c", subject="re: rate", body="ok")
    sent_id = client.send(msg)
    assert sent_id == "mock-sent-0001"
    assert client.sent == [msg]


async def test_llm_complete_returns_result_and_records_prompt() -> None:
    client = MockLLMClient()
    result = await client.complete("classify this")
    assert result.confidence == 0.9
    assert result.data == {"intent": "rate_request"}
    assert client.prompts == ["classify this"]


async def test_queue_publish_records_and_dispatches() -> None:
    seen: list[QueueMessage] = []

    async def handler(message: QueueMessage) -> None:
        seen.append(message)

    queue = InMemoryQueue(handler=handler)
    message = QueueMessage(id="msg-0001", payload={"x": 1})
    await queue.publish(message)

    assert queue.published == [message]
    assert seen == [message]
