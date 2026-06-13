"""Reviewer action endpoints — the human gate (JWT-protected).

POST /review/send is the ONLY path that triggers an outbound email.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from freight.auth import Reviewer, require_reviewer
from freight.config import get_settings
from freight.db.repository import IngestRepository, make_engine
from freight.factories import build_gmail_client
from freight.interfaces import GmailClient
from freight.security.http_rate_limit import RateLimit
from freight.sending import SendError, reject_deal, send_quote

router = APIRouter()

ReviewerDep = Annotated[Reviewer, Depends(require_reviewer)]


def get_review_deps() -> tuple[IngestRepository, GmailClient]:
    """Build (repo, gmail) from config (overridden in tests)."""
    settings = get_settings()
    return IngestRepository(make_engine(settings.database_url)), build_gmail_client(
        settings
    )


ReviewDeps = Annotated[tuple[IngestRepository, GmailClient], Depends(get_review_deps)]


class SendRequest(BaseModel):
    quote_id: str
    body: str


class RejectRequest(BaseModel):
    deal_id: str


@router.post("/review/send", dependencies=[Depends(RateLimit("review_send"))])
def review_send(
    request: SendRequest, reviewer: ReviewerDep, deps: ReviewDeps
) -> dict[str, str]:
    repo, gmail = deps
    try:
        result = send_quote(
            repo, gmail, reviewer=reviewer, quote_id=request.quote_id, body=request.body
        )
    except SendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "send_id": result.send_id,
        "gmail_message_id": result.gmail_message_id,
    }


@router.post("/review/reject", dependencies=[Depends(RateLimit("review_reject"))])
def review_reject(
    request: RejectRequest, reviewer: ReviewerDep, deps: ReviewDeps
) -> dict[str, str]:
    repo, _ = deps
    try:
        reject_deal(repo, reviewer=reviewer, deal_id=request.deal_id)
    except SendError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"status": "rejected", "deal_id": request.deal_id}
