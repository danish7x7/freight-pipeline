"""CORS lockdown for the API (Phase 6.3).

Only the review console (`/review/send`, `/review/reject`) is browser-facing; the
QStash `/ingest` and the cron `/poll` / `/jobs/surcharge` routes are server-to-server
and carry no browser `Origin`. So CORS exists to let the console's origin — and only
that origin — make cross-origin calls, never a wildcard.

The allowlist is env-driven (`CORS_ALLOW_ORIGINS`, comma-separated) so Phase 8 wires
the deployed Vercel origin without a code change. Empty => no origin allowed
(fail-closed), consistent with the 6.1/6.2 posture.

`allow_credentials=False`: the console authenticates with an explicit
`Authorization: Bearer <JWT>` header (see `web/lib/api.ts`), not cookies, so we never
rely on credentialed requests. Keeping it false is the tighter setting and avoids the
browser's wildcard+credentials rejection rule entirely.

Config lives here, never inline in the app body or the handlers — same discipline as
`cron_auth` and `qstash_verifier`.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from freight.config import Settings


def configure_cors(app: FastAPI, settings: Settings) -> None:
    """Attach the CORS middleware with an explicit origin allowlist."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list(),
        allow_credentials=False,
        allow_methods=["POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
