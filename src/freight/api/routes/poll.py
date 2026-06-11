"""POST /poll — run one poll cycle. The GitHub Actions cron target.

Correctness is INDEPENDENT of poll cadence: idempotent claims + the DB reconciliation
sweep mean a delayed or dropped poll only adds latency, never loss or double-process.
The cron is a convenience trigger, not a correctness dependency — no external scheduler
is warranted.

⚠️ Phase 6 gate: like /ingest, this endpoint is UNAUTHENTICATED but triggers ingestion.
A shared-secret / OIDC check must land before the Phase 8 deploy — see DECISIONS.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from freight.config import get_settings
from freight.ingestion.poller import Poller, build_poller

router = APIRouter()


def get_poller() -> Poller:
    """Build the poller from config (overridden in tests)."""
    return build_poller(get_settings())


PollerDep = Annotated[Poller, Depends(get_poller)]


@router.post("/poll")
def poll(poller: PollerDep) -> dict[str, int]:
    result = asyncio.run(poller.poll())
    return {"enqueued": result.enqueued, "recovered": result.recovered}
