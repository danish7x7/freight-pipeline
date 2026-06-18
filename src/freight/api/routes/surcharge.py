"""POST /jobs/surcharge — the fuel-surcharge cron target.

Auth (Phase 6.2): guarded by the shared CRON_SECRET bearer via ``require_cron_secret``
(see ``freight.security.cron_auth``). The GitHub Actions cron sends the secret.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from redis import Redis

from freight.config import get_settings
from freight.db.repository import IngestRepository, get_engine
from freight.rates import CachedRateLookup
from freight.security.cron_auth import require_cron_secret
from freight.security.http_rate_limit import RateLimit
from freight.surcharge import run_surcharge_update

router = APIRouter()


def get_surcharge_runner() -> Callable[[], int]:
    """Build the surcharge runner from config (overridden in tests)."""
    settings = get_settings()
    repo = IngestRepository(get_engine(settings.database_url))
    cache = CachedRateLookup(repo, Redis.from_url(settings.redis_url))

    def _run() -> int:
        with repo.begin() as conn:
            return run_surcharge_update(
                conn, repo, cache, delta_cents=settings.fuel_surcharge_delta_cents
            )

    return _run


RunnerDep = Annotated[Callable[[], int], Depends(get_surcharge_runner)]


@router.post(
    "/jobs/surcharge",
    dependencies=[Depends(RateLimit("surcharge")), Depends(require_cron_secret)],
)
def surcharge(run: RunnerDep) -> dict[str, int]:
    return {"versions_written": run()}
