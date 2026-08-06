"""Scoped catalog read API (integration, local stack).

Authorization-first reads: list isolation, breadcrumb semantics, sibling
IDOR 404s, cluster separation, integration scope isolation, search/count
side-channel protection, cursor stability, and response redaction.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from drake_api.catalog.service import CatalogService
from drake_api.db import dispose_engines
from harness_s1 import (
    S1Harness,
    build_harness,
    grant_platform_owner,
    require_it_settings,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from test_catalog_persistence_integration import reset_catalog

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def migrated_db() -> None:
    settings = require_it_settings()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


@pytest.fixture
async def engine() -> Any:
    settings = require_it_settings()
    eng = create_async_engine(settings.database_url)
    await reset_catalog(eng)
    yield eng
    await eng.dispose()
    await dispose_engines()


async def seed_catalog_world(engine: AsyncEngine) -> dict[str, Any]:
    """alpha (dev on cluster-a, prod on cluster-b) + beta (dev on cluster-a)."""
    async with engine.begin() as connection:
        service = CatalogService(connection, source_kind="fixture")
        cluster_a = await service.create_cluster("cluster-a", "Cluster A")
        cluster_b = await service.create_cluster("cluster-b", "Cluster B")
        alpha = await service.create_project(
            "alpha",
            "Alpha",
            repo_provider="github",
            repo_owner="example-org",
            repo_name="alpha",
            tenant_model="none",
            owners=[("platform", "primary")],
        )
        beta = await service.create_project(
            "beta",
            "Beta",
            repo_provider="github",
            repo_owner="example-org",
            repo_name="beta",
            tenant_model="shared_table",
        )
        alpha_dev = await service.create_environment(
            alpha.id,
            "dev",
            runtime="kubernetes",
            cluster_id=cluster_a.id,
            namespace="alpha-dev",
        )
        alpha_prod = await service.create_environment(
            alpha.id,
            "prod",
            runtime="kubernetes",
            cluster_id=cluster_b.id,
            namespace="alpha-prod",
            criticality="critical",
        )
        beta_dev = await service.create_environment(
            beta.id,
            "dev",
            runtime="kubernetes",
            cluster_id=cluster_a.id,
            namespace="beta-dev",
        )
        api_def = await service.create_service_definition(
            alpha.id,
            "api",
            component="api",
            runtime="fastapi",
            metrics_profile="fastapi-v1",
            health={"livePath": "/health/live"},
        )
        web_def = await service.create_service_definition(
            alpha.id,
            "web",
            component="web",
            runtime="nextjs",
            metrics_profile="nextjs-v1",
        )
        dev_api = await service.bind_service(alpha_dev.id, api_def)
        dev_web = await service.bind_service(alpha_dev.id, web_def)
        prod_api = await service.bind_service(alpha_prod.id, api_def)
        beta_api = await service.create_service_definition(
            beta.id,
            "api",
            component="api",
            runtime="fastapi",
            metrics_profile="fastapi-v1",
        )
        await service.bind_service(beta_dev.id, beta_api)
        for integration_type in ("prometheus", "github"):
            await service.register_integration(integration_type, alpha.scope_id)
            await service.register_integration(integration_type, beta.scope_id)
        return {
            "alpha": alpha,
            "beta": beta,
            "cluster_a": cluster_a,
            "cluster_b": cluster_b,
            "alpha_dev": alpha_dev,
            "alpha_prod": alpha_prod,
            "beta_dev": beta_dev,
            "dev_api": dev_api,
            "dev_web": dev_web,
            "prod_api": prod_api,
        }


async def grant(
    engine: AsyncEngine,
    harness: S1Harness,
    subject: str,
    role_name: str,
    scope_type: str,
    scope_ref: str,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO grants (identity_id, role_id, scope_id)
                SELECT i.id, r.id, s.id
                FROM identities i, roles r, scopes s
                WHERE i.issuer = :issuer AND i.subject = :subject
                  AND r.name = :role
                  AND s.scope_type = :scope_type AND s.external_ref = :scope_ref
                """
            ),
            {
                "issuer": harness.provider.issuer,
                "subject": subject,
                "role": role_name,
                "scope_type": scope_type,
                "scope_ref": scope_ref,
            },
        )


