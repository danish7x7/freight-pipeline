"""Contracted rate lookup: current version, carrier precedence, computed exclusion."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, RateKey, make_engine
from freight.rates import current_contracted_rate

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
ACME = "c1111111-1111-1111-1111-111111111111"

_CHI_DAL = RateKey(
    origin_city="Chicago",
    origin_state="IL",
    dest_city="Dallas",
    dest_state="TX",
    equipment="dry_van",
)
_ATL_MIA = RateKey(
    origin_city="Atlanta",
    origin_state="GA",
    dest_city="Miami",
    dest_state="FL",
    equipment="reefer",
)


@pytest.fixture
def repo() -> Iterator[IngestRepository]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    try:
        yield IngestRepository(engine)
    finally:
        engine.dispose()


def test_lane_generic_returns_current_version(repo: IngestRepository) -> None:
    # Two lane-generic versions (120000 / 125000); the newer is current. The computed
    # row (130000, newest effective_from) is excluded by the source='contracted' filter.
    rate = current_contracted_rate(repo, _CHI_DAL)
    assert rate is not None
    assert rate.amount_cents == 125000
    assert rate.source == "contracted"
    assert rate.carrier_id is None


def test_carrier_specific_wins(repo: IngestRepository) -> None:
    rate = current_contracted_rate(repo, _CHI_DAL, carrier_id=ACME)
    assert rate is not None
    assert rate.amount_cents == 118000  # Acme-specific beats the lane-generic 125000
    assert rate.carrier_id == ACME


def test_second_lane(repo: IngestRepository) -> None:
    rate = current_contracted_rate(repo, _ATL_MIA)
    assert rate is not None
    assert rate.amount_cents == 95000


def test_unknown_lane_returns_none(repo: IngestRepository) -> None:
    key = RateKey(
        origin_city="Nowhere",
        origin_state="ND",
        dest_city="Elsewhere",
        dest_state="SD",
        equipment="dry_van",
    )
    assert current_contracted_rate(repo, key) is None
