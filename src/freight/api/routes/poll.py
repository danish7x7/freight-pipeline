"""POST /poll — run one poll cycle. The GitHub Actions cron target.

Correctness is INDEPENDENT of poll cadence: idempotent claims + the DB reconciliation
sweep mean a delayed or dropped poll only adds latency, never loss or double-process.
The cron is a convenience trigger, not a correctness dependency — no external scheduler
is warranted.

Auth (Phase 6.2): guarded by the shared CRON_SECRET bearer via ``require_cron_secret``
(see ``freight.security.cron_auth``). The GitHub Actions cron sends the secret.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from freight.config import get_settings
from freight.ingestion.poller import Poller, build_poller
from freight.security.cron_auth import require_cron_secret

router = APIRouter()


def get_poller() -> Poller:
    """Build the poller from config (overridden in tests)."""
    return build_poller(get_settings())


PollerDep = Annotated[Poller, Depends(get_poller)]


@router.post("/poll", dependencies=[Depends(require_cron_secret)])
def poll(poller: PollerDep) -> dict[str, int]:
    result = asyncio.run(poller.poll())
    return {"enqueued": result.enqueued, "recovered": result.recovered}
