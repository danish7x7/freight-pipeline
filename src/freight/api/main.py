"""FastAPI app factory. Route handlers stay thin — no business logic here."""

from typing import Literal

from fastapi import FastAPI

from freight import __version__
from freight.api.routes.ingest import router as ingest_router
from freight.api.routes.poll import router as poll_router
from freight.api.routes.review import router as review_router
from freight.api.routes.surcharge import router as surcharge_router
from freight.config import get_settings
from freight.security.cors import configure_cors


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(title="freight-pipeline", version=__version__)
    configure_cors(app, get_settings())

    @app.get("/health")
    async def health() -> dict[str, Literal["ok"]]:
        return {"status": "ok"}

    app.include_router(ingest_router)
    app.include_router(poll_router)
    app.include_router(surcharge_router)
    app.include_router(review_router)
    return app


app = create_app()
