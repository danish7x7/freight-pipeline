"""The /ingest route — QStash's push target.

QStash decides to retry from the HTTP status, not a Python exception, so a ``handle()``
raise is mapped to 5xx here (same failure trigger as the local dispatcher's
retry-on-exception). Both error kinds map to 5xx for now, so a poison message exhausts
retries and lands in QStash's DLQ.

The route is a sync ``def`` (runs in a threadpool), sharing the sync repository; it runs
the async ``handle`` via ``asyncio.run``.

⚠️ Phase 6 gate: this endpoint is UNAUTHENTICATED. Upstash-Signature verification must
land before the Phase 8 deploy — see DECISIONS.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from freight.config import get_settings
from freight.db.repository import IngestRepository, make_engine
from freight.factories import build_llm_client
from freight.ingestion.consumer import IngestConsumer, IngestError
from freight.interfaces.types import QueueMessage

router = APIRouter()


def get_consumer() -> IngestConsumer:
    """Build the consumer from config (overridden in tests)."""
    settings = get_settings()
    repo = IngestRepository(make_engine(settings.database_url))
    # Storage defaults to the Phase 8 placeholder (PDF byte reads land then).
    return IngestConsumer(repo, build_llm_client(settings))


ConsumerDep = Annotated[IngestConsumer, Depends(get_consumer)]


@router.post("/ingest")
def ingest(message: QueueMessage, consumer: ConsumerDep) -> dict[str, str]:
    try:
        asyncio.run(consumer.handle(message))
    except IngestError as exc:
        # 5xx => QStash retries; after retries are exhausted it DLQs.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "ok"}
