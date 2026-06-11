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


def test_gmail_backend_constructs() -> None:
    from freight.gmail import GmailApiClient

    settings = Settings(gmail_backend="gmail")
    assert isinstance(build_gmail_client(settings), GmailApiClient)


def test_real_llm_backend_not_yet_implemented() -> None:
    settings = Settings(llm_backend="hf")
    with pytest.raises(NotImplementedError):
        build_llm_client(settings)


def test_qstash_backend_constructs() -> None:
    settings = Settings(queue_backend="qstash")
    from freight.queue import QStashQueue

    assert isinstance(build_queue(settings), QStashQueue)
