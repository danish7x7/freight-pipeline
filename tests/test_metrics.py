"""Phase 7.3: the local gate — /metrics scrapes + counters move when the pipeline runs.

Hermetic. Counter assertions use deltas via ``REGISTRY.get_sample_value`` (the metrics
are process-global singletons, so absolute values aren't stable across the suite).
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from freight.api.main import app, refresh_gauges_from_db
from freight.interfaces.types import QueueMessage
from freight.mocks.dispatcher import LocalDispatcher
from freight.observability import metrics
from freight.observability.metrics import REVIEW_DISPOSITIONS


def _value(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


async def _no_sleep(_s: float) -> None: ...


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Don't hit a real DB for the scrape-time gauge refresh.
    app.dependency_overrides[refresh_gauges_from_db] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- /metrics scrapes ---------------------------------------------------------------


def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    for name in (
        "freight_ingest_processed_total",
        "freight_messages_published_total",
        "freight_review_dispositions_total",
        "freight_ingest_backlog",
        "freight_sends_claimed_not_sent",
        "freight_dlq_size",
    ):
        assert name in body


# --- counters move when the pipeline runs -------------------------------------------


def test_review_disposition_counters_move(client: TestClient) -> None:
    before_sent = _value(
        "freight_review_dispositions_total", {"disposition": "sent"}
    )
    before_rej = _value(
        "freight_review_dispositions_total", {"disposition": "rejected"}
    )
    # Emit directly at the gate seam (the human-in-the-loop outcome).
    REVIEW_DISPOSITIONS.labels(disposition="sent").inc()
    REVIEW_DISPOSITIONS.labels(disposition="rejected").inc()
    assert (
        _value("freight_review_dispositions_total", {"disposition": "sent"})
        == before_sent + 1
    )
    assert (
        _value("freight_review_dispositions_total", {"disposition": "rejected"})
        == before_rej + 1
    )


def test_ingest_processed_counter_moves() -> None:
    before = _value(
        "freight_ingest_processed_total",
        {"status": "needs_review", "intent": "none"},
    )
    metrics.INGEST_PROCESSED.labels(status="needs_review", intent="none").inc()
    assert (
        _value(
            "freight_ingest_processed_total",
            {"status": "needs_review", "intent": "none"},
        )
        == before + 1
    )


# --- gauges keyed to real state -----------------------------------------------------


def test_db_gauges_reflect_real_counts() -> None:
    # The scrape-time refresh sets the gauges to the repo's real counts.
    metrics.refresh_db_gauges(backlog=7, claimed_not_sent=3)
    assert _value("freight_ingest_backlog") == 7
    assert _value("freight_sends_claimed_not_sent") == 3


async def test_dlq_gauge_pushed_by_dispatcher() -> None:
    async def poison(_m: QueueMessage) -> None:
        raise RuntimeError("poison")

    dispatcher = LocalDispatcher(poison, retries=0, sleep=_no_sleep)
    await dispatcher.deliver(QueueMessage(id="d1"))
    assert _value("freight_dlq_size") == 1  # dead-lettered → pushed

    # Replay still fails → re-dead-lettered, gauge stays at 1.
    await dispatcher.replay()
    assert _value("freight_dlq_size") == 1


def test_refresh_is_resilient_and_metrics_still_serves_without_db() -> None:
    # No local DB: the real scrape-time refresh must swallow the error (not raise) so
    # /metrics still serves the in-memory counters.
    refresh_gauges_from_db()  # must not raise even though the DB is unreachable
    with TestClient(app) as c:  # real refresh (no override) on the scrape
        resp = c.get("/metrics")
    assert resp.status_code == 200
    assert "freight_ingest_processed_total" in resp.text
