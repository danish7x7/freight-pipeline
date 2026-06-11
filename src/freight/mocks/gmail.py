"""In-memory ``GmailClient`` mock serving synthetic messages."""

from datetime import UTC, datetime

from freight.interfaces.types import InboundMessage, OutboundMessage


def _sample_messages() -> list[InboundMessage]:
    return [
        InboundMessage(
            gmail_message_id="msg-0001",
            thread_id="thread-0001",
            sender="broker@example.com",
            subject="Rate request: Chicago, IL -> Dallas, TX",
            body="Need a dry van rate for 42,000 lbs, pickup Monday.",
            received_at=datetime(2026, 6, 10, 14, 30, tzinfo=UTC),
        ),
    ]


class MockGmailClient:
    """Serves a fixed inbox and records sent messages in memory."""

    def __init__(self, inbox: list[InboundMessage] | None = None) -> None:
        self._inbox: list[InboundMessage] = (
            inbox if inbox is not None else _sample_messages()
        )
        self.sent: list[OutboundMessage] = []

    def list_messages(self) -> list[InboundMessage]:
        return list(self._inbox)

    def get_message(self, message_id: str) -> InboundMessage:
        for message in self._inbox:
            if message.gmail_message_id == message_id:
                return message
        raise KeyError(f"no message with id {message_id!r}")

    def send(self, message: OutboundMessage) -> str:
        self.sent.append(message)
        return f"mock-sent-{len(self.sent):04d}"
