"""The /health route returns a 200 ok payload."""

from fastapi.testclient import TestClient

from freight.api.main import app


def test_health_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
