"""The /demo/sample route — the showcase "load sample order" control.

Fail-closed and tightly scoped, so the demo never weakens a trust boundary:
- ``require_demo_enabled`` → 404 when ``DEMO_ENABLED`` is off (no demo write path).
- ``require_reviewer`` (the same Supabase JWT/RBAC as /review) → not anonymous. NOT
  admin-gated: the published demo login is a LEAST-PRIVILEGE reviewer. The demo deal is
  assigned to the caller (RLS scopes it to that reviewer — not admin-visible) and is
  flagged ``is_demo`` so the send service refuses it — a demo login cannot send or see
  other data. Publishing a demo login must never publish admin/send capability.
- rate-limited via the existing limiter.

It does NOT touch /ingest and does NOT bypass any signature: the business logic
(seed → real validation gate → rate → finalize) lives in ``freight.demo.service``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from freight.auth import Reviewer, require_reviewer
from freight.config import get_settings
from freight.db.repository import IngestRepository, get_engine
from freight.demo import DemoResult, SampleName, run_demo_sample
from freight.security.http_rate_limit import RateLimit

router = APIRouter()


def require_demo_enabled() -> None:
    """404 when the demo is disabled — fail-closed, no demo endpoint exists."""
    if not get_settings().demo_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


class DemoRequest(BaseModel):
    sample: SampleName


ReviewerDep = Annotated[Reviewer, Depends(require_reviewer)]


@router.post(
    "/demo/sample",
    dependencies=[Depends(require_demo_enabled), Depends(RateLimit("demo_sample"))],
)
def demo_sample(req: DemoRequest, reviewer: ReviewerDep) -> DemoResult:
    # Any authenticated reviewer (the published least-privilege demo login, or an admin)
    # may run the demo. The deal is assigned to the caller (RLS-scoped to them) and
    # flagged is_demo (send service refuses it) — so no admin/send capability is needed.
    settings = get_settings()
    repo = IngestRepository(get_engine(settings.database_url))
    return run_demo_sample(repo, sample=req.sample, reviewer_uid=reviewer.uid)
