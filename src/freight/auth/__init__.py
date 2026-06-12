"""Console/human authentication: Supabase JWT verification + RBAC."""

from freight.auth.deps import (
    Reviewer,
    get_auth_repo,
    get_verifier,
    require_reviewer,
)
from freight.auth.jwt import Identity, JwtError, SupabaseJwtVerifier

__all__ = [
    "Identity",
    "JwtError",
    "Reviewer",
    "SupabaseJwtVerifier",
    "get_auth_repo",
    "get_verifier",
    "require_reviewer",
]
