"""POST /jobs/surcharge — the fuel-surcharge cron target.

⚠️ Phase 6 gate: like /poll, this triggers writes and is currently UNAUTHENTICATED. A
shared-secret / OIDC check must land before the Phase 8 deploy — see DECISIONS.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from redis import Redis

from freight.config import get_settings
from freight.db.repository import IngestRepository, make_engine
from freight.rates import CachedRateLookup
from freight.surcharge import run_surcharge_update

router = APIRouter()


def get_surcharge_runner() -> Callable[[], int]:
    """Build the surcharge runner from config (overridden in tests)."""
    settings = get_settings()
    repo = IngestRepository(make_engine(settings.database_url))
    cache = CachedRateLookup(repo, Redis.from_url(settings.redis_url))

    def _run() -> int:
        with repo.begin() as conn:
            return run_surcharge_update(
                conn, repo, cache, delta_cents=settings.fuel_surcharge_delta_cents
            )

    return _run


RunnerDep = Annotated[Callable[[], int], Depends(get_surcharge_runner)]


@router.post("/jobs/surcharge")
def surcharge(run: RunnerDep) -> dict[str, int]:
    return {"versions_written": run()}
