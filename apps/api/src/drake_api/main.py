"""Application factory for the Drake control plane API."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from drake_api.audit.router import router as audit_router
from drake_api.auth.flows import AuthFlows
from drake_api.auth.oidc import OidcClient
from drake_api.auth.router import router as auth_router
from drake_api.auth.sessions import SessionStore
from drake_api.catalog.router import router as catalog_router
from drake_api.correlation import CorrelationIdMiddleware
from drake_api.db import dispose_engines
from drake_api.errors import register_error_handlers
from drake_api.health import router as health_router
from drake_api.integrations.router import router as integrations_router
from drake_api.logging import configure_logging
from drake_api.rbac.options_router import router as rbac_options_router
from drake_api.rbac.router import router as rbac_router
from drake_api.settings import Settings, get_settings


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.session_store.aclose()
    await app.state.oidc_client.aclose()
    await dispose_engines()


def create_app(
    settings: Settings | None = None,
    oidc_client: OidcClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    # Fail fast on insecure identity configuration outside local/test —
    # a fake/plaintext provider can never activate in a production-like env.
    settings.validate_runtime_security()
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

    app.state.session_store = SessionStore(settings)
    app.state.oidc_client = oidc_client or OidcClient(settings)
    app.state.auth_flows = AuthFlows(settings, app.state.oidc_client, app.state.session_store)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(rbac_router)
    app.include_router(rbac_options_router)
    app.include_router(audit_router)
    app.include_router(catalog_router)
    app.include_router(integrations_router)
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
