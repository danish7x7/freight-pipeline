"""Hermetic tests for the rate route-sensitivity instrument's PURE helpers.

The DB-backed pricing math + the route-sensitivity property/figures are guarded by
``tests/test_pricing.py`` (the single source of that invariant — not duplicated here).
These cover only the script's presentation helpers and lane roster, with no DB.
"""

from scripts.eval_rates import dollars, per_mile_lanes

from freight.rates import road_miles


def test_dollars_formats_cents() -> None:
    assert dollars(251748) == "$2,517.48"
    assert dollars(180985) == "$1,809.85"
    assert dollars(54000) == "$540.00"
    assert dollars(0) == "$0.00"


def test_per_mile_lane_roster_is_on_table_dry_van() -> None:
    """Guard: every lane in the roster is dry_van AND on the committed lane table, so a
    typo'd lane can't silently drop from the report (road_miles is pure — no DB)."""
    lanes = per_mile_lanes()
    assert len(lanes) == 3
    for lane in lanes:
        assert lane.key.equipment == "dry_van", lane.label
        assert road_miles(lane.key) is not None, lane.label
