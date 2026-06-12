"""The atomic finalize step (service layer — orchestration lives ONLY here).

The consumer (transport) opens ``with repo.begin() as conn``, runs the pre-tx cached
contracted lookup (Redis out of the tx), and calls ``finalize(conn, ...)``; the context
manager commits/rolls back. ``finalize`` owns the dispatch/gate/quote logic; the repo is
dumb and conn-scoped.

Dispatch (only rate_request creates a deal in this phase):
- extraction needs_review        → flip needs_review (carry reason), no deal.
- processed, not rate_request    → flip needs_review('intent_not_yet_routable'), no deal
                                   (thread-linking is later-phase; do NOT silently mark
                                   these processed/handled).
- processed rate_request         → flip processed, create deal(new_enquiry), MC gate;
                                   blocked/unknown MC → on_hold, NO quote; else → quote
                                   (pin contracted or computed) → quoted.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Connection

from freight.carriers import evaluate
from freight.db.repository import IngestRepository, RateKey, RateRecord
from freight.deals.state_machine import DealState, advance
from freight.extraction import ExtractionOutcome
from freight.rates import quote_for


@dataclass(frozen=True)
class FinalizeResult:
    """Outcome of one finalize attempt."""

    won: bool
    deal_id: str | None
    deal_state: DealState | None
    quote_id: str | None


def rate_key_from(extracted: dict[str, Any]) -> RateKey:
    """Build the lane key from extracted fields (missing → '' → no contracted match)."""
    return RateKey(
        origin_city=str(extracted.get("origin_city") or ""),
        origin_state=str(extracted.get("origin_state") or ""),
        dest_city=str(extracted.get("dest_city") or ""),
        dest_state=str(extracted.get("dest_state") or ""),
        equipment=str(extracted.get("equipment") or ""),
    )


def finalize(
    conn: Connection,
    repo: IngestRepository,
    *,
    gmail_message_id: str,
    outcome: ExtractionOutcome,
    contracted_rate: RateRecord | None,
) -> FinalizeResult:
    """Run the process-once flip + deal/quote logic on the caller's transaction."""
    # Extraction-level review, or a not-yet-routable intent: flip + done, no deal.
    if outcome.status == "needs_review":
        won = repo.flip_if_queued(
            conn,
            gmail_message_id=gmail_message_id,
            intent=outcome.intent,
            confidence=outcome.confidence,
            extracted=outcome.extracted,
            status="needs_review",
            review_reason=outcome.review_reason,
        )
        return FinalizeResult(won=won, deal_id=None, deal_state=None, quote_id=None)

    if outcome.intent != "rate_request":
        won = repo.flip_if_queued(
            conn,
            gmail_message_id=gmail_message_id,
            intent=outcome.intent,
            confidence=outcome.confidence,
            extracted=outcome.extracted,
            status="needs_review",
            review_reason="intent_not_yet_routable",
        )
        return FinalizeResult(won=won, deal_id=None, deal_state=None, quote_id=None)

    # processed rate_request → claim, then create the deal in the SAME tx.
    won = repo.flip_if_queued(
        conn,
        gmail_message_id=gmail_message_id,
        intent=outcome.intent,
        confidence=outcome.confidence,
        extracted=outcome.extracted,
        status="processed",
        review_reason=None,
    )
    if not won:
        return FinalizeResult(won=False, deal_id=None, deal_state=None, quote_id=None)

    extracted = outcome.extracted or {}
    deal_id = repo.create_deal(conn, state="new_enquiry", extracted=extracted)
    repo.link_email(conn, gmail_message_id=gmail_message_id, deal_id=deal_id)

    # MC gate (runs before quoted). No MC / active → proceed; blocked/unknown → on_hold.
    mc = extracted.get("mc_number")
    decision = evaluate(mc if isinstance(mc, str) else None, repo)
    if decision.eligibility == "on_hold":
        held: DealState = advance("new_enquiry", "hold")
        repo.advance_deal(conn, deal_id=deal_id, state=held, held_from="new_enquiry")
        return FinalizeResult(
            won=True, deal_id=deal_id, deal_state=held, quote_id=None
        )

    quote = quote_for(
        conn,
        repo,
        deal_id=deal_id,
        key=rate_key_from(extracted),
        contracted_rate=contracted_rate,
    )
    quoted: DealState = advance("new_enquiry", "quote_sent")
    repo.advance_deal(conn, deal_id=deal_id, state=quoted)
    return FinalizeResult(
        won=True, deal_id=deal_id, deal_state=quoted, quote_id=quote.quote_id
    )
