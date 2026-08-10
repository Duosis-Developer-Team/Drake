"""Shared Sprint 1 integration harness (not a test module).

Builds a full app wired to the fake OIDC provider over in-process ASGI
transports, drives the real login flow (login → authorize → callback), and
prepares/cleans RBAC state on the disposable local stack.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from drake_api.auth.oidc import OidcClient
from drake_api.main import create_app
from drake_api.settings import Settings
from drake_api.testing import integration_settings
from fake_oidc import DEFAULT_CLIENT_ID, DEFAULT_ISSUER, FakeOidcProvider
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

API_BASE = "http://testserver"
CALLBACK_URL = f"{API_BASE}/v1/auth/callback"


def require_it_settings() -> Settings:
    settings = integration_settings()
    if settings is None:
        import pytest

        pytest.skip("DRAKE_IT_DATABASE_URL / DRAKE_IT_REDIS_URL not set")
    return settings


@dataclass
class S1Harness:
    app: FastAPI
    provider: FakeOidcProvider
    settings: Settings

    def api_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url=self.client_base_url,
        )

    @property
    def client_base_url(self) -> str:
        """Where the test client addresses the app.

        A production-shaped harness is addressed on its canonical origin:
        the session cookie is `Secure` there, so an http base URL would
        silently drop it and every authenticated assertion would fail for
        the wrong reason.
        """
        if self.settings.env in ("local", "test"):
            return API_BASE
        return str(self.settings.resolved_public_origin())

    def provider_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.provider.build_app()),
            base_url=self.provider.issuer,
        )

    async def login(
        self, client: httpx.AsyncClient, subject: str, redirect: str = "/"
    ) -> dict[str, Any]:
        """Drive the full OIDC flow; returns the /v1/me payload."""
        start = await client.get(f"/v1/auth/login?redirect={redirect}")
        assert start.status_code == 302, start.text
        authorize_url = start.headers["location"]

        async with self.provider_client() as provider_client:
            parsed = urlparse(authorize_url)
            authorize = await provider_client.get(
                f"{parsed.path}?{parsed.query}&login_hint={subject}"
            )
        assert authorize.status_code == 302
        callback_url = authorize.headers["location"]
        callback_parsed = urlparse(callback_url)
        # Compare the PATH, not the whole URL: a production-shaped harness
        # redirects to the canonical public origin while the test client
        # still serves the app locally.
        assert callback_parsed.path == urlparse(CALLBACK_URL).path
        callback = await client.get(f"{callback_parsed.path}?{callback_parsed.query}")
        assert callback.status_code == 302, callback.text

        me = await client.get("/v1/me")
        assert me.status_code == 200, me.text
        payload: dict[str, Any] = me.json()
        return payload

    async def authorize_code(self, client: httpx.AsyncClient, subject: str) -> tuple[str, str]:
        """Run login+authorize only; return (code, state) without callback."""
        start = await client.get("/v1/auth/login?redirect=/")
        authorize_url = start.headers["location"]
        async with self.provider_client() as provider_client:
            parsed = urlparse(authorize_url)
            authorize = await provider_client.get(
                f"{parsed.path}?{parsed.query}&login_hint={subject}"
            )
        query = parse_qs(urlparse(authorize.headers["location"]).query)
        return query["code"][0], query["state"][0]


def build_harness(
    settings: Settings | None = None,
    telemetry_transport: httpx.AsyncBaseTransport | None = None,
    github_transport: httpx.AsyncBaseTransport | None = None,
) -> S1Harness:
    base = settings or require_it_settings()
    update = {"oidc_issuer": DEFAULT_ISSUER, "oidc_client_id": DEFAULT_CLIENT_ID}
    if base.env in ("local", "test"):
        update["oidc_redirect_url"] = CALLBACK_URL
    else:
        # A production-shaped harness keeps its canonical redirect URL:
        # overwriting it here would make the production edge guard
        # untestable, since the guard is exactly that the two agree.
        update["oidc_issuer"] = base.oidc_issuer or DEFAULT_ISSUER
    wired = base.model_copy(update=update)
    # The provider answers on whatever issuer the settings name. It is all
    # in-process ASGI, so an https issuer costs nothing and lets a
    # production-shaped harness satisfy the plaintext-issuer guard.
    provider = FakeOidcProvider(issuer=wired.oidc_issuer)
    oidc_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=provider.build_app()), base_url=wired.oidc_issuer
    )
    oidc_client = OidcClient(wired, http_client=oidc_http)
    app = create_app(
        wired,
        oidc_client=oidc_client,
        telemetry_transport=telemetry_transport,
        github_transport=github_transport,
    )
    return S1Harness(app=app, provider=provider, settings=wired)


async def reset_rbac_state(engine: AsyncEngine) -> None:
    """Clean RBAC tables between tests (audit is append-only and stays).

    Catalog tables (0004) reference scopes with RESTRICT, so they are
    cleared first whenever they exist — keeps resets order-independent
    across test files.
    """
    async with engine.begin() as connection:
        for table in (
            # Sprint 4 agent/inventory tables reference clusters AND
            # identities with RESTRICT — cleared first when they exist.
            # Sprint 5A GitHub tables reference scopes/installations with
            # RESTRICT — cleared before anything they point at.
            # Sprint 11 onboarding tables reference github projections,
            # identities and the catalog with RESTRICT, so they clear first.
            "gitops_requests",
            "onboarding_applies",
            "onboarding_plan_items",
            "onboarding_plans",
            "onboarding_findings",
            "onboarding_analyses",
            "onboarding_sessions",
            # Sprint 10 alerting tables reference integrations, incidents
            # and the catalog with RESTRICT, so they clear before any of them.
            "silence_requests",
            "slo_evaluations",
            "slo_definitions",
            "alert_incident_links",
            "alert_events",
            "alert_instances",
            "alertmanager_deliveries",
            # Sprint 9 protection tables reference the catalog with
            # RESTRICT, so they clear before projects and environments.
            "protection_snapshots",
            "protection_ingest_events",
            "protection_evaluations",
            "restore_drills",
            "integrity_checks",
            "replication_copies",
            "backup_artifacts",
            "backup_runs",
            "backup_policies",
            # Sprint 8 deployment revisions reference clusters, bindings
            # and the catalog with RESTRICT, so they clear first.
            "deployment_health_comparisons",
            "deployment_revisions",
            # Sprint 7 notification tables reference incident events,
            # identities and projects with RESTRICT, so they clear first.
            "webhook_delivery_attempts",
            "webhook_deliveries",
            "in_app_notifications",
            "notification_event_plans",
            "notification_policy_destinations",
            "notification_destinations",
            "notification_policies",
            # Sprint 6 incident tables reference bindings and the catalog
            # with RESTRICT, so they clear before the binding they describe.
            "incident_events",
            "incidents",
            "service_health_transitions",
            "service_health_state",
            # Sprint 5 bindings reference environment_services and clusters
            # with RESTRICT, so they clear before either.
            "service_workload_bindings",
            "github_policy_evaluations",
            "github_webhook_deliveries",
            "github_repository_projects",
            "github_onboarding_drafts",
            "github_repositories",
            "github_reconciliation_jobs",
            "github_installations",
            "cluster_inventory_state",
            "inventory_change_events",
            "inventory_resources",
            "inventory_staging_resources",
            "inventory_snapshot_pages",
            "inventory_snapshots",
            "cluster_agents",
            "agent_enrollment_tokens",
            "integrations",
            "environment_services",
            "service_definitions",
            "environments",
            "project_owners",
            "projects",
            "clusters",
        ):
            exists = (
                await connection.execute(text("SELECT to_regclass(:name)"), {"name": table})
            ).scalar_one()
            if exists is not None:
                await connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608
        await connection.execute(text("DELETE FROM grants"))
        await connection.execute(text("DELETE FROM group_mappings"))
        await connection.execute(text("DELETE FROM identities"))
        await connection.execute(
            text(
                "DELETE FROM role_permissions WHERE role_id IN "
                "(SELECT id FROM roles WHERE is_system = false)"
            )
        )
        await connection.execute(text("DELETE FROM roles WHERE is_system = false"))
        await connection.execute(text("DELETE FROM scopes WHERE scope_type != 'organization'"))


async def grant_platform_owner(engine: AsyncEngine, issuer: str, subject: str) -> None:
    """Test bootstrap: grant the Platform Owner template at the org root."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO grants (identity_id, role_id, scope_id)
                SELECT i.id, r.id, s.id
                FROM identities i, roles r, scopes s
                WHERE i.issuer = :issuer AND i.subject = :subject
                  AND r.name = 'Platform Owner'
                  AND s.scope_type = 'organization' AND s.external_ref = 'root'
                """
            ),
            {"issuer": issuer, "subject": subject},
        )
