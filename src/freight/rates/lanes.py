"""Lane distance table — committed REAL road miles for known city pairs.

The PRIMARY (and only) distance source for the route-aware engine. Road miles are
geography, not a rate: they don't version and aren't pinned to a quote — a committed
static table is the right size. Values are approximate one-way driving miles for the
corpus's lanes (PC*Miler / mapping-service ballpark), documented here.

An off-table lane returns ``None`` → the engine routes the deal to needs_review
(reason ``lane_not_in_table``). There is deliberately NO geodesic/circuity fallback: a
made-up distance would quietly produce a quote off a guessed number and poison the eval
(see DECISIONS). A broader lane universe can be added behind this same function later.
"""

from freight.db.repository import RateKey

# Normalized key: (origin_city.lower, origin_state.upper, dest_city.lower,
# dest_state.upper). Stored one-way; reverse directions added programmatically below.
_ONE_WAY_MILES: dict[tuple[str, str, str, str], int] = {
    ("chicago", "IL", "dallas", "TX"): 925,
    ("atlanta", "GA", "miami", "FL"): 665,
    ("newark", "NJ", "boston", "MA"): 225,
}


def _with_reverse(
    table: dict[tuple[str, str, str, str], int],
) -> dict[tuple[str, str, str, str], int]:
    full: dict[tuple[str, str, str, str], int] = {}
    for (oc, os_, dc, ds), miles in table.items():
        full[(oc, os_, dc, ds)] = miles
        full.setdefault((dc, ds, oc, os_), miles)  # ~symmetric road miles
    return full


_LANE_MILES = _with_reverse(_ONE_WAY_MILES)


def road_miles(key: RateKey) -> int | None:
    """Return committed road miles for the lane, or None if off-table (→ review)."""
    if not (key.origin_city and key.origin_state and key.dest_city and key.dest_state):
        return None
    lookup = (
        key.origin_city.strip().lower(),
        key.origin_state.strip().upper(),
        key.dest_city.strip().lower(),
        key.dest_state.strip().upper(),
    )
    return _LANE_MILES.get(lookup)
