"""Shared fixtures. Default config uses the mock/in-memory backends."""

import os

import pytest

from freight.config import Settings

# 8.3a: the app is env-only — config.py has NO dev fallbacks for infra URLs. Supply
# local infra config to the TEST HARNESS via env here (explicit test config, not a
# reintroduced code default). conftest is imported before any test module imports the
# app, so the get_settings() singleton reads these. setdefault => a real env (CI) wins.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/freight"
)


@pytest.fixture
def settings() -> Settings:
    """Settings pinned to the local mock backends."""
    return Settings(
        llm_backend="mock",
        gmail_backend="mock",
        queue_backend="memory",
    )
