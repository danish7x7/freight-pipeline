"""FastAPI app factory. Route handlers stay thin — no business logic here."""

from typing import Annotated, Literal

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from freight import __version__
from freight.api.routes.ingest import router as ingest_router
from freight.api.routes.poll import router as poll_router
from freight.api.routes.review import router as review_router
from freight.api.routes.surcharge import router as surcharge_router
from freight.config import get_settings
from freight.db.repository import make_engine
from freight.observability import configure_logging
from freight.observability.readiness import ReadinessReport, check_readiness
from freight.security.cors import configure_cors


def get_readiness_report() -> ReadinessReport:
    """Probe the hard (DB) and soft (Redis) deps. Overridden in tests."""
    settings = get_settings()
    return check_readiness(make_engine(settings.database_url), settings.redis_url)


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

    app.include_router(ingest_router)
    app.include_router(poll_router)
    app.include_router(surcharge_router)
    app.include_router(review_router)
    return app


app = create_app()
