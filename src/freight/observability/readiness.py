"""Readiness probe (Phase 7.2) — distinct from the ``/health`` liveness check.

Liveness (``/health``) answers "is the process up and serving?" — restart it if not.
Readiness (``/ready``) answers "can the process actually do its job right now?" — gate
traffic on it.

Hard vs soft dependencies:
- **Postgres is HARD.** Without it the consumer can't claim/finalize and ``/review``
  can't serve, so DB-down ⇒ ``not_ready`` (HTTP 503): pull the instance out of rotation.
- **Redis is FAIL-OPEN** (idempotency pre-check, cache, rate limiter all degrade
  gracefully). Redis-down ⇒ ``degraded`` (HTTP 200): the instance keeps serving —
  degraded is not process-down.

HF / Gmail / QStash are deliberately NOT readiness gates: they are per-request with
their own transient/retry/DLQ handling, so a blip there must not pull the whole instance
out of rotation.

Probes are cheap and BOUNDED (a ``SELECT 1``; a Redis ``PING`` under the existing 1s
socket timeouts) so the probe itself can never hang.
"""

import logging
from typing import Literal

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("freight.observability.readiness")

CheckState = Literal["ok", "down"]
ReadyStatus = Literal["ready", "degraded", "not_ready"]


class ReadinessReport:
    """The outcome of a readiness probe + the HTTP status it maps to."""

    def __init__(self, database: CheckState, redis: CheckState) -> None:
        self.database = database
        self.redis = redis

    @property
    def status(self) -> ReadyStatus:
        if self.database == "down":
            return "not_ready"  # hard dependency — pull from rotation
        if self.redis == "down":
            return "degraded"  # soft dependency — still serving
        return "ready"

    @property
    def http_status(self) -> int:
        return 503 if self.status == "not_ready" else 200

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": {"database": self.database, "redis": self.redis},
        }


def _check_database(engine: Engine) -> CheckState:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except SQLAlchemyError as exc:
        logger.warning("readiness: database check failed: %s", exc)
        return "down"


def _check_redis(redis_url: str) -> CheckState:
    # Short timeouts so a Redis outage reports "down" fast, never stalling the probe.
    client: Redis = Redis.from_url(
        redis_url, socket_connect_timeout=1, socket_timeout=1
    )
    try:
        client.ping()
        return "ok"
    except RedisError as exc:
        logger.warning("readiness: redis check failed (fail-open, degraded): %s", exc)
        return "down"
    finally:
        client.close()


def check_readiness(engine: Engine, redis_url: str) -> ReadinessReport:
    """Probe the hard (DB) and soft (Redis) dependencies and report readiness."""
    return ReadinessReport(
        database=_check_database(engine), redis=_check_redis(redis_url)
    )
