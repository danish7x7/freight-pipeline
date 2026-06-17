"""The rate engine: contracted lookup, or route-aware computed quote (pinned).

``quote_for`` runs entirely on the caller's Connection (the caller owns the tx). 4.6
passes the finalize tx's connection so deal + computed rate + quote + the pinned
breakdown are one atomic unit.

The computed path replaced the old flat formula (route-blind $2,200): it now prices off
real lane miles times effective-dated pricing_components and pins each line to
``quote_components``. Whether a deal is quotable at all (on-table lane, known costing
model) is decided BEFORE deal creation by ``assess_quotability`` — an off-table lane or
an unknown-model equipment routes to needs_review, never a flat fallback.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.engine import Connection

from freight.db.repository import IngestRepository, RateKey, RateRecord
from freight.rates.lanes import road_miles
from freight.rates.pricing import (
    _PER_MILE_EQUIPMENT,
    price_drayage,
    price_per_mile,
)

QuoteModel = Literal["per_mile", "drayage"]


@dataclass(frozen=True)
class QuotePlan:
    """How a computed quote will be priced (decided before the deal is created)."""

    model: QuoteModel
    miles: int | None  # set for per_mile; None for drayage (flat base)


@dataclass(frozen=True)
class QuoteResult:
    """The quote the engine produced for a deal."""

    quote_id: str
    rate_id: str
    amount_cents: int
    is_computed: bool


def assess_quotability(key: RateKey) -> QuotePlan | str:
    """Return a QuotePlan for a computed quote, or a needs_review reason string.

    Equipment is the model switch (it is already allowlist-validated). 'container' →
    flat drayage (no miles). Per-mile equipment → needs an on-table lane; an off-table
    lane returns ``lane_not_in_table``. Any other equipment (incl. 'other'/missing) has
    no determinable costing model → ``unknown_equipment_model``. Both reasons route the
    deal to needs_review (the safe sink) — a deliberate safety posture, not a flat
    quote.
    """
    if key.equipment == "container":
        return QuotePlan(model="drayage", miles=None)
    if key.equipment in _PER_MILE_EQUIPMENT:
        miles = road_miles(key)
        if miles is None:
            return "lane_not_in_table"
        return QuotePlan(model="per_mile", miles=miles)
    return "unknown_equipment_model"


def quote_for(
    conn: Connection,
    repo: IngestRepository,
    *,
    deal_id: str,
    key: RateKey,
    contracted_rate: RateRecord | None,
    plan: QuotePlan | None = None,
    accessorials: list[str] | None = None,
) -> QuoteResult:
    """Quote a deal: pin the pre-fetched contracted rate, or build a computed quote.

    Contracted (``contracted_rate`` is not None): pin its single ``rate_id`` and
    snapshot its amount — UNCHANGED, no breakdown. Computed (``plan`` is provided):
    price off the plan, materialize a ``source='computed'`` anchor ``rates`` row (so
    ``quotes.rate_id`` stays the NOT NULL anchor and the UI's ``is_computed`` keeps
    working), pin the quote to it, then pin each priced line to ``quote_components``.
    """
    if contracted_rate is not None:
        quote_id = repo.insert_quote(
            conn,
            deal_id=deal_id,
            rate_id=contracted_rate.id,
            amount_cents=contracted_rate.amount_cents,
            currency=contracted_rate.currency,
            is_computed=False,
        )
        return QuoteResult(
            quote_id=quote_id,
            rate_id=contracted_rate.id,
            amount_cents=contracted_rate.amount_cents,
            is_computed=False,
        )

    if plan is None:  # defensive: finalize only calls the computed path with a plan
        raise ValueError("computed quote requires a QuotePlan")
    accessorials = accessorials or []
    if plan.model == "per_mile":
        assert plan.miles is not None  # per_mile plans always carry miles
        priced = price_per_mile(
            conn, repo, key=key, miles=plan.miles, accessorials=accessorials
        )
    else:
        priced = price_drayage(conn, repo, accessorials=accessorials)

    rate_id = repo.insert_rate_version(
        conn,
        key=key,
        source="computed",
        amount_cents=priced.amount_cents,
        currency=priced.currency,
    )
    quote_id = repo.insert_quote(
        conn,
        deal_id=deal_id,
        rate_id=rate_id,
        amount_cents=priced.amount_cents,
        currency=priced.currency,
        is_computed=True,
    )
    for line in priced.lines:
        repo.insert_quote_component(
            conn,
            quote_id=quote_id,
            deal_id=deal_id,
            pricing_component_id=line.pricing_component_id,
            role=line.role,
            line_amount_cents=line.line_amount_cents,
        )
    return QuoteResult(
        quote_id=quote_id,
        rate_id=rate_id,
        amount_cents=priced.amount_cents,
        is_computed=True,
    )
