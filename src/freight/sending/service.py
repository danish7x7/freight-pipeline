"""The human-gated quote send (the dual-write done right).

Reached ONLY via an explicit reviewer action — never the pipeline. Order:
1. authz (reads): quote → deal; deal owned by reviewer (or admin); state 'quoted'.
2. TX-A: claim_send (UNIQUE(quote_id)) + audit 'email.send.claimed', atomically. An
   already-'sent' claim → AlreadySent (409, no double-send). Commit.
3. Gmail send AFTER the claim commits (the guarded external step).
4. TX-B: mark_sent + audit 'email.sent'. Commit.

Recovery: a claim returning an existing 'claimed' row (crash between claim and send)
resumes at step 3 — same idempotent path, no new claim. A Gmail failure leaves the row
'claimed' (502); the reviewer retries and it resumes.
"""

import logging
from dataclasses import dataclass
from typing import cast

from freight.auth import Reviewer
from freight.db.repository import IngestRepository
from freight.deals import DealState, TransitionError, advance
from freight.interfaces import GmailClient
from freight.interfaces.types import OutboundMessage
from freight.observability import bind_correlation_id

logger = logging.getLogger("freight.sending")


class SendError(Exception):
    """A send failure mapped to an HTTP status by the route."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class SendResult:
    send_id: str
    gmail_message_id: str


def reject_deal(
    repo: IngestRepository, *, reviewer: Reviewer, deal_id: str
) -> None:
    """Reject a deal (pure DB state change + audit, one transaction)."""
    deal = repo.get_deal(deal_id)
    if deal is None:
        raise SendError(404, "deal not found")
    if reviewer.role != "admin" and deal.assigned_reviewer != reviewer.uid:
        raise SendError(403, "not your deal")
    try:
        new_state = advance(cast(DealState, deal.state), "reject")
    except TransitionError as exc:
        raise SendError(409, f"deal is {deal.state}, cannot reject") from exc

    with repo.begin() as conn:
        repo.advance_deal(conn, deal_id=deal_id, state=new_state)
        repo.insert_audit(
            conn,
            actor=reviewer.uid,
            actor_email=reviewer.email,
            action="deal.rejected",
            entity_type="deals",
            entity_id=deal_id,
        )


def send_quote(
    repo: IngestRepository,
    gmail: GmailClient,
    *,
    reviewer: Reviewer,
    quote_id: str,
    body: str,
) -> SendResult:
    """Send the approved quote reply exactly once; write the audit trail."""
    quote = repo.get_quote(quote_id)
    if quote is None:
        raise SendError(404, "quote not found")
    deal = repo.get_deal(quote.deal_id)
    if deal is None:
        raise SendError(404, "deal not found")
    if reviewer.role != "admin" and deal.assigned_reviewer != reviewer.uid:
        raise SendError(403, "not your deal")
    if deal.state != "quoted":
        raise SendError(409, f"deal is {deal.state}, not sendable")
    email = repo.get_deal_email(deal.id)
    if email is None:
        raise SendError(409, "no inbound email to reply to")

    # Bind the ORIGINATING email's id so the send traces back to the same correlation
    # id ingest used — one email, ingest -> send, under one id. (gmail.send returns the
    # new OUTBOUND id, logged as a field, not used as the correlation key.)
    with bind_correlation_id(email.gmail_message_id):
        subject = f"Re: {email.subject}" if email.subject else "Re: your rate request"

        # TX-A: claim + audit (atomic). Already-sent → 409, no double-send.
        with repo.begin() as conn:
            claim = repo.claim_send(
                conn,
                quote_id=quote_id,
                deal_id=deal.id,
                to_email=email.sender,
                subject=subject,
                body=body,
                created_by=reviewer.uid,
            )
            if claim.status == "sent":
                raise SendError(409, "quote already sent")
            repo.insert_audit(
                conn,
                actor=reviewer.uid,
                actor_email=reviewer.email,
                action="email.send.claimed",
                entity_type="deals",
                entity_id=deal.id,
                detail={"quote_id": quote_id},
            )

        # Gmail send AFTER the claim commits (failure → 502, claim stays recoverable).
        try:
            gmail_message_id = gmail.send(
                OutboundMessage(
                    to=email.sender,
                    subject=subject,
                    body=body,
                    in_reply_to=email.gmail_message_id,
                    # Marker for future send dedup (the at-least-once window). A retry
                    # can later check the mailbox for this marker before re-sending.
                    headers={"X-Freight-Quote-Id": quote_id},
                )
            )
        except Exception as exc:
            raise SendError(502, "gmail send failed; retry to resume") from exc

        # TX-B: mark sent + audit.
        with repo.begin() as conn:
            repo.mark_sent(conn, send_id=claim.id, gmail_message_id=gmail_message_id)
            repo.insert_audit(
                conn,
                actor=reviewer.uid,
                actor_email=reviewer.email,
                action="email.sent",
                entity_type="deals",
                entity_id=deal.id,
                detail={"quote_id": quote_id, "gmail_message_id": gmail_message_id},
            )
        logger.info("quote sent", extra={"quote_id": quote_id})
        return SendResult(send_id=claim.id, gmail_message_id=gmail_message_id)
