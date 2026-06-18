"""The /health route returns 200 for both GET and HEAD (uptime probes use HEAD)."""

from fastapi.testclient import TestClient

from freight.api.main import app


def test_health_get_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_head_ok() -> None:
    """HEAD is UptimeRobot's default probe method — it must not 405."""
    client = TestClient(app)
    response = client.head("/health")
    assert response.status_code == 200
    # HEAD returns headers only (no body) by spec.
    assert response.content == b""
