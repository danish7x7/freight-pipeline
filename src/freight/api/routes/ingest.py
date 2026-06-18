"""The /ingest route — QStash's push target.

QStash decides to retry from the HTTP status, not a Python exception, so a ``handle()``
raise is mapped to 5xx here (same failure trigger as the local dispatcher's
retry-on-exception). Both error kinds map to 5xx for now, so a poison message exhausts
retries and lands in QStash's DLQ.

**Auth boundary (Phase 6).** The signature gate runs as a dependency chain —
``require_qstash_signature`` (verify the raw body) → ``parse_verified_message`` (parse
only after verifying) → handler. Because the message arrives via ``Depends``, FastAPI
does no automatic body parsing, so verification over the exact raw bytes strictly
precedes any JSON parse, the ``gmail_message_id`` idempotency claim, and any Redis/DB/
enqueue work (all of which live inside ``consumer.handle``). The gate is
**fail-closed**: a missing header, a bad/expired signature, the wrong key, or any
verifier exception → 401, never a fall-through into the handler.

The route is a sync ``def`` (runs in a threadpool), sharing the sync repository; it runs
the async ``handle`` via ``asyncio.run``.
"""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from freight.config import get_settings
from freight.db.repository import IngestRepository, get_engine
from freight.factories import build_llm_client
from freight.ingestion.consumer import IngestConsumer, IngestError
from freight.interfaces.types import QueueMessage
from freight.security.http_rate_limit import RateLimit
from freight.security.qstash_verifier import (
    QStashVerifier,
    SignatureError,
    build_qstash_verifier,
)
from freight.storage import SupabaseStorageReader

logger = logging.getLogger(__name__)

router = APIRouter()


def get_consumer() -> IngestConsumer:
    """Build the consumer from config (overridden in tests)."""
    settings = get_settings()
    repo = IngestRepository(get_engine(settings.database_url))
    # Real Supabase Storage reader only when a bucket is configured (Render); otherwise
    # the consumer falls back to the UnconfiguredStorageReader placeholder (body-only
    # path). Env-driven swap — no code change to flip. (8.3b)
    storage = (
        SupabaseStorageReader.from_settings(settings)
        if settings.supabase_storage_bucket
        else None
    )
    return IngestConsumer(repo, build_llm_client(settings), storage=storage)


def get_qstash_verifier() -> QStashVerifier:
    """Build the signature verifier from config (overridden in tests)."""
    return build_qstash_verifier(get_settings())


ConsumerDep = Annotated[IngestConsumer, Depends(get_consumer)]
VerifierDep = Annotated[QStashVerifier, Depends(get_qstash_verifier)]


async def require_qstash_signature(request: Request, verifier: VerifierDep) -> bytes:
    """Reject any request that is not a validly signed QStash delivery.

    Reads the raw body and verifies it BEFORE any parse or processing. Returns the
    raw bytes so the next dependency can parse them. Fail-closed: every rejection path
    is a 401, and the verifier is never allowed to fall through to the handler.
    """
    signature = request.headers.get("Upstash-Signature")
    if signature is None:
        raise HTTPException(status_code=401, detail="Missing Upstash-Signature")
    body = await request.body()
    try:
        verifier.verify(body=body, signature=signature)
    except SignatureError as exc:
        # The expected, routine rejection: bad/expired/wrong-key/sub-mismatch.
        raise HTTPException(status_code=401, detail="Invalid signature") from exc
    except Exception as exc:
        # Anything else (e.g. a misconfigured key) must still fail closed, but log
        # the type so a config/bug error can't hide as routine auth-failure noise.
        # (Phase 7 formats these as structured logs.)
        logger.warning(
            "qstash signature verification raised unexpected %s", type(exc).__name__
        )
        raise HTTPException(status_code=401, detail="Invalid signature") from exc
    return body


def parse_verified_message(
    body: Annotated[bytes, Depends(require_qstash_signature)],
) -> QueueMessage:
    """Parse the queue message — only reached after the signature is verified."""
    return QueueMessage.model_validate_json(body)


MessageDep = Annotated[QueueMessage, Depends(parse_verified_message)]


@router.post("/ingest", dependencies=[Depends(RateLimit("ingest"))])
def ingest(message: MessageDep, consumer: ConsumerDep) -> dict[str, str]:
    try:
        asyncio.run(consumer.handle(message))
    except IngestError as exc:
        # 5xx => QStash retries; after retries are exhausted it DLQs.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "ok"}
