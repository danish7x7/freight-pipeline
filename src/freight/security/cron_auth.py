"""Bearer-secret auth for the cron-triggered endpoints (/poll, /jobs/surcharge).

These endpoints trigger ingestion and rate writes, so they must not be callable by
anyone. A single shared secret (``CRON_SECRET``) gates both; the GitHub Actions crons
send it as ``Authorization: Bearer <secret>``. Auth lives in this dependency, never
inline in the handlers and never mixed with poll/surcharge logic.

Fail-closed, with one trap made explicit: ``hmac.compare_digest("", "")`` is ``True``,
so if ``CRON_SECRET`` is unset an empty bearer would otherwise pass. The unconfigured
secret is rejected BEFORE any compare runs.
"""

import hmac
import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from freight.config import get_settings

logger = logging.getLogger(__name__)


def get_cron_secret() -> str:
    """The configured cron bearer secret (overridden in tests)."""
    return get_settings().cron_secret


CronSecretDep = Annotated[str, Depends(get_cron_secret)]


def require_cron_secret(request: Request, configured: CronSecretDep) -> None:
    """Reject any request without the correct cron bearer. Returns None on success.

    Order matters: the unconfigured-secret guard runs before the compare, because
    ``compare_digest("", "")`` is ``True`` — an empty configured secret must never be
    compared against, or an empty bearer would fail open.
    """
    if not configured:
        # Misconfiguration must fail closed, and be visible (not hide as a routine
        # 401). Consistent with the 6.1 fail-closed logging. Phase 7 structures these.
        logger.warning("CRON_SECRET is unconfigured; rejecting cron request")
        raise HTTPException(status_code=401, detail="Cron auth not configured")

    header = request.headers.get("Authorization")
    if header is None:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Malformed Authorization header")

    if not hmac.compare_digest(token, configured):
        raise HTTPException(status_code=401, detail="Invalid cron secret")
