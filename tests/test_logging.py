"""Phase 7.1: structured JSON logs + the correlation id contextvar.

Hermetic — exercises the formatter/filter/contextvar directly via an in-memory handler;
no DB, no real pipeline.
"""

import json
import logging
from collections.abc import Iterator

import pytest

from freight.observability import bind_correlation_id, correlation_id
from freight.observability.logging import CorrelationIdFilter, JsonFormatter


class _CaptureHandler(logging.Handler):
    """Collects formatted log lines (already JSON strings)."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(JsonFormatter())
        self.addFilter(CorrelationIdFilter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def records(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.lines]


@pytest.fixture
def logger_and_handler() -> Iterator[tuple[logging.Logger, _CaptureHandler]]:
    logger = logging.getLogger("freight.test.logging")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = _CaptureHandler()
    logger.addHandler(handler)
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)


def test_record_is_valid_json_with_required_keys(
    logger_and_handler: tuple[logging.Logger, _CaptureHandler],
) -> None:
    logger, handler = logger_and_handler
    logger.info("hello")
    (rec,) = handler.records()
    assert set(rec) >= {"ts", "level", "logger", "msg", "correlation_id"}
    assert rec["level"] == "INFO"
    assert rec["logger"] == "freight.test.logging"
    assert rec["msg"] == "hello"


def test_correlation_id_absent_when_unbound(
    logger_and_handler: tuple[logging.Logger, _CaptureHandler],
) -> None:
    logger, handler = logger_and_handler
    logger.info("no id here")
    assert handler.records()[0]["correlation_id"] is None


def test_correlation_id_present_when_bound(
    logger_and_handler: tuple[logging.Logger, _CaptureHandler],
) -> None:
    logger, handler = logger_and_handler
    with bind_correlation_id("synthetic-0001"):
        logger.info("inside")
    assert handler.records()[0]["correlation_id"] == "synthetic-0001"


def test_contextvar_resets_after_block_no_leakage(
    logger_and_handler: tuple[logging.Logger, _CaptureHandler],
) -> None:
    logger, handler = logger_and_handler
    with bind_correlation_id("first"):
        logger.info("a")
    logger.info("b")  # outside the block → must not inherit "first"
    with bind_correlation_id("second"):
        logger.info("c")
    ids = [r["correlation_id"] for r in handler.records()]
    assert ids == ["first", None, "second"]
    assert correlation_id.get() is None  # fully reset


def test_extra_fields_are_merged(
    logger_and_handler: tuple[logging.Logger, _CaptureHandler],
) -> None:
    logger, handler = logger_and_handler
    logger.info("processed", extra={"status": "needs_review", "intent": "rc"})
    rec = handler.records()[0]
    assert rec["status"] == "needs_review"
    assert rec["intent"] == "rc"


def test_exception_is_captured(
    logger_and_handler: tuple[logging.Logger, _CaptureHandler],
) -> None:
    logger, handler = logger_and_handler
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed")
    rec = handler.records()[0]
    assert "boom" in str(rec["exc"])


def test_one_unit_of_work_threads_a_single_id(
    logger_and_handler: tuple[logging.Logger, _CaptureHandler],
) -> None:
    """All lines under one bound block share the id — the 'trace one email' property."""
    logger, handler = logger_and_handler
    with bind_correlation_id("synthetic-0042"):
        logger.info("ingest received")
        logger.info("extracted")
        logger.info("finalized")
    ids = {r["correlation_id"] for r in handler.records()}
    assert ids == {"synthetic-0042"}
