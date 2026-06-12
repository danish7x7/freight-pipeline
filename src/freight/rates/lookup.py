"""Contracted rate lookup (Model A).

Thin public API over the repository query. 4.5 wraps this with the Redis hot-route
cache; the underlying DB query stays authoritative.
"""

from typing import Protocol

from freight.db.repository import RateKey, RateRecord


class RateLookup(Protocol):
    def current_contracted_rate(
        self, key: RateKey, carrier_id: str | None = None
    ) -> RateRecord | None: ...


def current_contracted_rate(
    repo: RateLookup, key: RateKey, carrier_id: str | None = None
) -> RateRecord | None:
    """Return the current contracted rate for a lane key (carrier precedence)."""
    return repo.current_contracted_rate(key, carrier_id)