async def make_role(
    harness: S1Harness, engine: AsyncEngine, name: str, permissions: list[str]
) -> None:
    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        me = (await owner.get("/v1/me")).json()
        created = await owner.post(
            "/v1/roles",
            json={"name": name, "description": ""},
            headers={
                "X-CSRF-Token": me["csrf_token"],
                "Idempotency-Key": f"cat-{uuidlib.uuid4().hex}",
            },
        )
        assert created.status_code == 201, created.text
        assert (
            await owner.put(
                f"/v1/roles/{created.json()['id']}/permissions",
                json={"permissions": permissions},
                headers={
                    "X-CSRF-Token": me["csrf_token"],
                    "Idempotency-Key": f"cat-{uuidlib.uuid4().hex}",
                    "If-Match": 'W/"role-1"',
                },
            )
        ).status_code == 200


async def login_all(harness: S1Harness, subjects: list[str]) -> None:
    for subject in subjects:
        if subject not in harness.provider.users:
            harness.provider.users[subject] = type(harness.provider.users["user-owner"])(
                subject, subject.replace("user-", "").title(), f"{subject}@example.test"
            )
        async with harness.api_client() as client:
            await harness.login(client, subject)


async def build_users(engine: AsyncEngine) -> S1Harness:
    """plain→Developer@project-alpha; env→Developer@alpha/dev; b→Developer@
    project-beta; clusterviewer→ClusterViewer@org-root."""
    harness = build_harness()
    await login_all(harness, ["user-plain", "user-env", "user-b-only", "user-cluster"])
    await make_role(harness, engine, "Cluster Viewer S2", ["cluster.view"])
    await grant(engine, harness, "user-plain", "Developer", "project", "alpha")
    await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
    await grant(engine, harness, "user-b-only", "Developer", "project", "beta")
    await grant(engine, harness, "user-cluster", "Cluster Viewer S2", "organization", "root")
    return harness


async def client_for(harness: S1Harness, subject: str) -> httpx.AsyncClient:
    client = harness.api_client()
    await harness.login(client, subject)
    return client


async def test_project_list_isolation_and_counts(engine: AsyncEngine) -> None:
    await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        body = (await plain.get("/v1/projects")).json()
        keys = [project["project_key"] for project in body["projects"]]
        assert keys == ["alpha"]  # beta absent — no unauthorized rows
        alpha = body["projects"][0]
        assert alpha["counts"]["environments"] == 2  # project-wide grant
        assert alpha["counts"]["services"] == 3

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        body = (await env_user.get("/v1/projects")).json()
        keys = [project["project_key"] for project in body["projects"]]
        assert keys == ["alpha"]  # breadcrumb visibility via the env grant
        alpha = body["projects"][0]
        # Counts reflect ONLY the caller's authorized children:
        assert alpha["counts"]["environments"] == 1
        assert alpha["counts"]["services"] == 2

    async with harness.api_client() as nobody:
        await harness.login(nobody, "user-cluster")
        body = (await nobody.get("/v1/projects")).json()
        assert body["projects"] == []  # cluster grant yields no project rows


