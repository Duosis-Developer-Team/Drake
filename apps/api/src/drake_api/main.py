"""Application factory for the Drake control plane API."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from drake_api.correlation import CorrelationIdMiddleware
from drake_api.db import dispose_engines
from drake_api.errors import register_error_handlers
from drake_api.health import router as health_router
from drake_api.logging import configure_logging
from drake_api.settings import Settings, get_settings


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engines()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(logging.INFO)

    app = FastAPI(
        title="Drake API",
        version="0.1.0",
        lifespan=_lifespan,
        # OpenAPI stays available in local/dev; revisit before any shared deploy.
        docs_url="/docs" if settings.env == "local" else None,
        redoc_url=None,
    )
    app.state.settings = settings

    app.add_middleware(CorrelationIdMiddleware)
    if settings.cors_origins:
        # CORS is deny-by-default: the middleware exists only when origins
        # are explicitly configured.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-Correlation-ID"],
        )

    register_error_handlers(app)
    app.include_router(health_router)
    return app


def run() -> None:
    """Local development entrypoint. Binds to localhost only by default."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "drake_api.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )


if __name__ == "__main__":
    run()
