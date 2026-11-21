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
from drake_api.alert_rules.router import router as alert_rules_router
from drake_api.alert_rules.telemetry import TelemetrySampler
from drake_api.alerting.router import router as alerting_router
from drake_api.alerting.router_webhook import router as alertmanager_webhook_router
from drake_api.alerting.silences import SilenceWorker
from drake_api.alerting.sli_rollups import SliRollupWriter
from drake_api.alerting.slo_service import SloEvaluator
from drake_api.audit.router import router as audit_router
from drake_api.auth.flows import AuthFlows
from drake_api.auth.oidc import OidcClient
from drake_api.auth.router import router as auth_router
from drake_api.auth.sessions import SessionStore
from drake_api.catalog.router import router as catalog_router
from drake_api.catalog.router_clusters import router as catalog_clusters_router
from drake_api.catalog.scheduled_jobs import router as scheduled_jobs_router
from drake_api.correlation import CorrelationIdMiddleware
from drake_api.dashboards.router import router as dashboards_router
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
from drake_api.infrastructure.router import router as infrastructure_router
from drake_api.infrastructure.service import InfrastructureService
from drake_api.integrations.router import router as integrations_router
from drake_api.logging import configure_logging
from drake_api.notifications.router import router as notifications_router
from drake_api.notifications.worker import NotificationWorker
from drake_api.observability.application import TraceGoldenReader
from drake_api.observability.overview_router import router as observability_overview_router
from drake_api.observability.router import router as observability_router
from drake_api.observability.store import ClickHouseStore
from drake_api.onboarding.github_provider import GitHubPullRequestProvider
from drake_api.onboarding.gitops import GitOpsWorker, RecordingProvider
from drake_api.onboarding.router import router as onboarding_router
from drake_api.protection.router import router as protection_router
from drake_api.protection.router_ingest import router as protection_ingest_router
from drake_api.protection.setup import ProtectionEvaluator
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
    slo_evaluator = getattr(app.state, "slo_evaluator", None)
    if slo_evaluator is not None:
        await slo_evaluator.start()
    sli_writer = getattr(app.state, "sli_rollup_writer", None)
    if sli_writer is not None:
        await sli_writer.start()
    protection_evaluator = getattr(app.state, "protection_evaluator", None)
    if protection_evaluator is not None:
        await protection_evaluator.start()
    silence_worker = getattr(app.state, "silence_worker", None)
    if silence_worker is not None:
        await silence_worker.start()
    gitops_worker = getattr(app.state, "gitops_worker", None)
    if gitops_worker is not None:
        await gitops_worker.start()
    try:
        yield
    finally:
        if gitops_worker is not None:
            await gitops_worker.stop()
        if silence_worker is not None:
            await silence_worker.stop()
        if sli_writer is not None:
            await sli_writer.stop()
        if protection_evaluator is not None:
            await protection_evaluator.stop()
        if slo_evaluator is not None:
            await slo_evaluator.stop()
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
    observability_transport: "httpx.AsyncBaseTransport | None" = None,
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
    # Logs and traces (ADR-0029). Constructing the adapter opens nothing and
    # reads no credential; it does so per query, and only when the feature
    # is on and a store is configured.
    app.state.observability_store = ClickHouseStore(settings, transport=observability_transport)
    # The evaluator reuses the Sprint 5 read path exactly as the API does —
    # same orchestrator, same broker, same budgets — so a scheduled
    # evaluation and a screen refresh can never disagree about a service.
    app.state.trace_golden = TraceGoldenReader(
        app.state.observability_store, enabled=settings.observability_traces_enabled
    )
    app.state.infrastructure = InfrastructureService(
        get_engine(settings), app.state.telemetry_broker, app.state.telemetry_redis
    )
    app.state.health_orchestrator = HealthOrchestrator(
        get_engine(settings),
        app.state.telemetry_broker,
        app.state.telemetry_registry,
        HealthCache(app.state.telemetry_redis),
        trace_golden=app.state.trace_golden,
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
            sampler=TelemetrySampler(
                app.state.observability_store,
                logs_enabled=settings.observability_logs_enabled,
                traces_enabled=settings.observability_traces_enabled,
            ),
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
    # The SLO evaluator reuses the Sprint 5 broker exactly as the API does,
    # so a scheduled evaluation and a screen refresh can never disagree.
    app.state.slo_evaluator = (
        SloEvaluator(
            get_engine(settings),
            app.state.telemetry_broker,
            app.state.telemetry_redis,
            interval_seconds=settings.slo_evaluator_interval_seconds,
            batch_size=settings.slo_evaluator_batch_size,
            lease_seconds=settings.slo_evaluator_lease_seconds,
        )
        if settings.slo_evaluator_enabled
        else None
    )
    # Objectives on stored requests need the five-minute counts kept: the
    # writer runs with the evaluator, and only when traces are read at all.
    store_connector = settings.observability_store
    app.state.sli_rollup_writer = (
        SliRollupWriter(
            get_engine(settings),
            app.state.observability_store,
            app.state.telemetry_redis,
            database=store_connector.database,
        )
        if settings.slo_evaluator_enabled
        and settings.observability_traces_enabled
        and store_connector is not None
        else None
    )
    # Backup posture is judged on a timer too, so a reporter that falls
    # silent reads as overdue rather than as its last good report.
    app.state.protection_evaluator = (
        ProtectionEvaluator(
            get_engine(settings),
            settings,
            app.state.telemetry_redis,
            interval_seconds=settings.protection_evaluation_interval_seconds,
        )
        if settings.protection_evaluation_enabled
        else None
    )
    # The GitOps worker is the only path through which Drake would WRITE to
    # a repository. Off by default, and while it is off nothing is claimed,
    # no token is minted and no provider call is made.
    #
    # Which provider a process gets is decided by the ENVIRONMENT, not by a
    # flag, and `RecordingProvider` can never be the production answer. It
    # is a test double that returns a pull-request number, so a production
    # process holding one would report an open pull request that does not
    # exist, against a branch nobody created.
    #
    # Production gets the real provider, and only when a real GitHub App is
    # configured — settings validation already requires that alongside the
    # flags, and this is the same rule where the object is constructed, so a
    # caller that bypassed the flags still cannot end up with a fake.
    if settings.is_production_like:
        app.state.gitops_provider = (
            GitHubPullRequestProvider(github_client) if settings.github_app_enabled else None
        )
    else:
        app.state.gitops_provider = RecordingProvider()
    app.state.gitops_worker = (
        GitOpsWorker(get_engine(settings), settings, app.state.gitops_provider)
        if (
            settings.gitops_worker_enabled
            and settings.github_gitops_pr_enabled
            and app.state.gitops_provider is not None
        )
        else None
    )
    # The only outbound provider MUTATION in Drake. Off unless an operator
    # turned it on and registered an Alertmanager.
    app.state.silence_worker = (
        SilenceWorker(get_engine(settings), settings, transport=webhook_transport)
        if settings.silence_worker_enabled
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
    app.include_router(catalog_clusters_router)
    app.include_router(scheduled_jobs_router)
    app.include_router(integrations_router)
    app.include_router(github_router)
    app.include_router(github_onboarding_router)
    app.include_router(onboarding_router)
    app.include_router(github_webhook_router)
    app.include_router(deployments_router)
    app.include_router(protection_router)
    app.include_router(protection_ingest_router)
    app.include_router(service_health_router)
    app.include_router(observability_router)
    app.include_router(observability_overview_router)
    app.include_router(dashboards_router)
    app.include_router(incidents_router)
    app.include_router(infrastructure_router)
    app.include_router(alerting_router)
    app.include_router(alert_rules_router)
    # Alertmanager delivers to /v1/webhooks/alertmanager/{key}, the path the
    # chart and deploy/monitoring configure. The router itself has no prefix,
    # so until 2026-11-17 that path answered 404 and no alert reached Drake
    # after 2026-08-12. Both paths are served: /v1 is the one in use, and the
    # unprefixed one stays for any sender configured against it.
    app.include_router(alertmanager_webhook_router, prefix="/v1")
    app.include_router(alertmanager_webhook_router)
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
