"""Shared fixtures. Default config uses the mock/in-memory backends."""

import pytest

from freight.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings pinned to the local mock backends."""
    return Settings(
        llm_backend="mock",
        gmail_backend="mock",
        queue_backend="memory",
    )
