"""Observability seams (Phase 7): structured logging + correlation id."""

from freight.observability.logging import (
    bind_correlation_id,
    configure_logging,
    correlation_id,
)

__all__ = ["bind_correlation_id", "configure_logging", "correlation_id"]
