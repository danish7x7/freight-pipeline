"""Carrier MC eligibility gate: unit (fake repo) + integration (seeded carriers)."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy.exc import OperationalError

from freight.carriers import evaluate
from freight.db import CarrierRecord, IngestRepository, make_engine

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


class _FakeCarriers:
    def __init__(self, carrier: CarrierRecord | None) -> None:
        self._carrier = carrier

    def get_carrier_by_mc(self, mc_number: str) -> CarrierRecord | None:
        return self._carrier


def _carrier(status: str) -> CarrierRecord:
    return CarrierRecord(id="c1", mc_number="MC123456", status=status)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# unit
# --------------------------------------------------------------------------- #
def test_no_mc_is_eligible() -> None:
    decision = evaluate(None, _FakeCarriers(None))
    assert decision.eligibility == "eligible"
    decision_blank = evaluate("   ", _FakeCarriers(None))
    assert decision_blank.eligibility == "eligible"


def test_active_carrier_is_eligible() -> None:
    decision = evaluate("MC123456", _FakeCarriers(_carrier("active")))
    assert decision.eligibility == "eligible"
    assert decision.reason is None


def test_blocked_carrier_is_on_hold() -> None:
    decision = evaluate("MC999999", _FakeCarriers(_carrier("blocked")))
    assert decision.eligibility == "on_hold"
    assert decision.reason == "blocked_carrier"


def test_unknown_status_carrier_is_on_hold() -> None:
    decision = evaluate("MC123456", _FakeCarriers(_carrier("unknown")))
    assert decision.eligibility == "on_hold"
    assert decision.reason == "unknown_carrier"


def test_not_found_carrier_is_on_hold() -> None:
    decision = evaluate("MC000000", _FakeCarriers(None))
    assert decision.eligibility == "on_hold"
    assert decision.reason == "unknown_carrier"


# --------------------------------------------------------------------------- #
# integration (seeded carriers: MC123456 active, MC999999 blocked)
# --------------------------------------------------------------------------- #
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


@pytest.mark.integration
def test_seeded_carriers_resolve_correctly(repo: IngestRepository) -> None:
    assert evaluate("MC123456", repo).eligibility == "eligible"
    blocked = evaluate("MC999999", repo)
    assert blocked.eligibility == "on_hold"
    assert blocked.reason == "blocked_carrier"
    unknown = evaluate("MC000000", repo)
    assert unknown.eligibility == "on_hold"
    assert unknown.reason == "unknown_carrier"
