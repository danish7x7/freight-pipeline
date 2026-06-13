"""The 6.1 done-when: /ingest rejects anything that isn't a validly signed delivery.

Hermetic (no DB): the consumer is overridden with a no-op so a 200 proves only that
the signature gate passed and the handler was reached. The verifier itself is the
REAL SDK-backed ``SDKQStashVerifier`` built with known test keys — it actually
executes; nothing is monkeypatched. Tokens are minted locally with the genuine SDK
claim/body-hash shape (see ``tests.test_qstash_verifier.mint_token``).
"""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from qstash import Receiver

from freight.api.main import app
from freight.api.routes.ingest import get_consumer, get_qstash_verifier
from freight.interfaces.types import QueueMessage
from freight.security.qstash_verifier import SDKQStashVerifier
from tests.test_qstash_verifier import CURRENT, NEXT, URL, WRONG_KEY, mint_token

BODY = json.dumps({"id": "msg-sig-test", "payload": {}}).encode()


class _NoopConsumer:
    """Stand-in consumer: a reached handler returns 200 without touching the DB."""

    async def handle(self, message: QueueMessage) -> None:
        return None


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_qstash_verifier] = lambda: SDKQStashVerifier(
        Receiver(current_signing_key=CURRENT, next_signing_key=NEXT),
        expected_url=URL,
    )
    app.dependency_overrides[get_consumer] = lambda: _NoopConsumer()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _post(client: TestClient, body: bytes, token: str | None) -> int:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Upstash-Signature"] = token
    # content= sends the exact bytes we hashed; json= would re-serialize and break it.
    return int(client.post("/ingest", content=body, headers=headers).status_code)


def test_valid_signature_matching_body_returns_200(client: TestClient) -> None:
    token = mint_token(CURRENT, body=BODY, url=URL)
    assert _post(client, BODY, token) == 200


def test_tampered_body_returns_401(client: TestClient) -> None:
    token = mint_token(CURRENT, body=BODY, url=URL)
    assert _post(client, BODY + b" ", token) == 401  # hash mismatch


def test_missing_header_returns_401(client: TestClient) -> None:
    assert _post(client, BODY, None) == 401


def test_wrong_key_returns_401(client: TestClient) -> None:
    token = mint_token(WRONG_KEY, body=BODY, url=URL)
    assert _post(client, BODY, token) == 401


def test_expired_token_returns_401(client: TestClient) -> None:
    token = mint_token(CURRENT, body=BODY, url=URL, exp_delta=-10, nbf_delta=-20)
    assert _post(client, BODY, token) == 401


def test_sub_mismatch_returns_401(client: TestClient) -> None:
    # Proves the configured expected-URL binding actually rejects a foreign sub.
    token = mint_token(CURRENT, body=BODY, url="https://evil.example/ingest")
    assert _post(client, BODY, token) == 401
