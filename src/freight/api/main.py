"""FastAPI app factory. Route handlers stay thin — no business logic here."""

from typing import Literal

from fastapi import FastAPI

from freight import __version__


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(title="freight-pipeline", version=__version__)

    @app.get("/health")
    async def health() -> dict[str, Literal["ok"]]:
        return {"status": "ok"}

    return app


app = create_app()
