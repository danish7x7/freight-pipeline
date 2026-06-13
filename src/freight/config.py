"""Application settings, loaded from the environment (see .env.example).

Settings are the single source of runtime configuration. Interface implementations
are selected by the ``*_backend`` fields and built in ``factories.py`` — never by
rewriting call sites.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    local = "local"
    staging = "staging"
    production = "production"


class Settings(BaseSettings):
    """Typed view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_env: AppEnv = AppEnv.local
    log_level: str = "INFO"
    app_secret: str = "dev-only-insecure-secret"

    # --- Interface selection (swap impls by config, not by code) ---
    llm_backend: Literal["mock", "hf"] = "mock"
    gmail_backend: Literal["mock", "gmail"] = "mock"
    queue_backend: Literal["memory", "qstash"] = "memory"

    # --- Supabase ---
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    database_url: str = "postgresql://postgres:postgres@localhost:5432/freight"

    # --- Redis (Upstash) ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Rate engine ---
    fuel_surcharge_delta_cents: int = 1000  # added to each lane per surcharge run

    # --- Upstash QStash ---
    qstash_token: str = ""
    qstash_url: str = "https://qstash.upstash.io"
    # The consumer endpoint QStash pushes to (the deployed /ingest URL). Placeholder
    # locally; real value wired at Phase 8.
    qstash_destination_url: str = ""
    # Signature verification keys (QStash signs each delivery; the verifier tries
    # current then next to survive a rotation). Real keys from the QStash console at
    # Phase 8; empty locally. The expected URL is the signed `sub` claim — the public
    # /ingest URL; configurable, never hard-coded. Empty => sub is not matched.
    qstash_current_signing_key: str = ""
    qstash_next_signing_key: str = ""
    qstash_expected_url: str = ""

    # --- Hugging Face serverless inference (OpenAI-compatible chat-completions) ---
    hf_token: str = ""
    hf_model: str = ""
    hf_base_url: str = "https://router.huggingface.co"

    # --- Gmail OAuth (single inbox; refresh token is the one runtime secret) ---
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_redirect_uri: str = Field(
        default="http://localhost:8000/auth/gmail/callback"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
