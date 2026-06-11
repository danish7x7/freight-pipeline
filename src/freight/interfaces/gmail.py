"""The ``GmailClient`` contract. Implementations are selected by config."""

from typing import Protocol, runtime_checkable

from freight.interfaces.types import InboundMessage, OutboundMessage


@runtime_checkable
class GmailClient(Protocol):
    """Read the inbox and send human-approved replies.

    Least-privilege by design: read and send only. The LLM never calls ``send`` —
    a human approves every outbound message.
    """

    def list_messages(self) -> list[InboundMessage]: ...

    def get_message(self, message_id: str) -> InboundMessage: ...

    def send(self, message: OutboundMessage) -> str:
        """Send ``message`` and return the provider message id."""
        ...
