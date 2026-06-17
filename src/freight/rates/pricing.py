"""Route-aware computed pricing — the engine that replaced the flat $2,200 formula.

Builds a quote from EFFECTIVE-DATED pricing_components (per-mile cost, margin, fuel
surcharge, deadhead, drayage base, accessorial fees), each read on the caller's finalize
transaction so the quote pins the version effective at quote time. Two models, selected
by equipment (see ``assess_quotability`` in ``engine``):

  per-mile (dry_van/reefer/flatbed/step_deck/power_only):
    linehaul        = road_miles * per_mile_cost
    deadhead        = linehaul   * deadhead_bps / 10_000     (deadhead-miles uplift)
    subtotal        = linehaul + deadhead
    margin          = subtotal   * margin_bps / 10_000
    fuel_surcharge  = subtotal   * fsc_bps    / 10_000       (separate line)
    + accessorials (flat per type)
  drayage (container):
    drayage_base (flat) + fuel_surcharge (on base) + accessorials (flat)

All amounts are integer cents (floor division on bps), so the all-in total is exactly
the sum of the pinned line amounts — no rounding drift. Route-sensitivity falls out of
``road_miles`` differing per lane.
"""

from dataclasses import dataclass

from sqlalchemy.engine import Connection

from freight.db.repository import IngestRepository, PricingComponent, RateKey

_PER_MILE_EQUIPMENT: frozenset[str] = frozenset(
    {"dry_van", "reefer", "flatbed", "step_deck", "power_only"}
)


@dataclass(frozen=True)
class QuoteLine:
    """One pinned line of a computed quote (the row written to quote_components)."""

    pricing_component_id: str
    role: str
    line_amount_cents: int


@dataclass(frozen=True)
class PricedQuote:
    """A computed quote: the all-in total plus the lines that produced it."""

    amount_cents: int
    currency: str
    lines: list[QuoteLine]


class PricingConfigError(RuntimeError):
    """A required effective-dated pricing component is missing (operator misconfig)."""


def _require(
    component: PricingComponent | None, description: str
) -> PricingComponent:
    if component is None:
        raise PricingConfigError(f"no effective pricing component for {description}")
    return component


def _cents(component: PricingComponent, description: str) -> int:
    if component.value_cents is None:
        raise PricingConfigError(f"{description} has no value_cents")
    return component.value_cents


def _bps(component: PricingComponent, description: str) -> int:
    if component.value_bps is None:
        raise PricingConfigError(f"{description} has no value_bps")
    return component.value_bps


def price_per_mile(
    conn: Connection,
    repo: IngestRepository,
    *,
    key: RateKey,
    miles: int,
    accessorials: list[str],
) -> PricedQuote:
    """Per-mile model: linehaul + deadhead + margin + FSC + accessorials."""
    per_mile = _require(
        repo.current_pricing_component(conn, "per_mile_cost", equipment=key.equipment),
        f"per_mile_cost[{key.equipment}]",
    )
    deadhead = _require(
        repo.current_pricing_component(conn, "deadhead"), "deadhead"
    )
    margin = _require(repo.current_pricing_component(conn, "margin"), "margin")
    fsc = _require(
        repo.current_pricing_component(conn, "fuel_surcharge"), "fuel_surcharge"
    )

    linehaul = miles * _cents(per_mile, "per_mile_cost")
    deadhead_amt = linehaul * _bps(deadhead, "deadhead") // 10_000
    subtotal = linehaul + deadhead_amt
    margin_amt = subtotal * _bps(margin, "margin") // 10_000
    fsc_amt = subtotal * _bps(fsc, "fuel_surcharge") // 10_000

    lines = [
        QuoteLine(per_mile.id, "linehaul", linehaul),
        QuoteLine(deadhead.id, "deadhead", deadhead_amt),
        QuoteLine(margin.id, "margin", margin_amt),
        QuoteLine(fsc.id, "fuel_surcharge", fsc_amt),
    ]
    base = subtotal + margin_amt + fsc_amt
    return _with_accessorials(conn, repo, base, lines, accessorials)


def price_drayage(
    conn: Connection,
    repo: IngestRepository,
    *,
    accessorials: list[str],
) -> PricedQuote:
    """Drayage model: flat base + FSC on the base + accessorials. No miles."""
    drayage = _require(
        repo.current_pricing_component(
            conn, "drayage_base", equipment="container"
        ),
        "drayage_base[container]",
    )
    fsc = _require(
        repo.current_pricing_component(conn, "fuel_surcharge"), "fuel_surcharge"
    )
    base_cents = _cents(drayage, "drayage_base")
    fsc_amt = base_cents * _bps(fsc, "fuel_surcharge") // 10_000
    lines = [
        QuoteLine(drayage.id, "drayage_base", base_cents),
        QuoteLine(fsc.id, "fuel_surcharge", fsc_amt),
    ]
    return _with_accessorials(conn, repo, base_cents + fsc_amt, lines, accessorials)


def _with_accessorials(
    conn: Connection,
    repo: IngestRepository,
    base: int,
    lines: list[QuoteLine],
    accessorials: list[str],
) -> PricedQuote:
    """Append a flat accessorial line per validated type; pin each to its current row.

    The type is the only thing the email contributed (it survived the allowlist gate);
    the amount comes solely from the effective-dated component.
    """
    total = base
    for acc in accessorials:
        component = _require(
            repo.current_pricing_component(conn, "accessorial", accessorial_type=acc),
            f"accessorial[{acc}]",
        )
        amount = _cents(component, f"accessorial[{acc}]")
        lines.append(QuoteLine(component.id, f"accessorial:{acc}", amount))
        total += amount
    return PricedQuote(amount_cents=total, currency="USD", lines=lines)
