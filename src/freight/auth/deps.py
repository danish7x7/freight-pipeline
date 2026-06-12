"""FastAPI auth dependency: verify the Supabase JWT, resolve app role from public.users.

The Postgres ``role`` claim is always 'authenticated'; the APP role (reviewer vs admin)
is looked up from public.users by uid — never trusted from the token.
"""

from dataclasses import dataclass
from typing import Annotated, Protocol

from fastapi import Depends, Header, HTTPException

from freight.auth.jwt import Identity, JwtError, SupabaseJwtVerifier
from freight.config import get_settings
from freight.db.repository import IngestRepository, UserRecord, UserRole, make_engine


@dataclass(frozen=True)
class Reviewer:
    """The authenticated caller, with app role for RBAC."""

    uid: str
    email: str | None
    role: UserRole


class _Verifier(Protocol):
    def verify(self, token: str) -> Identity: ...


class _UserLookup(Protocol):
    def get_user(self, uid: str) -> UserRecord | None: ...


def get_verifier() -> SupabaseJwtVerifier:
    return SupabaseJwtVerifier.from_settings(get_settings())


def get_auth_repo() -> IngestRepository:
    return IngestRepository(make_engine(get_settings().database_url))


VerifierDep = Annotated[_Verifier, Depends(get_verifier)]
AuthRepoDep = Annotated[_UserLookup, Depends(get_auth_repo)]
AuthHeader = Annotated[str | None, Header()]


def require_reviewer(
    verifier: VerifierDep,
    repo: AuthRepoDep,
    authorization: AuthHeader = None,
) -> Reviewer:
    """Resolve the authenticated reviewer, or raise 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        identity = verifier.verify(token)
    except JwtError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    user = repo.get_user(identity.uid)
    if user is None:
        raise HTTPException(status_code=401, detail="unknown user")
    return Reviewer(
        uid=identity.uid, email=identity.email or user.email, role=user.role
    )
