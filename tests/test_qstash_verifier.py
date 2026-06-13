"""Unit tests for the QStash signature-verification seam.

These mint a real HS256 token locally (matching the SDK's exact claim set and
body-hash encoding, read from ``qstash/receiver.py``) and run the genuine
``qstash.Receiver`` — the verifier is never stubbed.
"""

import base64
import hashlib
import time

import jwt
import pytest
from qstash import Receiver

from freight.config import Settings
from freight.security.qstash_verifier import (
    SDKQStashVerifier,
    SignatureError,
    build_qstash_verifier,
)

# Keys are >=32 bytes so PyJWT doesn't emit InsecureKeyLengthWarning for HS256.
CURRENT = "test-current-signing-key-padding-0123456789"
NEXT = "test-next-signing-key-padding-0123456789abc"
URL = "https://freight.example/ingest"
WRONG_KEY = "an-unknown-signing-key-padding-0123456789ab"


def mint_token(
    key: str,
    *,
    body: bytes,
    url: str,
    exp_delta: int = 300,
    nbf_delta: int = -10,
) -> str:
    """Sign a QStash-shaped HS256 token over ``body`` exactly as the SDK verifies it.

    Body-hash encoding mirrors ``qstash.receiver.verify_with_key``:
    ``urlsafe_b64encode(sha256(body)).rstrip("=")``.
    """
    body_hash = base64.urlsafe_b64encode(hashlib.sha256(body).digest())
    now = int(time.time())
    claims = {
        "iss": "Upstash",
        "sub": url,
        "iat": now,
        "nbf": now + nbf_delta,
        "exp": now + exp_delta,
        "body": body_hash.decode().rstrip("="),
    }
    return jwt.encode(claims, key, algorithm="HS256")


def _verifier() -> SDKQStashVerifier:
    return SDKQStashVerifier(
        Receiver(current_signing_key=CURRENT, next_signing_key=NEXT),
        expected_url=URL,
    )


def test_verify_passes_on_valid_signature() -> None:
    body = b'{"id":"abc"}'
    token = mint_token(CURRENT, body=body, url=URL)
    _verifier().verify(body=body, signature=token)  # no raise == valid


def test_verify_accepts_next_key_for_rotation() -> None:
    # A token signed with the NEXT key must still verify (key-rotation window).
    body = b'{"id":"abc"}'
    token = mint_token(NEXT, body=body, url=URL)
    _verifier().verify(body=body, signature=token)


def test_verify_raises_on_tampered_body() -> None:
    body = b'{"id":"abc"}'
    token = mint_token(CURRENT, body=body, url=URL)
    with pytest.raises(SignatureError):
        _verifier().verify(body=body + b" ", signature=token)


def test_verify_raises_on_wrong_key() -> None:
    body = b'{"id":"abc"}'
    token = mint_token(WRONG_KEY, body=body, url=URL)
    with pytest.raises(SignatureError):
        _verifier().verify(body=body, signature=token)


def test_verify_raises_on_sub_mismatch() -> None:
    # The `sub` binding is live: a token minted for another URL must reject.
    body = b'{"id":"abc"}'
    token = mint_token(CURRENT, body=body, url="https://evil.example/ingest")
    with pytest.raises(SignatureError):
        _verifier().verify(body=body, signature=token)


def test_build_qstash_verifier_from_settings() -> None:
    settings = Settings(
        qstash_current_signing_key=CURRENT,
        qstash_next_signing_key=NEXT,
        qstash_expected_url=URL,
    )
    verifier = build_qstash_verifier(settings)
    assert isinstance(verifier, SDKQStashVerifier)
    body = b'{"id":"abc"}'
    verifier.verify(body=body, signature=mint_token(CURRENT, body=body, url=URL))
