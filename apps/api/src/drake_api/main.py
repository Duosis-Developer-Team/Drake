"""Application factory for the Drake control plane API."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from drake_api.audit.router import router as audit_router
from drake_api.auth.flows import AuthFlows
from drake_api.auth.oidc import OidcClient
from drake_api.auth.router import router as auth_router
from drake_api.auth.sessions import SessionStore
from drake_api.catalog.router import router as catalog_router
from drake_api.correlation import CorrelationIdMiddleware
from drake_api.db import dispose_engines, get_engine
from drake_api.errors import register_error_handlers
from drake_api.health import router as health_router
from drake_api.integrations.router import router as integrations_router
from drake_api.logging import configure_logging
from drake_api.rbac.options_router import router as rbac_options_router
from drake_api.rbac.router import router as rbac_router
from drake_api.settings import Settings, get_settings
from drake_api.telemetry.broker import TelemetryBroker
from drake_api.telemetry.metrics import BrokerMetrics
from drake_api.telemetry.provider import PrometheusAdapter
from drake_api.telemetry.registry import get_registry
from drake_api.telemetry.router import internal_router as telemetry_internal_router
from drake_api.telemetry.router import router as telemetry_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.session_store.aclose()
    await app.state.oidc_client.aclose()
    await app.state.telemetry_redis.aclose()
    await dispose_engines()


def create_app(
    settings: Settings | None = None,
    oidc_client: OidcClient | None = None,
    telemetry_transport: "httpx.AsyncBaseTransport | None" = None,
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

    # Telemetry runtime: the registry loads fail-closed at startup — a
    # malformed registry refuses to boot rather than serving loosely.
    app.state.telemetry_registry = get_registry()
    app.state.telemetry_metrics = BrokerMetrics()
    app.state.telemetry_redis = aioredis.from_url(settings.redis_url)
    app.state.telemetry_broker = TelemetryBroker(
        settings=settings,
        engine=get_engine(settings),
        redis=app.state.telemetry_redis,
        registry=app.state.telemetry_registry,
        adapter=PrometheusAdapter(settings, transport=telemetry_transport),
        metrics=app.state.telemetry_metrics,
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(rbac_router)
    app.include_router(rbac_options_router)
    app.include_router(audit_router)
    app.include_router(catalog_router)
    app.include_router(integrations_router)
    app.include_router(telemetry_router)
    if settings.internal_metrics_enabled and settings.env in ("local", "test"):
        # Explicit local/test opt-in only; validate_runtime_security refuses
        # the flag outside local/test, so this cannot register elsewhere.
        app.include_router(telemetry_internal_router)
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
