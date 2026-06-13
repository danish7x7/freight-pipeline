"""The 6.2 done-when: /poll and /jobs/surcharge require the CRON_SECRET bearer.

Hermetic — the downstream poll/surcharge work is stubbed via dependency overrides, so
no DB/Gmail/Redis is touched. The auth dependency itself runs for real. Both endpoints
are covered by parametrization.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from freight.api.main import app
from freight.api.routes.poll import get_poller
from freight.api.routes.surcharge import get_surcharge_runner
from freight.ingestion.poller import PollResult
from freight.security.cron_auth import get_cron_secret

CRON_SECRET = "test-cron-secret"

# (path, factory-dep, stub) for each guarded endpoint.
ENDPOINTS = [
    pytest.param("/poll", id="poll"),
    pytest.param("/jobs/surcharge", id="surcharge"),
]


class _StubPoller:
    async def poll(self) -> PollResult:
        return PollResult(enqueued=0, recovered=0)


def _override_downstream() -> None:
    """Stub both endpoints' work so a 200 reflects only the auth gate passing."""
    app.dependency_overrides[get_poller] = lambda: _StubPoller()
    app.dependency_overrides[get_surcharge_runner] = lambda: (lambda: 0)


@pytest.fixture
def client() -> Iterator[TestClient]:
    _override_downstream()
    app.dependency_overrides[get_cron_secret] = lambda: CRON_SECRET
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client_unconfigured() -> Iterator[TestClient]:
    """Client where CRON_SECRET is empty — the fail-closed guard must reject all."""
    _override_downstream()
    app.dependency_overrides[get_cron_secret] = lambda: ""
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("path", ENDPOINTS)
def test_correct_secret_returns_200(client: TestClient, path: str) -> None:
    resp = client.post(path, headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ENDPOINTS)
def test_wrong_secret_returns_401(client: TestClient, path: str) -> None:
    resp = client.post(path, headers={"Authorization": "Bearer wrong-secret"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ENDPOINTS)
def test_missing_header_returns_401(client: TestClient, path: str) -> None:
    assert client.post(path).status_code == 401


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize(
    "header",
    ["Basic abc", "Bearer", "Bearer ", "Token xyz", CRON_SECRET],
    ids=["wrong-scheme", "no-token", "empty-token", "token-scheme", "no-scheme"],
)
def test_malformed_header_returns_401(
    client: TestClient, path: str, header: str
) -> None:
    assert client.post(path, headers={"Authorization": header}).status_code == 401


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize(
    "header",
    [None, "Bearer ", f"Bearer {CRON_SECRET}"],
    ids=["missing", "empty-bearer", "any-bearer"],
)
def test_unconfigured_secret_fails_closed(
    client_unconfigured: TestClient, path: str, header: str | None
) -> None:
    # compare_digest("", "") is True, so an empty configured secret must reject
    # BEFORE any compare — an empty (or any) bearer can never fail open.
    headers = {"Authorization": header} if header is not None else {}
    assert client_unconfigured.post(path, headers=headers).status_code == 401
