"""Rate engine: contracted vs flagged-computed quotes; formula determinism.

Integration tests run inside a rolled-back transaction: rates is append-only (the
forbid_mutation trigger blocks DELETE), so we never persist the test's rate/quote rows.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, RateKey, make_engine
from freight.rates import compute_rate, quote_for

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
SEEDED_DEAL = "d2222222-2222-2222-2222-222222222222"

_CHI_DAL = RateKey(
    origin_city="Chicago",
    origin_state="IL",
    dest_city="Dallas",
    dest_state="TX",
    equipment="dry_van",
)
_NEW_LANE = RateKey(
    origin_city="Nowhere",
    origin_state="ND",
    dest_city="Elsewhere",
    dest_state="SD",
    equipment="dry_van",
)


# --------------------------------------------------------------------------- #
# formula (unit)
# --------------------------------------------------------------------------- #
def test_compute_rate_is_deterministic() -> None:
    first = compute_rate(_NEW_LANE)
    assert first == compute_rate(_NEW_LANE)
    # dry_van: 80000 + 800*150 + 20000
    assert first.amount_cents == 220000
    assert first.currency == "USD"


def test_compute_rate_varies_by_equipment() -> None:
    reefer = RateKey(
        origin_city="A", origin_state="IL", dest_city="B", dest_state="TX",
        equipment="reefer",
    )
    assert compute_rate(reefer).amount_cents != compute_rate(_NEW_LANE).amount_cents


# --------------------------------------------------------------------------- #
# engine (integration)
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
        finally:
            trans.rollback()


@pytest.mark.integration
def test_new_lane_produces_flagged_computed_quote(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            result = quote_for(
                conn, repo, deal_id=SEEDED_DEAL, key=_NEW_LANE, contracted_rate=None
            )
            assert result.is_computed is True
            assert result.amount_cents == 220000  # formula for dry_van
            source = conn.execute(
                text("select source from rates where id = :id"),
                {"id": result.rate_id},
            ).scalar_one()
            assert source == "computed"  # a computed row was materialized + pinned
        finally:
            trans.rollback()
