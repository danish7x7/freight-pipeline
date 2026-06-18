"""Mint a valid Upstash-Signature JWT for the load harness.

This lets the load test drive the REAL fail-closed QStash gate on ``/ingest`` (not a
bypass): it reproduces exactly what QStash signs, and the endpoint's own
``qstash.Receiver`` verifies it. ``tests/test_loadtest.py`` proves the round-trip
through the project's real verifier, so the load number reflects the gate under load.
"""

import base64
import hashlib
import time

import jwt


def body_hash(body: bytes) -> str:
    """The base64url(sha256(body)) claim QStash signs (padding stripped)."""
    digest = hashlib.sha256(body).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign_qstash(body: bytes, *, key: str, url: str, ttl_seconds: int = 300) -> str:
    """Return an ``Upstash-Signature`` HS256 JWT over ``body`` for destination ``url``.

    Claims mirror QStash (``iss=Upstash``, ``sub=url``, ``iat``/``nbf``/``exp``, and the
    ``body`` hash) — the exact set the ``qstash.Receiver`` requires.
    """
    now = int(time.time())
    claims = {
        "iss": "Upstash",
        "sub": url,
        "iat": now,
        "nbf": now - 1,
        "exp": now + ttl_seconds,
        "body": body_hash(body),
    }
    return jwt.encode(claims, key, algorithm="HS256")
