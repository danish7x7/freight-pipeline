"""Rate engine: contracted (anchor) vs route-aware computed quote with pinned lines.

Integration tests run inside a rolled-back transaction: rates/quote_components are
append-only (the forbid_mutation trigger blocks DELETE), so the test's rows never
persist.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, RateKey, make_engine
from freight.rates import assess_quotability, quote_for
from freight.rates.engine import QuotePlan

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
SEEDED_DEAL = "d2222222-2222-2222-2222-222222222222"

_CHI_DAL = RateKey(
    origin_city="Chicago", origin_state="IL", dest_city="Dallas", dest_state="TX",
    equipment="dry_van",
)
_ATL_MIA = RateKey(
    origin_city="Atlanta", origin_state="GA", dest_city="Miami", dest_state="FL",
    equipment="dry_van",
)
_CONTAINER = RateKey(
    origin_city="Newark", origin_state="NJ", dest_city="Boston", dest_state="MA",
    equipment="container",
)


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
def test_contracted_lane_produces_contracted_quote(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            contracted = repo.current_contracted_rate(_CHI_DAL)
            assert contracted is not None
            result = quote_for(
                conn, repo, deal_id=SEEDED_DEAL, key=_CHI_DAL,
                contracted_rate=contracted,
            )
            assert result.is_computed is False
            assert result.amount_cents == 125000
            assert result.rate_id == contracted.id  # pins the contracted version
            # Contracted path writes no breakdown (it's an all-in pinned rate).
            n = conn.execute(
                text("select count(*) from quote_components where quote_id = :q"),
                {"q": result.quote_id},
            ).scalar_one()
            assert n == 0
        finally:
            trans.rollback()


@pytest.mark.integration
def test_computed_quote_pins_anchor_and_components(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            plan = assess_quotability(_ATL_MIA)
            assert plan == QuotePlan(model="per_mile", miles=665)
            result = quote_for(
                conn, repo, deal_id=SEEDED_DEAL, key=_ATL_MIA,
                contracted_rate=None, plan=plan, accessorials=["detention"],
            )
            assert result.is_computed is True
            assert result.amount_cents == 180985 + 7500  # per-mile + detention flat
            # anchor: a source='computed' rates row was materialized + pinned
            source = conn.execute(
                text("select source from rates where id = :id"),
                {"id": result.rate_id},
            ).scalar_one()
            assert source == "computed"
            # breakdown: each line pins a pricing_components row; lines sum to total
            rows = conn.execute(
                text(
                    "select role, line_amount_cents, pricing_component_id "
                    "from quote_components where quote_id = :q"
                ),
                {"q": result.quote_id},
            ).mappings().all()
            assert {r["role"] for r in rows} == {
                "linehaul", "deadhead", "margin", "fuel_surcharge",
                "accessorial:detention",
            }
            assert sum(r["line_amount_cents"] for r in rows) == result.amount_cents
            assert all(r["pricing_component_id"] is not None for r in rows)
        finally:
            trans.rollback()


@pytest.mark.integration
def test_computed_quote_is_route_sensitive(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            chi_plan = assess_quotability(_CHI_DAL)
            atl_plan = assess_quotability(_ATL_MIA)
            assert isinstance(chi_plan, QuotePlan)
            assert isinstance(atl_plan, QuotePlan)
            chi = quote_for(
                conn, repo, deal_id=SEEDED_DEAL, key=_CHI_DAL,
                contracted_rate=None, plan=chi_plan, accessorials=[],
            )
            atl = quote_for(
                conn, repo, deal_id=SEEDED_DEAL, key=_ATL_MIA,
                contracted_rate=None, plan=atl_plan, accessorials=[],
            )
            assert chi.amount_cents == 251748
            assert atl.amount_cents == 180985
            assert chi.amount_cents != atl.amount_cents  # flat-rate bug is dead
        finally:
            trans.rollback()


@pytest.mark.integration
def test_container_uses_flat_drayage_model(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            plan = assess_quotability(_CONTAINER)
            assert plan == QuotePlan(model="drayage", miles=None)
            result = quote_for(
                conn, repo, deal_id=SEEDED_DEAL, key=_CONTAINER,
                contracted_rate=None, plan=plan, accessorials=[],
            )
            assert result.is_computed is True
            assert result.amount_cents == 54000  # $450 base + $90 fsc, no miles
            roles = conn.execute(
                text("select role from quote_components where quote_id = :q"),
                {"q": result.quote_id},
            ).scalars().all()
            assert set(roles) == {"drayage_base", "fuel_surcharge"}
        finally:
            trans.rollback()


@pytest.mark.integration
def test_new_effective_version_repins_without_touching_old_quotes(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    # Effective-dating: append a NEW per_mile_cost version, then a new quote pins the
    # NEW version (higher total) while the prior quote keeps its OLD pinned amount.
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            plan = assess_quotability(_ATL_MIA)
            assert isinstance(plan, QuotePlan)
            before = quote_for(
                conn, repo, deal_id=SEEDED_DEAL, key=_ATL_MIA,
                contracted_rate=None, plan=plan, accessorials=[],
            )
            # New effective-dated dry_van per_mile_cost (append-only insert).
            conn.execute(
                text(
                    "insert into pricing_components "
                    "(component_type, equipment, value_cents, effective_from) "
                    "values ('per_mile_cost', 'dry_van', 360, now())"
                )
            )
            after = quote_for(
                conn, repo, deal_id=SEEDED_DEAL, key=_ATL_MIA,
                contracted_rate=None, plan=plan, accessorials=[],
            )
            assert after.amount_cents > before.amount_cents  # new version applied
            # the earlier quote's snapshotted amount is unchanged
            old_amount = conn.execute(
                text("select amount_cents from quotes where id = :q"),
                {"q": before.quote_id},
            ).scalar_one()
            assert old_amount == before.amount_cents == 180985
        finally:
            trans.rollback()
