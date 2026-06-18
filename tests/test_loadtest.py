"""Hermetic proof that the load harness drives the REAL /ingest gate (no network/DB).

The load number is only honest if the signed envelopes pass the SAME fail-closed
verifier the endpoint enforces. These tests round-trip ``sign_qstash`` through the
project's real ``build_qstash_verifier`` (the official ``qstash.Receiver``) and confirm
tampering / wrong-key / wrong-url are rejected — so the load test cannot be a bypass.
Also covers the per-email cost helper.
"""

import base64
import hashlib

import pytest
from scripts.eval_llm_latency import cost_usd
from scripts.qstash_sign import body_hash, sign_qstash

from freight.config import Settings
from freight.security.qstash_verifier import SignatureError, build_qstash_verifier

_URL = "https://app.example/ingest"


def _verifier() -> object:
    settings = Settings(
        qstash_current_signing_key="sig_current",
        qstash_next_signing_key="sig_next",
        qstash_expected_url=_URL,
    )
    return build_qstash_verifier(settings)


def test_signed_body_passes_the_real_verifier() -> None:
    body = b'{"id": "load-1", "payload": {}}'
    sig = sign_qstash(body, key="sig_current", url=_URL)
    _verifier().verify(body=body, signature=sig)  # type: ignore[attr-defined]


def test_next_signing_key_is_also_accepted() -> None:
    body = b'{"id": "load-2", "payload": {}}'
    sig = sign_qstash(body, key="sig_next", url=_URL)
    _verifier().verify(body=body, signature=sig)  # type: ignore[attr-defined]


def test_tampered_body_is_rejected() -> None:
    sig = sign_qstash(b'{"id": "a"}', key="sig_current", url=_URL)
    with pytest.raises(SignatureError):
        _verifier().verify(  # type: ignore[attr-defined]
            body=b'{"id": "b"}', signature=sig
        )


def test_wrong_key_is_rejected() -> None:
    body = b'{"id": "x"}'
    sig = sign_qstash(body, key="not_the_key", url=_URL)
    with pytest.raises(SignatureError):
        _verifier().verify(body=body, signature=sig)  # type: ignore[attr-defined]


def test_wrong_url_is_rejected() -> None:
    body = b'{"id": "x"}'
    sig = sign_qstash(body, key="sig_current", url="https://evil/ingest")
    with pytest.raises(SignatureError):
        _verifier().verify(body=body, signature=sig)  # type: ignore[attr-defined]


def test_body_hash_matches_sha256_base64url() -> None:
    body = b"hello world"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
    )
    assert body_hash(body) == expected


def test_cost_usd() -> None:
    assert cost_usd(1_000_000, 0.6) == pytest.approx(0.6)
    assert cost_usd(335, 0.6) == pytest.approx(335 / 1_000_000 * 0.6)
    assert cost_usd(335, None) is None
