"""Build interface implementations from config.

This is the *single* place that maps a configured backend to a concrete impl.
Call sites depend only on the Protocols in ``freight.interfaces`` — to swap an
implementation, change config (or extend the match here), never the call sites.
"""

from freight.config import Settings, get_settings
from freight.gmail import GmailApiClient
from freight.interfaces import GmailClient, LLMClient, Queue
from freight.mocks.gmail import MockGmailClient
from freight.mocks.llm import MockLLMClient
from freight.mocks.queue import InMemoryQueue
from freight.queue import QStashQueue


def build_gmail_client(settings: Settings | None = None) -> GmailClient:
    settings = settings or get_settings()
    match settings.gmail_backend:
        case "mock":
            return MockGmailClient()
        case "gmail":
            return GmailApiClient.from_settings(settings)


def build_llm_client(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    match settings.llm_backend:
        case "mock":
            return MockLLMClient()
        case "hf":
            raise NotImplementedError("HF LLMClient lands in Phase 3")


def build_queue(settings: Settings | None = None) -> Queue:
    settings = settings or get_settings()
    match settings.queue_backend:
        case "memory":
            return InMemoryQueue()
        case "qstash":
            return QStashQueue(
                token=settings.qstash_token,
                qstash_url=settings.qstash_url,
                destination_url=settings.qstash_destination_url,
            )
