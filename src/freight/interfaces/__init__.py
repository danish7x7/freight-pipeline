"""Interface contracts and their boundary DTOs.

Build against these; swap implementations by config in ``factories.py``, never by
rewriting call sites.
"""

from freight.interfaces.gmail import GmailClient
from freight.interfaces.llm import LLMClient
from freight.interfaces.queue import Handler, Queue
from freight.interfaces.types import (
    InboundMessage,
    LLMResult,
    OutboundMessage,
    QueueMessage,
)

__all__ = [
    "GmailClient",
    "Handler",
    "InboundMessage",
    "LLMClient",
    "LLMResult",
    "OutboundMessage",
    "Queue",
    "QueueMessage",
]
