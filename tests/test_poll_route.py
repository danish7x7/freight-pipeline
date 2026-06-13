"""/poll route returns the poller's counts (stub poller, no DB)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from freight.api.main import app
from freight.api.routes.poll import get_poller
from freight.ingestion.poller import PollResult
from freight.security.cron_auth import get_cron_secret

CRON_SECRET = "test-cron-secret"


class _StubPoller:
    def __init__(self, result: PollResult) -> None:
        self._result = result

    async def poll(self) -> PollResult:
        return self._result


def _stub_factory() -> _StubPoller:
    return _StubPoller(PollResult(enqueued=3, recovered=1))


def test_poll_route_returns_counts() -> None:
    app.dependency_overrides[get_poller] = _stub_factory
    app.dependency_overrides[get_cron_secret] = lambda: CRON_SECRET
    try:
        response = TestClient(app).post(
            "/poll", headers={"Authorization": f"Bearer {CRON_SECRET}"}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"enqueued": 3, "recovered": 1}


def test_poll_workflow_yaml_parses() -> None:
    yaml = pytest.importorskip("yaml")
    path = Path(__file__).resolve().parents[1] / ".github/workflows/poll-inbox.yml"
    data = yaml.safe_load(path.read_text())
    assert "jobs" in data
    assert "poll" in data["jobs"]
    # GitHub's 5-minute scheduling floor — never tighter than */5.
    schedule = data[True]["schedule"]  # YAML parses the `on:` key as boolean True
    assert schedule == [{"cron": "*/5 * * * *"}]
