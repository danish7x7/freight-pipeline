"""Factories return mock impls by default and real impls when configured."""

from freight.config import Settings
from freight.factories import build_gmail_client, build_llm_client, build_queue
from freight.mocks.gmail import MockGmailClient
from freight.mocks.llm import MockLLMClient
from freight.mocks.queue import InMemoryQueue
from freight.security.llm_guard import GuardedLLMClient


def test_factories_build_mocks_by_default(settings: Settings) -> None:
    assert isinstance(build_gmail_client(settings), MockGmailClient)
    # rate_limit disabled here isolates backend SELECTION from the guard wrapper.
    no_guard = settings.model_copy(update={"rate_limit_enabled": False})
    assert isinstance(build_llm_client(no_guard), MockLLMClient)
    assert isinstance(build_queue(settings), InMemoryQueue)


def test_gmail_backend_constructs() -> None:
    from freight.gmail import GmailApiClient

    settings = Settings(gmail_backend="gmail")
    assert isinstance(build_gmail_client(settings), GmailApiClient)


def test_llm_backend_constructs() -> None:
    from freight.llm import HFLLMClient

    settings = Settings(llm_backend="hf", rate_limit_enabled=False)
    assert isinstance(build_llm_client(settings), HFLLMClient)


def test_llm_client_wrapped_in_guard_by_default() -> None:
    # With rate limiting on (the default), every backend is wrapped in the budget guard.
    client = build_llm_client(Settings(llm_backend="mock"))
    assert isinstance(client, GuardedLLMClient)


def test_qstash_backend_constructs() -> None:
    settings = Settings(queue_backend="qstash")
    from freight.queue import QStashQueue

    assert isinstance(build_queue(settings), QStashQueue)
