"""Supabase JWT verification (ES256/JWKS, mocked) + require_reviewer RBAC."""

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from jwt.algorithms import ECAlgorithm

from freight.auth import (
    Identity,
    JwtError,
    Reviewer,
    SupabaseJwtVerifier,
    require_reviewer,
)
from freight.db.repository import UserRecord

_KID = "test-kid"
_ISSUER = "https://proj.example/auth/v1"
_PRIVATE = ec.generate_private_key(ec.SECP256R1())


def _jwk_for(private_key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, object]:
    jwk: dict[str, object] = json.loads(ECAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "ES256"})
    return jwk


def _verifier() -> SupabaseJwtVerifier:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [_jwk_for(_PRIVATE, _KID)]})

    return SupabaseJwtVerifier(
        jwks_url="https://proj.example/jwks",
        issuer=_ISSUER,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _token(
    private: ec.EllipticCurvePrivateKey = _PRIVATE, kid: str = _KID, **overrides: object
) -> str:
    claims: dict[str, object] = {
        "sub": "uid-1",
        "email": "r@x.co",
        "aud": "authenticated",
        "iss": _ISSUER,
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="ES256", headers={"kid": kid})


def test_valid_token_resolves_identity() -> None:
    identity = _verifier().verify(_token())
    assert identity.uid == "uid-1"
    assert identity.email == "r@x.co"


def test_expired_token_rejected() -> None:
    with pytest.raises(JwtError):
        _verifier().verify(_token(exp=int(time.time()) - 10))


def test_wrong_audience_rejected() -> None:
    with pytest.raises(JwtError):
        _verifier().verify(_token(aud="anon"))


def test_wrong_issuer_rejected() -> None:
    with pytest.raises(JwtError):
        _verifier().verify(_token(iss="https://evil.example/auth/v1"))


def test_bad_signature_rejected() -> None:
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(JwtError):
        _verifier().verify(_token(private=other))  # signed by a different key


def test_unknown_kid_rejected() -> None:
    with pytest.raises(JwtError):
        _verifier().verify(_token(kid="rotated-away"))


# --------------------------------------------------------------------------- #
# require_reviewer (RBAC) — stub verifier + user lookup
# --------------------------------------------------------------------------- #
class _StubVerifier:
    def __init__(
        self, identity: Identity | None = None, error: Exception | None = None
    ) -> None:
        self._identity = identity
        self._error = error

    def verify(self, token: str) -> Identity:
        if self._error is not None:
            raise self._error
        assert self._identity is not None
        return self._identity


class _StubUsers:
    def __init__(self, user: UserRecord | None) -> None:
        self._user = user

    def get_user(self, uid: str) -> UserRecord | None:
        return self._user


def test_require_reviewer_resolves_app_role() -> None:
    reviewer = require_reviewer(
        verifier=_StubVerifier(Identity("uid-1", "r@x.co")),
        repo=_StubUsers(UserRecord(id="uid-1", email="r@x.co", role="admin")),
        authorization="Bearer tok",
    )
    assert isinstance(reviewer, Reviewer)
    assert reviewer.uid == "uid-1"
    assert reviewer.role == "admin"  # from public.users, not the token


def test_missing_bearer_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        require_reviewer(
            verifier=_StubVerifier(Identity("uid-1", None)),
            repo=_StubUsers(UserRecord(id="uid-1", email="r@x.co", role="reviewer")),
            authorization=None,
        )
    assert exc.value.status_code == 401


def test_invalid_token_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        require_reviewer(
            verifier=_StubVerifier(error=JwtError("bad")),
            repo=_StubUsers(None),
            authorization="Bearer x",
        )
    assert exc.value.status_code == 401


def test_unknown_user_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        require_reviewer(
            verifier=_StubVerifier(Identity("ghost", None)),
            repo=_StubUsers(None),
            authorization="Bearer x",
        )
    assert exc.value.status_code == 401
