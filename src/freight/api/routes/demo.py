"""The /demo/sample route — the showcase "load sample order" control.

Fail-closed and tightly scoped, so the demo never weakens a trust boundary:
- ``require_demo_enabled`` → 404 when ``DEMO_ENABLED`` is off (no demo write path).
- ``require_reviewer`` (the same Supabase JWT/RBAC as /review) → not anonymous; and the
  caller must be ADMIN, because a demo deal (like a real ingested deal) has no assigned
  reviewer and is therefore admin-visible under RLS.
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
    # Demo deals are NULL-reviewer → admin-visible under RLS (same as real ingested
    # deals). Require admin so the caller can actually see the result in the queue.
    if reviewer.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="demo requires an admin (demo deals are admin-visible)",
        )
    settings = get_settings()
    repo = IngestRepository(get_engine(settings.database_url))
    return run_demo_sample(repo, sample=req.sample)
