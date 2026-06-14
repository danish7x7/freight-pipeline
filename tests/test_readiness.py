"""Phase 7.2: readiness probe — distinct from /health liveness; DB hard, Redis soft.

Hermetic — the readiness mapping is exercised directly, and the /ready route via a
dependency override. No real DB or Redis.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from freight.api.main import app, get_readiness_report
from freight.observability.readiness import ReadinessReport

# --- the report's status/HTTP mapping (the hard-vs-soft logic) ----------------------


def test_both_up_is_ready() -> None:
    r = ReadinessReport(database="ok", redis="ok")
    assert r.status == "ready"
    assert r.http_status == 200


def test_redis_down_is_degraded_not_down() -> None:
    # Redis is fail-open → degraded, still serving (HTTP 200).
    r = ReadinessReport(database="ok", redis="down")
    assert r.status == "degraded"
    assert r.http_status == 200


def test_database_down_is_not_ready() -> None:
    # Postgres is the hard dependency → pull from rotation (HTTP 503).
    r = ReadinessReport(database="down", redis="ok")
    assert r.status == "not_ready"
    assert r.http_status == 503


def test_database_down_dominates_even_if_redis_also_down() -> None:
    r = ReadinessReport(database="down", redis="down")
    assert r.status == "not_ready"


# --- the /ready route (distinct from /health) ---------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_liveness_is_independent_of_dependencies(client: TestClient) -> None:
    # Liveness never checks deps — it stays 200 even when readiness would be 503.
    app.dependency_overrides[get_readiness_report] = lambda: ReadinessReport(
        database="down", redis="down"
    )
    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_returns_200_when_ready(client: TestClient) -> None:
    app.dependency_overrides[get_readiness_report] = lambda: ReadinessReport(
        database="ok", redis="ok"
    )
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok"},
    }


def test_ready_returns_200_degraded_when_redis_down(client: TestClient) -> None:
    app.dependency_overrides[get_readiness_report] = lambda: ReadinessReport(
        database="ok", redis="down"
    )
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_ready_returns_503_when_database_down(client: TestClient) -> None:
    app.dependency_overrides[get_readiness_report] = lambda: ReadinessReport(
        database="down", redis="ok"
    )
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
