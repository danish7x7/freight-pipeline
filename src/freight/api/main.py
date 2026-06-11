"""FastAPI app factory. Route handlers stay thin — no business logic here."""

from typing import Literal

from fastapi import FastAPI

from freight import __version__
from freight.api.routes.ingest import router as ingest_router
from freight.api.routes.poll import router as poll_router


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(title="freight-pipeline", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, Literal["ok"]]:
        return {"status": "ok"}

    app.include_router(ingest_router)
    app.include_router(poll_router)
    return app


app = create_app()
