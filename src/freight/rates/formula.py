"""Computed-rate fallback formula (transparent placeholder).

Deterministic and auditable, NO geocoding:
    amount = base_by_equipment + flat_mileage_assumption * per_mile + fuel_surcharge

This is a PLACEHOLDER the Phase 9 eval measures, not a tuned pricing model. SEAM: the
fuel surcharge is a module constant here; the Phase 4.7 cron writes new contracted rate
VERSIONS (it does not mutate this constant). Real distance/pricing is a future task.
"""

from dataclasses import dataclass

from freight.db.repository import RateKey

_BASE_BY_EQUIPMENT_CENTS: dict[str, int] = {
    "dry_van": 80_000,
    "reefer": 110_000,
    "flatbed": 95_000,
    "step_deck": 105_000,
    "power_only": 60_000,
    "other": 90_000,
}
_FLAT_MILES = 800  # flat mileage assumption (no geocoding)
_PER_MILE_CENTS = 150  # $1.50 / mile
_FUEL_SURCHARGE_CENTS = 20_000  # current surcharge input (see SEAM above)


@dataclass(frozen=True)
class ComputedRate:
    amount_cents: int
    currency: str


def compute_rate(key: RateKey) -> ComputedRate:
    """Compute a fallback rate for a lane with no contracted rate."""
    base = _BASE_BY_EQUIPMENT_CENTS.get(
        key.equipment, _BASE_BY_EQUIPMENT_CENTS["other"]
    )
    amount = base + _FLAT_MILES * _PER_MILE_CENTS + _FUEL_SURCHARGE_CENTS
    return ComputedRate(amount_cents=amount, currency="USD")
