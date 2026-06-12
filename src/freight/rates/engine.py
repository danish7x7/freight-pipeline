"""The rate engine: contracted lookup, or computed fallback (materialized + pinned).

``quote_for`` runs entirely on the caller's Connection (the caller owns the tx). 4.6
passes the finalize tx's connection so deal + computed rate + quote are one atomic unit;
4.4 tests open their own ``engine.begin()``.
"""

from dataclasses import dataclass

from sqlalchemy.engine import Connection

from freight.db.repository import IngestRepository, RateKey, RateRecord
from freight.rates.formula import compute_rate


@dataclass(frozen=True)
class QuoteResult:
    """The quote the engine produced for a deal."""

    quote_id: str
    rate_id: str
    amount_cents: int
    is_computed: bool


def quote_for(
    conn: Connection,
    repo: IngestRepository,
    *,
    deal_id: str,
    key: RateKey,
    contracted_rate: RateRecord | None,
) -> QuoteResult:
    """Quote a deal: pin the pre-fetched contracted rate, or materialize a computed one.

    ``contracted_rate`` is fetched pre-tx by the caller (cached lookup, Redis out of the
    tx). The lookup is NOT repeated here.
    """
    if contracted_rate is not None:
        rate_id = contracted_rate.id
        amount_cents = contracted_rate.amount_cents
        currency = contracted_rate.currency
        is_computed = False
    else:
        computed = compute_rate(key)
        rate_id = repo.insert_rate_version(
            conn,
            key=key,
            source="computed",
            amount_cents=computed.amount_cents,
            currency=computed.currency,
        )
        amount_cents = computed.amount_cents
        currency = computed.currency
        is_computed = True

    quote_id = repo.insert_quote(
        conn,
        deal_id=deal_id,
        rate_id=rate_id,
        amount_cents=amount_cents,
        currency=currency,
        is_computed=is_computed,
    )
    return QuoteResult(
        quote_id=quote_id,
        rate_id=rate_id,
        amount_cents=amount_cents,
        is_computed=is_computed,
    )
