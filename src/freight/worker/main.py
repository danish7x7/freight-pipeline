"""Worker entrypoint. Queue consumption is wired in Phase 2; this is a stub."""

import logging

from freight.config import get_settings

logger = logging.getLogger("freight.worker")


def main() -> None:
    """Start the worker process (no queue wiring yet)."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info(
        "worker started (env=%s, queue_backend=%s)",
        settings.app_env,
        settings.queue_backend,
    )


if __name__ == "__main__":
    main()
