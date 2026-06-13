"""Build interface implementations from config.

This is the *single* place that maps a configured backend to a concrete impl.
Call sites depend only on the Protocols in ``freight.interfaces`` — to swap an
implementation, change config (or extend the match here), never the call sites.
"""

from freight.config import Settings, get_settings
from freight.gmail import GmailApiClient
from freight.interfaces import GmailClient, LLMClient, Queue
from freight.llm import HFLLMClient
from freight.mocks.gmail import MockGmailClient
from freight.mocks.llm import MockLLMClient
from freight.mocks.queue import InMemoryQueue
from freight.queue import QStashQueue
from freight.security.llm_guard import GuardedLLMClient
from freight.security.rate_limit import RateLimiter


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
            inner: LLMClient = MockLLMClient()
        case "hf":
            inner = HFLLMClient.from_settings(settings)
    # Wrap every backend in the global LLM-call budget guard (no call-site change).
    # Disabled => no budget; the guard then just delegates.
    if not settings.rate_limit_enabled:
        return inner
    return GuardedLLMClient(
        inner,
        RateLimiter.from_url(settings.redis_url),
        limit=settings.llm_calls_per_minute,
    )


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
