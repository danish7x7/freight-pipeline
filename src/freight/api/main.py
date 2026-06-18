"""FastAPI app factory. Route handlers stay thin — no business logic here."""

import logging
from typing import Annotated, Literal

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import SQLAlchemyError

from freight import __version__
from freight.api.routes.ingest import router as ingest_router
from freight.api.routes.poll import router as poll_router
from freight.api.routes.review import router as review_router
from freight.api.routes.surcharge import router as surcharge_router
from freight.config import get_settings
from freight.db.repository import IngestRepository, get_engine
from freight.observability import configure_logging
from freight.observability.metrics import refresh_db_gauges
from freight.observability.readiness import ReadinessReport, check_readiness
from freight.security.cors import configure_cors

logger = logging.getLogger("freight.api")


def get_readiness_report() -> ReadinessReport:
    """Probe the hard (DB) and soft (Redis) deps. Overridden in tests."""
    settings = get_settings()
    return check_readiness(get_engine(settings.database_url), settings.redis_url)


def refresh_gauges_from_db() -> None:
    """Set the DB-derived gauges to their real current values (called at scrape time).

    Resilient: a DB error leaves the gauges at their last value and /metrics still
    serves the in-memory counters. Overridden in tests.
    """
    settings = get_settings()
    repo = IngestRepository(get_engine(settings.database_url))
    try:
        refresh_db_gauges(repo.count_ingest_backlog(), repo.count_sends_claimed())
    except SQLAlchemyError as exc:
        logger.warning("metrics: DB gauge refresh failed, counters only: %s", exc)


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="freight-pipeline", version=__version__)
    configure_cors(app, settings)

    @app.get("/health")
    async def health() -> dict[str, Literal["ok"]]:
        """Liveness: the process is up and serving (no dependency checks)."""
        return {"status": "ok"}

    @app.get("/ready")
    def ready(
        report: Annotated[ReadinessReport, Depends(get_readiness_report)],
    ) -> JSONResponse:
        """Readiness: can the process work? DB down → 503; Redis down → degraded/200."""
        return JSONResponse(status_code=report.http_status, content=report.to_dict())

    @app.get("/metrics")
    def metrics(
        _refresh: Annotated[None, Depends(refresh_gauges_from_db)],
    ) -> Response:
        """Prometheus scrape: refresh the DB gauges, then serialize all metrics."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(ingest_router)
    app.include_router(poll_router)
    app.include_router(surcharge_router)
    app.include_router(review_router)
    return app


app = create_app()
