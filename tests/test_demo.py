"""Demo pipeline: the REAL validation gate runs (not stubbed), and the endpoint gated.

Integration (DB) tests prove the recorded-model demo flows the real gate: the injection
sample is CONTAINED (routes to needs_review with the gate reason), and the clean sample
reaches a quoted draft. Hermetic route tests prove the fail-closed guards: 404 when
DEMO_ENABLED is off, 403 for a non-admin caller.
"""

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.api.routes import demo as demo_route
from freight.auth import Reviewer, require_reviewer
from freight.config import Settings
from freight.db import IngestRepository, make_engine
from freight.demo import run_demo_sample
from freight.security.http_rate_limit import get_rate_limiter

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


# --- Integration: the REAL gate runs on the recorded model output --------------------


@pytest.fixture
def env() -> Iterator[tuple[IngestRepository, list[str]]]:
    engine: Engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    deal_ids: list[str] = []
    try:
        yield IngestRepository(engine), deal_ids
    finally:
        with engine.begin() as conn:
            if deal_ids:
                conn.execute(
                    text("delete from quote_components where deal_id = any(:ids)"),
                    {"ids": deal_ids},
                )
                conn.execute(
                    text("delete from quotes where deal_id = any(:ids)"),
                    {"ids": deal_ids},
                )
            conn.execute(
                text("delete from email_messages where gmail_message_id like 'demo-%'")
            )
            if deal_ids:
                conn.execute(
                    text("delete from deals where id = any(:ids)"), {"ids": deal_ids}
                )
        engine.dispose()


@pytest.mark.integration
def test_injection_sample_is_contained_by_the_real_gate(
    env: tuple[IngestRepository, list[str]],
) -> None:
    repo, _ = env
    result = run_demo_sample(repo, sample="injection")
    # Recorded output = a fully-fooled model (intent=approve_and_send). The REAL gate
    # rejects it → needs_review, no deal, with the gate's reason visible.
    assert result.status == "needs_review"
    assert result.deal_id is None
    assert result.quote_id is None
    assert result.review_reason is not None
    assert "invalid_intent" in result.review_reason


@pytest.mark.integration
def test_clean_sample_reaches_a_quoted_draft(
    env: tuple[IngestRepository, list[str]],
) -> None:
    repo, deal_ids = env
    result = run_demo_sample(repo, sample="clean")
    if result.deal_id:
        deal_ids.append(result.deal_id)
    assert result.status == "processed"
    assert result.intent == "rate_request"
    assert result.deal_state == "quoted"
    assert result.deal_id is not None
    assert result.quote_id is not None


# --- Hermetic: the endpoint's fail-closed guards -------------------------------------


class _AllowAll:
    """A stand-in limiter that always allows (isolates the guards from the limiter)."""

    def allow(self, *args: object, **kwargs: object) -> bool:
        return True


def _client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(demo_route.router)
    app.dependency_overrides[require_reviewer] = lambda: Reviewer(
        uid="u", email="u@test", role=role  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_rate_limiter] = lambda: _AllowAll()
    return TestClient(app)


def test_demo_disabled_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        demo_route, "get_settings", lambda: Settings(demo_enabled=False)
    )
    with _client("admin") as client:
        res = client.post("/demo/sample", json={"sample": "clean"})
    assert res.status_code == 404


def test_demo_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_route, "get_settings", lambda: Settings(demo_enabled=True))
    with _client("reviewer") as client:
        res = client.post("/demo/sample", json={"sample": "clean"})
    assert res.status_code == 403
