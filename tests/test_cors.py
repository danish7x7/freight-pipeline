"""The 6.3 done-when: CORS is locked to an explicit origin allowlist, never a wildcard.

Hermetic — a throwaway app exercises the `configure_cors` seam directly with explicit
settings, so the test is independent of the process env / settings singleton. We assert
the Starlette CORS middleware echoes the allowed origin, rejects an unlisted one, and
never advertises credentials (the console authenticates with a bearer header, not
cookies).
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from freight.config import Settings
from freight.security.cors import configure_cors

ALLOWED = "http://localhost:3000"
DENIED = "https://evil.example"


def _build_app(origins: str) -> FastAPI:
    app = FastAPI()
    configure_cors(app, Settings(cors_allow_origins=origins))

    @app.post("/review/send")
    async def _send() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_build_app(ALLOWED)) as c:
        yield c


def test_preflight_allows_listed_origin(client: TestClient) -> None:
    resp = client.options(
        "/review/send",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED


def test_preflight_denies_unlisted_origin(client: TestClient) -> None:
    resp = client.options(
        "/review/send",
        headers={
            "Origin": DENIED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    # Starlette returns a non-2xx preflight and, crucially, no ACAO grant.
    assert "access-control-allow-origin" not in resp.headers


def test_actual_request_only_echoes_allowed_origin(client: TestClient) -> None:
    allowed = client.post("/review/send", headers={"Origin": ALLOWED})
    assert allowed.headers.get("access-control-allow-origin") == ALLOWED

    denied = client.post("/review/send", headers={"Origin": DENIED})
    assert "access-control-allow-origin" not in denied.headers


def test_credentials_never_advertised(client: TestClient) -> None:
    resp = client.post("/review/send", headers={"Origin": ALLOWED})
    # Bearer-header auth, no cookies => credentials must not be granted.
    assert "access-control-allow-credentials" not in resp.headers


def test_empty_allowlist_is_fail_closed() -> None:
    with TestClient(_build_app("")) as c:
        resp = c.post("/review/send", headers={"Origin": ALLOWED})
        assert "access-control-allow-origin" not in resp.headers
