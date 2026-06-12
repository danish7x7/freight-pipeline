"""Verify Supabase JWTs against the project's JWKS (ES256, asymmetric).

Current Supabase signs access tokens with asymmetric ES256 keys, so verification fetches
the project's JWKS (URL derived from SUPABASE_URL — works local + prod) and checks the
token's signature by ``kid``. Keys are cached; an unknown kid triggers ONE re-fetch
(handles rotation) rate-limited so a bogus-kid flood can't hammer the JWKS endpoint.

Claims are validated, not just the signature: ``exp`` (not expired), ``aud`` =
'authenticated', and ``iss`` = the project issuer. The Postgres ``role`` claim
('authenticated') is NOT used for app authz — that comes from public.users (see deps).
"""

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt.algorithms import ECAlgorithm

_AUDIENCE = "authenticated"
_MIN_REFRESH_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class Identity:
    """The verified caller (from the token)."""

    uid: str
    email: str | None


class JwtError(Exception):
    """The token is missing, malformed, expired, or fails verification."""


class SupabaseJwtVerifier:
    """Verifies ES256 Supabase JWTs against a cached JWKS."""

    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: str,
        audience: str = _AUDIENCE,
        client: httpx.Client | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._client = client or httpx.Client(timeout=5.0)
        self._keys: dict[str, Any] = {}
        self._last_refresh = 0.0

    @classmethod
    def from_settings(cls, settings: Any) -> "SupabaseJwtVerifier":
        base = settings.supabase_url.rstrip("/")
        return cls(
            jwks_url=f"{base}/auth/v1/.well-known/jwks.json",
            issuer=f"{base}/auth/v1",
        )

    def verify(self, token: str) -> Identity:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError as exc:
            raise JwtError("malformed token") from exc
        key = self._key_for(kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["ES256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.InvalidTokenError as exc:
            raise JwtError(f"invalid token: {exc}") from exc
        sub = claims.get("sub")
        if not sub:
            raise JwtError("token missing sub")
        return Identity(uid=sub, email=claims.get("email"))

    def _key_for(self, kid: str | None) -> Any:
        if kid and kid in self._keys:
            return self._keys[kid]
        self._refresh()  # unknown kid → fetch once (rate-limited)
        if kid and kid in self._keys:
            return self._keys[kid]
        raise JwtError(f"unknown signing key {kid!r}")

    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_refresh < _MIN_REFRESH_INTERVAL_SECONDS and self._keys:
            return  # don't hammer JWKS on a bogus-kid flood
        self._last_refresh = now
        try:
            response = self._client.get(self._jwks_url)
            response.raise_for_status()
            jwks = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JwtError(f"jwks fetch failed: {exc}") from exc
        self._keys = {
            jwk["kid"]: ECAlgorithm.from_jwk(json.dumps(jwk))
            for jwk in jwks.get("keys", [])
            if jwk.get("kty") == "EC" and jwk.get("kid")
        }