async def test_environment_sibling_idor(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    alpha_id = str(world["alpha"].id)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        envs = (await env_user.get(f"/v1/projects/{alpha_id}/environments")).json()
        assert [env["environment_key"] for env in envs["environments"]] == ["dev"]

        prod_id = str(world["alpha_prod"].id)
        detail = await env_user.get(f"/v1/projects/{alpha_id}/environments/{prod_id}")
        assert detail.status_code == 404
        services = await env_user.get(f"/v1/projects/{alpha_id}/environments/{prod_id}/services")
        assert services.status_code == 404
        sibling_service = await env_user.get(
            f"/v1/projects/{alpha_id}/environments/{prod_id}/services/{world['prod_api'].id}"
        )
        assert sibling_service.status_code == 404
        assert "alpha-prod" not in detail.text  # no namespace leak in the body


async def test_service_visibility_follows_environment(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    alpha_id = str(world["alpha"].id)
    dev_id = str(world["alpha_dev"].id)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        services = (
            await env_user.get(f"/v1/projects/{alpha_id}/environments/{dev_id}/services")
        ).json()
        assert sorted(s["service_key"] for s in services["services"]) == ["api", "web"]
        detail = (
            await env_user.get(
                f"/v1/projects/{alpha_id}/environments/{dev_id}/services/{world['dev_api'].id}"
            )
        ).json()
        assert detail["service_key"] == "api"
        assert detail["health"] == {"livePath": "/health/live"}
        assert detail["operational"]["metrics"] == "not_configured"
        assert detail["scope"]["ref"] == "alpha/dev/api"


async def test_cluster_separation(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        assert (await plain.get("/v1/clusters")).json()["clusters"] == []
        detail = await plain.get(f"/v1/clusters/{world['cluster_a'].id}")
        assert detail.status_code == 404  # project grant ≠ cluster inventory

    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        clusters = (await viewer.get("/v1/clusters")).json()["clusters"]
        assert sorted(c["cluster_ref"] for c in clusters) == ["cluster-a", "cluster-b"]
        detail = (await viewer.get(f"/v1/clusters/{world['cluster_a'].id}")).json()
        assert detail["operational"] == {
            "agent": "not_configured",
            "inventory": "not_configured",
        }
        # Cluster grant alone reveals no project environments:
        assert detail["referenced_environments"] == []


async def test_integration_scope_isolation_and_redaction(engine: AsyncEngine) -> None:
    await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        body = (await plain.get("/v1/integrations/health")).json()
        scopes = {entry["scope"]["ref"] for entry in body["integrations"]}
        assert scopes == {"alpha"}  # beta's integrations are silently absent
        for entry in body["integrations"]:
            assert entry["configuration_state"] == "not_configured"
            assert entry["observed_state"] in ("unknown", "not_configured")
            assert "config_ref" not in entry


async def test_search_authorization_and_bounds(engine: AsyncEngine) -> None:
    await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        hits = (await plain.get("/v1/catalog/search?q=alpha")).json()["results"]
        assert {hit["kind"] for hit in hits} <= {"project", "environment", "service"}
        assert all(hit["project_key"] in (None, "alpha") for hit in hits)

        # Unauthorized names yield NOTHING — no existence oracle:
        assert (await plain.get("/v1/catalog/search?q=beta")).json()["results"] == []
        assert (await plain.get("/v1/catalog/search?q=cluster")).json()["results"] == []

        # Bounds enforced:
        assert (await plain.get("/v1/catalog/search?q=a")).status_code == 422
        assert (await plain.get("/v1/catalog/search?q=")).status_code == 422
        assert (await plain.get(f"/v1/catalog/search?q={'x' * 65}")).status_code == 422
        # Wildcards are literals, not patterns:
        assert (await plain.get("/v1/catalog/search?q=%25%25")).json()["results"] == []


async def test_pagination_after_authorization_and_cursor_stability(
    engine: AsyncEngine,
) -> None:
    await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        first = (await owner.get("/v1/projects?limit=1")).json()
        assert [p["project_key"] for p in first["projects"]] == ["alpha"]
        assert first["next_cursor"]
        second = (await owner.get(f"/v1/projects?limit=1&cursor={first['next_cursor']}")).json()
        assert [p["project_key"] for p in second["projects"]] == ["beta"]
        assert second["next_cursor"] is None
        assert (await owner.get("/v1/projects?cursor=garbage")).status_code == 422

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        # Authorization first, pagination second: one visible row, no cursor.
        page = (await plain.get("/v1/projects?limit=1")).json()
        assert [p["project_key"] for p in page["projects"]] == ["alpha"]
        assert page["next_cursor"] is None  # beta can't extend the caller's pages


async def test_context_counts_are_authorized_only(engine: AsyncEngine) -> None:
    await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        context = (await env_user.get("/v1/catalog/context")).json()
        assert context == {
            "projects": 1,
            "environments": 1,
            "clusters": 0,
            "as_of": context["as_of"],
        }

    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        context = (await viewer.get("/v1/catalog/context")).json()
        assert context["projects"] == 0
        assert context["clusters"] == 2


async def test_responses_never_leak_config_or_secrets(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    # Give one integration a config ref: it must stay server-side.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE integrations SET config_ref = 'alpha-prometheus-config', "
                "configuration_state = 'configured' WHERE scope_id = :sid "
                "AND integration_type = 'prometheus'"
            ),
            {"sid": world["alpha"].scope_id},
        )

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        for path in (
            "/v1/projects",
            f"/v1/projects/{world['alpha'].id}",
            "/v1/integrations/health",
        ):
            response = await plain.get(path)
            assert response.status_code == 200
            assert "config_ref" not in response.text
            assert "alpha-prometheus-config" not in response.text
            assert "issuer" not in response.text  # no OIDC identity data
        health = (await plain.get("/v1/integrations/health")).json()
        prometheus = next(
            entry for entry in health["integrations"] if entry["integration_type"] == "prometheus"
        )
        assert prometheus["configuration_state"] == "configured"
        assert prometheus["observed_state"] == "unknown"  # never fabricated health


async def test_unauthenticated_is_401(engine: AsyncEngine) -> None:
    await seed_catalog_world(engine)
    harness = build_harness()
    async with harness.api_client() as anonymous:
        assert (await anonymous.get("/v1/projects")).status_code == 401
        assert (await anonymous.get("/v1/clusters")).status_code == 401
        assert (await anonymous.get("/v1/catalog/search?q=alpha")).status_code == 401
