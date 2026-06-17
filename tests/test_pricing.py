"""Route-aware pricing: model switch, per-mile/drayage math, route-sensitivity.

Integration tests run inside a rolled-back transaction (pricing_components is
append-only; the forbid_mutation trigger blocks DELETE), so nothing persists.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, RateKey, make_engine
from freight.rates import assess_quotability, price_drayage, price_per_mile
from freight.rates.engine import QuotePlan

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


def _key(equipment: str, oc: str, os_: str, dc: str, ds: str) -> RateKey:
    return RateKey(
        origin_city=oc, origin_state=os_, dest_city=dc, dest_state=ds,
        equipment=equipment,
    )


_CHI_DAL = _key("dry_van", "Chicago", "IL", "Dallas", "TX")  # 925 mi
_ATL_MIA = _key("dry_van", "Atlanta", "GA", "Miami", "FL")   # 665 mi


# --------------------------------------------------------------------------- #
# assess_quotability (pure: equipment switch + lane-table gate)
# --------------------------------------------------------------------------- #
def test_per_mile_equipment_on_table_lane_plans_per_mile() -> None:
    plan = assess_quotability(_CHI_DAL)
    assert plan == QuotePlan(model="per_mile", miles=925)


def test_container_plans_drayage_without_miles() -> None:
    plan = assess_quotability(_key("container", "Newark", "NJ", "Boston", "MA"))
    assert plan == QuotePlan(model="drayage", miles=None)


def test_off_table_lane_routes_to_review() -> None:
    assert assess_quotability(_key("dry_van", "Nowhere", "ND", "Elsewhere", "SD")) == (
        "lane_not_in_table"
    )


def test_unknown_equipment_model_routes_to_review() -> None:
    assert assess_quotability(_key("other", "Chicago", "IL", "Dallas", "TX")) == (
        "unknown_equipment_model"
    )
    # Missing equipment likewise has no determinable model.
    assert assess_quotability(_key("", "Chicago", "IL", "Dallas", "TX")) == (
        "unknown_equipment_model"
    )


# --------------------------------------------------------------------------- #
# pricing math (integration: reads the seeded effective-dated components)
# --------------------------------------------------------------------------- #
@pytest.fixture
def engine_repo() -> Iterator[tuple[Engine, IngestRepository]]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    try:
        yield engine, IngestRepository(engine)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_per_mile_total_matches_seeded_components(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            priced = price_per_mile(
                conn, repo, key=_CHI_DAL, miles=925, accessorials=[]
            )
            # 925*180=166500 linehaul; +12% deadhead=19980; subtotal=186480;
            # +15% margin=27972; +20% fsc=37296 => 251748.
            assert priced.amount_cents == 251748
            assert sum(line.line_amount_cents for line in priced.lines) == 251748
            roles = {line.role for line in priced.lines}
            assert roles == {"linehaul", "deadhead", "margin", "fuel_surcharge"}
        finally:
            trans.rollback()


@pytest.mark.integration
def test_pricing_is_route_sensitive(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    # THE flat-rate bug is dead: same equipment, different miles => different totals.
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            chi = price_per_mile(conn, repo, key=_CHI_DAL, miles=925, accessorials=[])
            atl = price_per_mile(conn, repo, key=_ATL_MIA, miles=665, accessorials=[])
            assert chi.amount_cents == 251748
            assert atl.amount_cents == 180985
            assert chi.amount_cents != atl.amount_cents
        finally:
            trans.rollback()


@pytest.mark.integration
def test_accessorials_add_pinned_flat_lines(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            base = price_per_mile(conn, repo, key=_ATL_MIA, miles=665, accessorials=[])
            withacc = price_per_mile(
                conn, repo, key=_ATL_MIA, miles=665,
                accessorials=["detention", "liftgate"],
            )
            # detention $75 + liftgate $50 added flat (not route-scaled).
            assert withacc.amount_cents == base.amount_cents + 7500 + 5000
            acc_lines = [
                line for line in withacc.lines if line.role.startswith("accessorial:")
            ]
            assert {line.role for line in acc_lines} == {
                "accessorial:detention", "accessorial:liftgate"
            }
        finally:
            trans.rollback()


@pytest.mark.integration
def test_drayage_is_flat_base_plus_fsc(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            priced = price_drayage(conn, repo, accessorials=[])
            # drayage_base $450 + 20% fsc ($90) = $540, no miles, no margin line.
            assert priced.amount_cents == 54000
            assert {line.role for line in priced.lines} == {
                "drayage_base", "fuel_surcharge"
            }
        finally:
            trans.rollback()
