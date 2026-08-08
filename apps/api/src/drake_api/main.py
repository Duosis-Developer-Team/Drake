"""Application factory for the Drake control plane API."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from drake_api.agents.router_inventory import router as cluster_inventory_router
from drake_api.agents.router_tokens import router as agent_tokens_router
from drake_api.audit.router import router as audit_router
from drake_api.auth.flows import AuthFlows
from drake_api.auth.oidc import OidcClient
from drake_api.auth.router import router as auth_router
from drake_api.auth.sessions import SessionStore
from drake_api.catalog.router import router as catalog_router
from drake_api.correlation import CorrelationIdMiddleware
from drake_api.db import dispose_engines, get_engine
from drake_api.deployments.router import router as deployments_router
from drake_api.deployments.worker import DeploymentIngestWorker
from drake_api.errors import register_error_handlers
from drake_api.github_app.auth import GitHubAppAuth
from drake_api.github_app.auth import validate_credentials as validate_github_credentials
from drake_api.github_app.client import GitHubClient
from drake_api.github_app.onboarding_service import CatalogImporter, OnboardingScanner
from drake_api.github_app.router import router as github_router
from drake_api.github_app.router_onboarding import router as github_onboarding_router
from drake_api.github_app.router_webhook import router as github_webhook_router
from drake_api.github_app.service import DeliveryRecoveryWorker, GitHubReconciler
from drake_api.health import router as health_router
from drake_api.incidents.router import router as incidents_router
from drake_api.incidents.runner import EvaluationRunner
from drake_api.integrations.router import router as integrations_router
from drake_api.logging import configure_logging
from drake_api.notifications.router import router as notifications_router
from drake_api.notifications.worker import NotificationWorker
from drake_api.protection.router import router as protection_router
from drake_api.protection.router_ingest import router as protection_ingest_router
from drake_api.rbac.options_router import router as rbac_options_router
from drake_api.rbac.router import router as rbac_router
from drake_api.service_health.cache import HealthCache
from drake_api.service_health.orchestrator import HealthOrchestrator
from drake_api.service_health.router import router as service_health_router
from drake_api.settings import Settings, get_settings
from drake_api.telemetry.broker import TelemetryBroker
from drake_api.telemetry.metrics import BrokerMetrics
from drake_api.telemetry.provider import PrometheusAdapter
from drake_api.telemetry.registry import get_registry
from drake_api.telemetry.router import internal_router as telemetry_internal_router
from drake_api.telemetry.router import router as telemetry_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The recovery worker exists only when the integration is enabled: a
    # disabled feature starts no background task and touches no credential.
    worker = getattr(app.state, "github_recovery_worker", None)
    if worker is not None:
        await worker.start()
    # Same contract for the incident evaluator: absent unless enabled, so a
    # disabled feature issues no query and holds no lease.
    evaluator = getattr(app.state, "incident_runner", None)
    if evaluator is not None:
        await evaluator.start()
    # Planning and delivery each have their own flag; the worker exists
    # only if at least one of them is on.
    notifier = getattr(app.state, "notification_worker", None)
    if notifier is not None:
        await notifier.start()
    ingestor = getattr(app.state, "deployment_ingest_worker", None)
    if ingestor is not None:
        await ingestor.start()
    try:
        yield
    finally:
        if ingestor is not None:
            await ingestor.stop()
        if notifier is not None:
            await notifier.stop()
        if evaluator is not None:
            await evaluator.stop()
        if worker is not None:
            # Deterministic cancellation, so no task outlives the process.
            await worker.stop()
    await app.state.session_store.aclose()
    await app.state.oidc_client.aclose()
    await app.state.telemetry_redis.aclose()
    await dispose_engines()


def create_app(
    settings: Settings | None = None,
    oidc_client: OidcClient | None = None,
    telemetry_transport: "httpx.AsyncBaseTransport | None" = None,
    github_transport: "httpx.AsyncBaseTransport | None" = None,
    webhook_transport: "httpx.AsyncBaseTransport | None" = None,
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
    # GitHub App: credentials stay in process memory; only the reconciler
    # and its bounded client live on app.state.
    # An enabled GitHub App must prove its credentials are usable now, not
    # discover at the first webhook that the key never parsed.
    validate_github_credentials(settings)
    github_auth = GitHubAppAuth(settings)
    github_client = GitHubClient(settings, github_auth, transport=github_transport)
    app.state.github_auth = github_auth
    app.state.github_client = github_client
    github_reconciler = GitHubReconciler(get_engine(settings), github_client)
    app.state.github_reconciler = github_reconciler
    app.state.github_onboarding_scanner = OnboardingScanner(get_engine(settings), github_client)
    app.state.github_catalog_importer = CatalogImporter(get_engine(settings), github_client)
    app.state.github_recovery_worker = (
        DeliveryRecoveryWorker(
            get_engine(settings),
            poll_seconds=settings.github_recovery_poll_seconds,
            batch_size=settings.github_recovery_batch_size,
            reconciler=github_reconciler,
        )
        if settings.github_app_enabled
        else None
    )
    app.state.telemetry_broker = TelemetryBroker(
        settings=settings,
        engine=get_engine(settings),
        redis=app.state.telemetry_redis,
        registry=app.state.telemetry_registry,
        adapter=PrometheusAdapter(settings, transport=telemetry_transport),
        metrics=app.state.telemetry_metrics,
    )
    # The evaluator reuses the Sprint 5 read path exactly as the API does —
    # same orchestrator, same broker, same budgets — so a scheduled
    # evaluation and a screen refresh can never disagree about a service.
    app.state.health_orchestrator = HealthOrchestrator(
        get_engine(settings),
        app.state.telemetry_broker,
        app.state.telemetry_registry,
        HealthCache(app.state.telemetry_redis),
    )
    app.state.incident_runner = (
        EvaluationRunner(
            get_engine(settings),
            app.state.health_orchestrator,
            app.state.telemetry_redis,
            interval_seconds=settings.incident_runner_interval_seconds,
            batch_size=settings.incident_runner_batch_size,
            concurrency=settings.incident_runner_concurrency,
            lease_seconds=settings.incident_runner_lease_seconds,
        )
        if settings.incident_runner_enabled
        else None
    )
    app.state.notification_worker = (
        NotificationWorker(
            get_engine(settings),
            settings,
            app.state.telemetry_redis,
            transport=webhook_transport,
        )
        if (settings.notification_planner_enabled or settings.webhook_worker_enabled)
        else None
    )
    app.state.deployment_ingest_worker = (
        DeploymentIngestWorker(get_engine(settings), settings, app.state.telemetry_redis)
        if settings.deployment_ingest_enabled
        else None
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(rbac_router)
    app.include_router(rbac_options_router)
    app.include_router(audit_router)
    app.include_router(agent_tokens_router)
    app.include_router(cluster_inventory_router)
    app.include_router(catalog_router)
    app.include_router(integrations_router)
    app.include_router(github_router)
    app.include_router(github_onboarding_router)
    app.include_router(github_webhook_router)
    app.include_router(deployments_router)
    app.include_router(protection_router)
    app.include_router(protection_ingest_router)
    app.include_router(service_health_router)
    app.include_router(incidents_router)
    app.include_router(notifications_router)
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
