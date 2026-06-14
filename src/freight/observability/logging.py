"""Structured JSON logging + a correlation id threaded ingest -> send (Phase 7.1).

Dependency-free: a small ``logging.Formatter`` emitting one JSON object per record, plus
a ``contextvars``-bound correlation id injected onto every record. The correlation id is
the ``gmail_message_id`` — the same key that idempotently identifies one inbound email —
so a single email can be traced end to end (ingest -> extract -> rate -> finalize, and
the human send) by grepping one id.

WHY contextvars: the id is set once at the top of a unit of work (a queue delivery, a
poll of one message, a reviewer send) and every log line emitted underneath inherits it
without being passed through every call. ``bind_correlation_id`` is a context manager
that resets the previous value on exit, so ids never leak across messages (or across
async tasks — contextvars are per-task).
"""

import datetime as dt
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# The current unit-of-work correlation id (None outside any bound block).
correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Standard LogRecord attributes — anything NOT in here is treated as caller-supplied
# ``extra`` and merged into the JSON output.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


@contextmanager
def bind_correlation_id(value: str) -> Iterator[None]:
    """Bind ``value`` as the correlation id for the duration of the block."""
    token = correlation_id.set(value)
    try:
        yield
    finally:
        correlation_id.reset(token)


class CorrelationIdFilter(logging.Filter):
    """Stamp the current correlation id onto every record (None if unbound)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render a record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": dt.datetime.fromtimestamp(
                record.created, tz=dt.UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Merge any structured extras (logger.info(..., extra={...})).
        for key, val in record.__dict__.items():
            if key not in _RESERVED and key != "correlation_id":
                payload[key] = val
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler + correlation filter on the root logger.

    Idempotent: re-installs our single handler rather than stacking duplicates, so
    calling it from both the API app factory and the worker entrypoint is safe.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationIdFilter())
    handler.set_name("freight-json")

    root = logging.getLogger()
    root.setLevel(level.upper())
    # Drop any prior freight handler so repeated calls don't duplicate lines.
    for existing in list(root.handlers):
        if existing.get_name() == "freight-json":
            root.removeHandler(existing)
    root.addHandler(handler)
