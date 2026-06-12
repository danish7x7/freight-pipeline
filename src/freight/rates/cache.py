"""Redis hot-route cache over the contracted rate lookup.

NON-AUTHORITATIVE and FAIL-OPEN (same discipline as the idempotency cache): any
RedisError sends the call straight to the DB lookup — never raises, never blocks.
Correctness comes from invalidation + the append-only DB; the short TTL only caps drift
if an invalidation is missed.

Key: ``rate:{OS}:{OC}:{DS}:{DC}:{EQ}:{carrier_id|_}`` — carrier_id trailing so a lane is
a clean prefix. ``invalidate`` SCANs the lane prefix and deletes every probe
(lane-generic plus each carrier-specific) for that lane.

INVALIDATION COUPLING: only CONTRACTED-version inserts invalidate (the 4.7 surcharge job
and any admin contracted insert). The engine's source='computed' materialization must
NOT invalidate — computed rows are excluded from the cached contracted lookup, so they
can't stale it. Do not wire invalidation into the computed path.
"""

import logging

from redis import Redis
from redis.exceptions import RedisError

from freight.db.repository import RateKey, RateRecord
from freight.rates.lookup import RateLookup

logger = logging.getLogger("freight.rates.cache")

DEFAULT_TTL_SECONDS = 60


def _key(key: RateKey, carrier_id: str | None) -> str:
    return (
        f"rate:{key.origin_state}:{key.origin_city}:"
        f"{key.dest_state}:{key.dest_city}:{key.equipment}:{carrier_id or '_'}"
    )


def _lane_prefix(key: RateKey) -> str:
    return (
        f"rate:{key.origin_state}:{key.origin_city}:"
        f"{key.dest_state}:{key.dest_city}:{key.equipment}:"
    )


class CachedRateLookup:
    """RateLookup wrapper that caches positive contracted rates in Redis."""

    def __init__(
        self, repo: RateLookup, client: Redis, *, ttl: int = DEFAULT_TTL_SECONDS
    ) -> None:
        self._repo = repo
        self._client = client
        self._ttl = ttl

    def current_contracted_rate(
        self, key: RateKey, carrier_id: str | None = None
    ) -> RateRecord | None:
        cache_key = _key(key, carrier_id)
        cached = self._read(cache_key)
        if cached is not None:
            return cached
        rate = self._repo.current_contracted_rate(key, carrier_id)
        if rate is not None:  # only positive results are cached
            self._write(cache_key, rate)
        return rate

    def invalidate(self, key: RateKey) -> None:
        """Clear every cached probe for a lane (call after a contracted insert)."""
        try:
            for cache_key in self._client.scan_iter(match=f"{_lane_prefix(key)}*"):
                self._client.delete(cache_key)
        except RedisError as exc:
            logger.warning("rate cache invalidate failed (TTL will heal): %s", exc)

    def _read(self, cache_key: str) -> RateRecord | None:
        try:
            raw = self._client.get(cache_key)
        except RedisError as exc:
            logger.warning("rate cache read failed, using DB: %s", exc)
            return None
        if raw is None:
            return None
        try:
            return RateRecord.model_validate_json(raw)
        except ValueError:
            return None

    def _write(self, cache_key: str, rate: RateRecord) -> None:
        try:
            self._client.set(cache_key, rate.model_dump_json(), ex=self._ttl)
        except RedisError as exc:
            logger.warning("rate cache write failed (non-fatal): %s", exc)
