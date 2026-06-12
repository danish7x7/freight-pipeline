"""Deal reject action: state → rejected + audit, authz, terminal guard (integration)."""

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.api.main import app
from freight.api.routes.review import get_review_deps
from freight.auth import Reviewer
from freight.auth.deps import require_reviewer
from freight.db import IngestRepository, make_engine
from freight.mocks.gmail import MockGmailClient
from freight.sending import SendError, reject_deal

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
REVIEWER1 = Reviewer(
    "a2222222-2222-2222-2222-222222222222", "r1@freight.local", "reviewer"
)
REVIEWER2 = Reviewer(
    "a3333333-3333-3333-3333-333333333333", "r2@freight.local", "reviewer"
)


@pytest.fixture
def env() -> Iterator[tuple[Engine, IngestRepository, list[str]]]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    deal_ids: list[str] = []
    try:
        yield engine, IngestRepository(engine), deal_ids
    finally:
        with engine.begin() as conn:
            if deal_ids:
                conn.execute(
                    text("delete from deals where id = any(:d)"), {"d": deal_ids}
                )
        engine.dispose()


def _make_deal(
    engine: Engine, deal_ids: list[str], *, reviewer_uid: str, state: str = "quoted"
) -> str:
    deal_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into deals (id, state, assigned_reviewer) values (:d, :s, :r)"
            ),
            {"d": deal_id, "s": state, "r": reviewer_uid},
        )
    deal_ids.append(deal_id)
    return deal_id


def test_reject_sets_state_and_audits(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, deal_ids = env
    deal_id = _make_deal(engine, deal_ids, reviewer_uid=REVIEWER1.uid)

    reject_deal(repo, reviewer=REVIEWER1, deal_id=deal_id)

    deal = repo.get_deal(deal_id)
    assert deal is not None
    assert deal.state == "rejected"
    with engine.connect() as conn:
        actions = set(
            conn.execute(
                text("select action from audit_log where entity_id = :d"),
                {"d": deal_id},
            ).scalars()
        )
    assert "deal.rejected" in actions


def test_reject_non_owner_403(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, deal_ids = env
    deal_id = _make_deal(engine, deal_ids, reviewer_uid=REVIEWER1.uid)
    with pytest.raises(SendError) as exc:
        reject_deal(repo, reviewer=REVIEWER2, deal_id=deal_id)
    assert exc.value.status_code == 403


def test_reject_terminal_deal_409(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, deal_ids = env
    deal_id = _make_deal(engine, deal_ids, reviewer_uid=REVIEWER1.uid, state="rejected")
    with pytest.raises(SendError) as exc:
        reject_deal(repo, reviewer=REVIEWER1, deal_id=deal_id)
    assert exc.value.status_code == 409


def test_review_reject_route_200(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, deal_ids = env
    deal_id = _make_deal(engine, deal_ids, reviewer_uid=REVIEWER1.uid)
    app.dependency_overrides[require_reviewer] = lambda: REVIEWER1
    app.dependency_overrides[get_review_deps] = lambda: (repo, MockGmailClient())
    try:
        response = TestClient(app).post("/review/reject", json={"deal_id": deal_id})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert repo.get_deal(deal_id).state == "rejected"  # type: ignore[union-attr]
