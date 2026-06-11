"""In-memory ``GmailClient`` mock serving the synthetic corpus."""

from freight.interfaces.types import InboundMessage, OutboundMessage
from freight.synthetic import generate_dataset


def _default_inbox() -> list[InboundMessage]:
    """The labeled synthetic corpus, as raw inbound messages."""
    return [sample.message for sample in generate_dataset()]


class MockGmailClient:
    """Serves a fixed inbox and records sent messages in memory."""

    def __init__(self, inbox: list[InboundMessage] | None = None) -> None:
        self._inbox: list[InboundMessage] = (
            inbox if inbox is not None else _default_inbox()
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
