"""The fail-closed contract: Sentry initializes ONLY when a DSN is configured.

An empty ``sentry_dsn`` (the default) must NOT call ``sentry_sdk.init`` — no DSN means
no SDK activity, so local/dev and tests stay silent and a missing DSN never crashes.
"""

from unittest.mock import patch

from freight.api.main import configure_sentry
from freight.config import AppEnv, Settings


def test_sentry_disabled_when_dsn_empty() -> None:
    settings = Settings(sentry_dsn="")
    with patch("freight.api.main.sentry_sdk.init") as init:
        initialized = configure_sentry(settings)
    init.assert_not_called()
    assert initialized is False


def test_sentry_enabled_when_dsn_set() -> None:
    settings = Settings(
        sentry_dsn="https://pub@o0.ingest.sentry.io/0",
        app_env=AppEnv.production,
    )
    with patch("freight.api.main.sentry_sdk.init") as init:
        initialized = configure_sentry(settings)
    init.assert_called_once()
    kwargs = init.call_args.kwargs
    assert kwargs["dsn"] == settings.sentry_dsn
    # Error-capture-only + PII discipline (the non-negotiable posture).
    assert kwargs["environment"] == "production"
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["traces_sample_rate"] == 0.0
    assert initialized is True
