"""Prometheus metrics (Phase 7.3) — instrumented at the SAME seams 7.1 binds the
correlation id, so logs and metrics line up.

Local gate: ``/metrics`` scrapes (Prometheus text format) and these counters MOVE when
the pipeline runs. The Grafana Cloud dashboard is Phase 8 (no hosted scraping here).

Metric design (mapped to the PLAN's four: queue depth, latency, acceptance rate, DLQ):
- **Counters / histogram** are PUSHED at the seams (consumer.handle, poller._publish,
  send_quote / reject_deal).
- **acceptance rate** is the HUMAN disposition at the gate — ``sent`` at /review/send vs
  ``rejected`` at /review/reject — NOT extraction confidence.
- **Gauges** are keyed to REAL state, never a fake depth number. The queue is
  push-based, so there is no depth to poll: ``backlog`` and ``sends_claimed_not_sent``
  are refreshed from actual DB rows at SCRAPE time (see ``refresh_db_gauges``);
  ``dlq_size`` is pushed by the LocalDispatcher on dead-letter/replay (Phase 8 wires the
  real QStash DLQ count).

Single low-volume process → module-level singletons on the default registry. No
multiprocess/pushgateway (over-engineering at this volume).
"""

import logging

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger("freight.observability.metrics")

# --- counters / histogram (pushed at the 7.1 seams) --------------------------------

INGEST_PROCESSED = Counter(
    "freight_ingest_processed_total",
    "Inbound messages processed by the consumer, by routing outcome.",
    ["status", "intent"],
)

INGEST_DURATION = Histogram(
    "freight_ingest_duration_seconds",
    "Wall-clock duration of one consumer.handle delivery.",
)

MESSAGES_PUBLISHED = Counter(
    "freight_messages_published_total",
    "Messages published to the queue by the poller (front door + sweep).",
)

REVIEW_DISPOSITIONS = Counter(
    "freight_review_dispositions_total",
    "Human reviewer dispositions at the gate (acceptance = sent / (sent+rejected)).",
    ["disposition"],  # "sent" | "rejected"
)

# --- gauges (keyed to real state) ---------------------------------------------------

INGEST_BACKLOG = Gauge(
    "freight_ingest_backlog",
    "Inbound emails not yet terminal (ingest_status in received/queued). DB-derived.",
)

SENDS_CLAIMED_NOT_SENT = Gauge(
    "freight_sends_claimed_not_sent",
    "Sends claimed but not yet sent (the at-least-once stuck window). DB-derived.",
)

DLQ_SIZE = Gauge(
    "freight_dlq_size",
    "Dead-lettered messages. Pushed by the local dispatcher; QStash DLQ at Phase 8.",
)


def refresh_db_gauges(backlog: int, claimed_not_sent: int) -> None:
    """Set the DB-derived gauges to their real current values (at scrape time)."""
    INGEST_BACKLOG.set(backlog)
    SENDS_CLAIMED_NOT_SENT.set(claimed_not_sent)
