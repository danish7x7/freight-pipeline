"""Phase 9 — rate-engine route-sensitivity sanity (DB-backed measurement instrument).

Prices the corpus lanes through the REAL route-aware engine (``price_per_mile`` /
``price_drayage``) against the live local DB's seeded effective-dated
``pricing_components``, read-only. Emits the measured all-in totals AND the per-line
decomposition (linehaul / deadhead / margin / fuel_surcharge / accessorials) so the
flat-$2,200 death is visible in real numbers and deadhead scales with miles per lane.

The route-sensitivity PROPERTY and the exact figures are guarded by
``tests/test_pricing.py`` (``test_pricing_is_route_sensitive`` +
``test_per_mile_total_matches_seeded_components``) — cited here, NOT duplicated. This is
a presentation/measurement instrument; ``tests/test_eval_rates.py`` covers only its pure
helpers (formatting, lane roster).

Run: ``uv run python scripts/eval_rates.py [--json eval/rates.json]``. The DSN comes
from ``INGEST_TEST_DSN`` (default the local Supabase stack on :54322). Fails LOUDLY if
the DB is unreachable or a required component is missing (``PricingConfigError``
propagates) — no silent fallback.
"""

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, RateKey, make_engine
from freight.rates import PricedQuote, price_drayage, price_per_mile, road_miles

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


@dataclass(frozen=True)
class Lane:
    """A labeled lane to price (the key drives the real engine)."""

    label: str
    key: RateKey


# Corpus lanes at CONSTANT equipment (dry_van) — same per-mile cost / bps, only miles
# differ, so different totals prove route-sensitivity (not a flat fallback).
_PER_MILE_LANES: tuple[Lane, ...] = (
    Lane(
        "Chicago, IL -> Dallas, TX",
        RateKey("Chicago", "IL", "Dallas", "TX", "dry_van"),
    ),
    Lane(
        "Atlanta, GA -> Miami, FL",
        RateKey("Atlanta", "GA", "Miami", "FL", "dry_van"),
    ),
    Lane(
        "Newark, NJ -> Boston, MA",
        RateKey("Newark", "NJ", "Boston", "MA", "dry_van"),
    ),
)


@dataclass(frozen=True)
class PricedLane:
    """One lane priced through the real engine."""

    label: str
    equipment: str
    miles: int | None  # None for the flat drayage model
    priced: PricedQuote


# --------------------------------------------------------------------------- pure
def dollars(cents: int) -> str:
    """Format integer cents as a USD string, e.g. ``251748 -> "$2,517.48"``."""
    return f"${cents / 100:,.2f}"


def per_mile_lanes() -> tuple[Lane, ...]:
    """The dry_van lane roster (pure; used by the hermetic on-table-roster test)."""
    return _PER_MILE_LANES


def lines_by_role(priced: PricedQuote) -> dict[str, int]:
    """Map each pinned line's role to its amount in cents (roles are distinct)."""
    return {line.role: line.line_amount_cents for line in priced.lines}


# --------------------------------------------------------------- DB-backed pricing
def price_corpus_lanes(conn: Connection, repo: IngestRepository) -> list[PricedLane]:
    """Price the dry_van lanes (per-mile) + a container row (drayage), read-only."""
    out: list[PricedLane] = []
    for lane in _PER_MILE_LANES:
        miles = road_miles(lane.key)
        if miles is None:  # instrument misconfig — fail loud, never invent a distance
            raise SystemExit(f"lane off-table (instrument bug): {lane.label}")
        priced = price_per_mile(conn, repo, key=lane.key, miles=miles, accessorials=[])
        out.append(PricedLane(lane.label, lane.key.equipment, miles, priced))
    drayage = price_drayage(conn, repo, accessorials=[])
    out.append(PricedLane("container drayage (flat)", "container", None, drayage))
    return out


# --------------------------------------------------------------------- rendering
def render(lanes: list[PricedLane], date: str) -> str:
    out: list[str] = []
    out.append("# Phase 9 — rate-engine route-sensitivity (measured)")
    out.append("")
    out.append(f"- measured on: {date}")
    out.append("- source: live local Supabase, seeded pricing_components (read-only)")
    out.append(
        "- property + exact figures guarded by tests/test_pricing.py (not duplicated)"
    )
    out.append("")

    out.append("## Per-mile route-sensitivity (equipment = dry_van)")
    out.append("| lane | miles | linehaul | deadhead | margin | FSC | all-in |")
    out.append("|---|---|---|---|---|---|---|")
    for pl in (p for p in lanes if p.miles is not None):
        r = lines_by_role(pl.priced)
        out.append(
            f"| {pl.label} | {pl.miles} | {dollars(r['linehaul'])} | "
            f"{dollars(r['deadhead'])} | {dollars(r['margin'])} | "
            f"{dollars(r['fuel_surcharge'])} | **{dollars(pl.priced.amount_cents)}** |"
        )
    out.append("")
    out.append(
        "Same equipment, different lanes → different totals; deadhead scales with "
        "miles. The flat $2,200 is structurally impossible."
    )
    out.append("")

    drayage = [p for p in lanes if p.miles is None]
    if drayage:
        out.append("## Drayage (equipment = container, flat model)")
        out.append("| model | drayage_base | FSC | all-in |")
        out.append("|---|---|---|---|")
        for pl in drayage:
            r = lines_by_role(pl.priced)
            out.append(
                f"| {pl.label} | {dollars(r['drayage_base'])} | "
                f"{dollars(r['fuel_surcharge'])} | "
                f"**{dollars(pl.priced.amount_cents)}** |"
            )
        out.append("")

    out.append("## Per-line breakdown (sum == all-in, no drift)")
    for pl in lanes:
        parts = ", ".join(
            f"{role}={dollars(amt)}" for role, amt in lines_by_role(pl.priced).items()
        )
        line_sum = sum(line.line_amount_cents for line in pl.priced.lines)
        check = "✓" if line_sum == pl.priced.amount_cents else "✗ MISMATCH"
        out.append(
            f"- {pl.label}: {parts} -> {dollars(pl.priced.amount_cents)} {check}"
        )
    return "\n".join(out)


def write_json(path: str, lanes: list[PricedLane], date: str) -> None:
    payload = {
        "meta": {"date": date, "source": "live local Supabase (INGEST_TEST_DSN)"},
        "lanes": [
            {
                "label": pl.label,
                "equipment": pl.equipment,
                "miles": pl.miles,
                "total_cents": pl.priced.amount_cents,
                "currency": pl.priced.currency,
                "lines": lines_by_role(pl.priced),
            }
            for pl in lanes
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\n(wrote {path})")


def _dsn() -> str:
    return os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN)


def run(json_path: str | None) -> None:
    date = datetime.now(UTC).date().isoformat()
    engine = make_engine(_dsn())
    try:
        with engine.connect() as conn:
            priced = price_corpus_lanes(conn, IngestRepository(engine))
    except OperationalError as exc:
        raise SystemExit(
            f"local DB not reachable (set INGEST_TEST_DSN): {exc}"
        ) from exc
    finally:
        engine.dispose()
    print(render(priced, date))
    if json_path is not None:
        write_json(json_path, priced, date)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 9 rate route-sensitivity.")
    parser.add_argument("--json", help="optional path to write the measured JSON")
    args = parser.parse_args()
    run(args.json)


if __name__ == "__main__":
    main()
