"""Fuel-surcharge update: append a new contracted rate version per lane.

Re-versions the CURRENT contracted rate of each lane by a surcharge delta — always an
INSERT (rates is append-only; the forbid_mutation trigger blocks overwrites). After each
insert the lane's cache is invalidated (only contracted inserts invalidate — the 4.5
coupling). Runs on the caller's Connection (the route owns the transaction; tests roll
back so the appended rows don't persist).
"""

from typing import Protocol

from sqlalchemy.engine import Connection

from freight.db.repository import IngestRepository, RateKey


class _CacheInvalidator(Protocol):
    def invalidate(self, key: RateKey) -> None: ...


def run_surcharge_update(
    conn: Connection,
    repo: IngestRepository,
    cache: _CacheInvalidator,
    *,
    delta_cents: int,
) -> int:
    """Append a surcharged contracted version per current lane; return the count."""
    lanes = repo.list_contracted_lanes()
    for lane in lanes:
        key = RateKey(
            origin_city=lane.origin_city,
            origin_state=lane.origin_state,
            dest_city=lane.dest_city,
            dest_state=lane.dest_state,
            equipment=lane.equipment,
        )
        repo.insert_rate_version(
            conn,
            key=key,
            source="contracted",
            amount_cents=lane.amount_cents + delta_cents,
            currency=lane.currency,
            carrier_id=lane.carrier_id,
        )
        cache.invalidate(key)
    return len(lanes)
