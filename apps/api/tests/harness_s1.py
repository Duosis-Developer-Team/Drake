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
            base_url=API_BASE,
        )

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
        assert callback_url.startswith(CALLBACK_URL)

        callback_parsed = urlparse(callback_url)
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
) -> S1Harness:
    base = settings or require_it_settings()
    wired = base.model_copy(
        update={
            "oidc_issuer": DEFAULT_ISSUER,
            "oidc_client_id": DEFAULT_CLIENT_ID,
            "oidc_redirect_url": CALLBACK_URL,
        }
    )
    provider = FakeOidcProvider()
    oidc_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=provider.build_app()), base_url=DEFAULT_ISSUER
    )
    oidc_client = OidcClient(wired, http_client=oidc_http)
    app = create_app(wired, oidc_client=oidc_client, telemetry_transport=telemetry_transport)
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
