"""Factories return mock impls by default and refuse unbuilt backends."""

import pytest

from freight.config import Settings
from freight.factories import build_gmail_client, build_llm_client, build_queue
from freight.mocks.gmail import MockGmailClient
from freight.mocks.llm import MockLLMClient
from freight.mocks.queue import InMemoryQueue


def test_factories_build_mocks_by_default(settings: Settings) -> None:
    assert isinstance(build_gmail_client(settings), MockGmailClient)
    assert isinstance(build_llm_client(settings), MockLLMClient)
    assert isinstance(build_queue(settings), InMemoryQueue)


def test_real_gmail_backend_not_yet_implemented() -> None:
    settings = Settings(gmail_backend="gmail")
    with pytest.raises(NotImplementedError):
        build_gmail_client(settings)


def test_real_llm_backend_not_yet_implemented() -> None:
    settings = Settings(llm_backend="hf")
    with pytest.raises(NotImplementedError):
        build_llm_client(settings)


def test_real_queue_backend_not_yet_implemented() -> None:
    settings = Settings(queue_backend="qstash")
    with pytest.raises(NotImplementedError):
        build_queue(settings)
