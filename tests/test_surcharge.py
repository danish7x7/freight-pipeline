"""Fuel-surcharge job: append new versions (not overwrite) + route + workflow YAML."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.api.main import app
from freight.api.routes.surcharge import get_surcharge_runner
from freight.db import IngestRepository, RateKey, make_engine
from freight.surcharge import run_surcharge_update

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

_CHI_DAL_GENERIC = (
    "origin_city='Chicago' and dest_city='Dallas' and equipment='dry_van' "
    "and carrier_id is null and source='contracted'"
)


class _NoopCache:
    def invalidate(self, key: RateKey) -> None:
        pass


@pytest.fixture
def engine_repo() -> Iterator[tuple[Engine, IngestRepository]]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    try:
        yield engine, IngestRepository(engine)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_list_contracted_lanes(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    _, repo = engine_repo
    lanes = repo.list_contracted_lanes()
    amounts = {lane.amount_cents for lane in lanes}
    # current contracted per key: lane-generic 125000, Acme 118000, reefer 95000
    assert {125000, 118000, 95000} <= amounts


@pytest.mark.integration
def test_surcharge_appends_new_version_not_overwrite(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            before = conn.execute(
                text(f"select count(*) from rates where {_CHI_DAL_GENERIC}")
            ).scalar_one()

            count = run_surcharge_update(conn, repo, _NoopCache(), delta_cents=1000)
            assert count == 3  # the three seeded contracted lanes

            after = conn.execute(
                text(f"select count(*) from rates where {_CHI_DAL_GENERIC}")
            ).scalar_one()
            assert after == before + 1  # appended, not overwritten

            current = conn.execute(
                text(
                    f"select amount_cents from rates where {_CHI_DAL_GENERIC} "
                    "order by effective_from desc, created_at desc limit 1"
                )
            ).scalar_one()
            assert current == 126000  # 125000 + 1000, now the current version

            old_present = conn.execute(
                text(f"select count(*) from rates where {_CHI_DAL_GENERIC} "
                     "and amount_cents=125000")
            ).scalar_one()
            assert old_present == 1  # the prior version still exists
        finally:
            trans.rollback()


def test_surcharge_route_returns_count() -> None:
    app.dependency_overrides[get_surcharge_runner] = lambda: (lambda: 3)
    try:
        response = TestClient(app).post("/jobs/surcharge")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"versions_written": 3}


def test_surcharge_workflow_yaml_parses() -> None:
    yaml = pytest.importorskip("yaml")
    path = Path(__file__).resolve().parents[1] / ".github/workflows/fuel-surcharge.yml"
    data = yaml.safe_load(path.read_text())
    assert "surcharge" in data["jobs"]
    assert data[True]["schedule"] == [{"cron": "0 6 * * *"}]
