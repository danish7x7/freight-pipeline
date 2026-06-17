"""Lane distance table: known pairs return real miles; off-table → None (→ review)."""

from freight.db.repository import RateKey
from freight.rates.lanes import road_miles


def _key(oc: str, os_: str, dc: str, ds: str, equip: str = "dry_van") -> RateKey:
    return RateKey(
        origin_city=oc, origin_state=os_, dest_city=dc, dest_state=ds, equipment=equip
    )


def test_known_lane_returns_road_miles() -> None:
    assert road_miles(_key("Chicago", "IL", "Dallas", "TX")) == 925
    assert road_miles(_key("Atlanta", "GA", "Miami", "FL")) == 665
    assert road_miles(_key("Newark", "NJ", "Boston", "MA")) == 225


def test_lane_is_case_and_whitespace_insensitive() -> None:
    assert road_miles(_key("chicago", "il", "dallas", "tx")) == 925
    assert road_miles(_key("  Chicago ", "IL", "Dallas", "TX")) == 925


def test_reverse_direction_is_present() -> None:
    assert road_miles(_key("Dallas", "TX", "Chicago", "IL")) == 925


def test_off_table_lane_returns_none() -> None:
    # No flat fallback, no geodesic guess — None routes the deal to needs_review.
    assert road_miles(_key("Nowhere", "ND", "Elsewhere", "SD")) is None


def test_missing_lane_fields_return_none() -> None:
    assert road_miles(_key("Chicago", "IL", "", "")) is None
    assert road_miles(_key("", "", "Dallas", "TX")) is None
