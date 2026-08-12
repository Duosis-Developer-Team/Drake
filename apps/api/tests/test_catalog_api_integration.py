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
from drake_api.service_health.policy import DEFAULT_POLICY_KEY
from drake_api.service_health.presets import DEFAULT_PRESET_KEY
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


async def test_service_capabilities_are_derived_from_evidence(engine: AsyncEngine) -> None:
    """The four capability states were a hardcoded literal.

    Every service reported all four as `not_configured` forever — including
    services whose golden-signal charts were rendering live Prometheus data
    on the same screen. A panel that cannot change is decoration, and this
    asserts that it changes for the reason it claims to.

    The middle state is the interesting one: a binding that exists but whose
    workload inventory has never resolved is configuration without an object
    behind it. Reporting that as `ok` would promise charts that come back
    empty, so it reports `unknown` — Drake is watching and has not seen yet.
    """
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    alpha_id, dev_id = world["alpha"].id, world["alpha_dev"].id
    service_binding = world["dev_api"].id

    async def metrics_state() -> str:
        async with harness.api_client() as client:
            await harness.login(client, "user-env")
            detail = (
                await client.get(
                    f"/v1/projects/{alpha_id}/environments/{dev_id}/services/{service_binding}"
                )
            ).json()
            return str(detail["operational"]["metrics"])

    assert await metrics_state() == "not_configured", "no binding, nothing to ask"

    async with engine.begin() as connection:
        anchor = (
            await connection.execute(
                text(
                    """
                    SELECT es.service_id, e.project_id, es.environment_id
                    FROM environment_services es
                    JOIN environments e ON e.id = es.environment_id
                    WHERE es.id = :id
                    """
                ),
                {"id": service_binding},
            )
        ).first()
        assert anchor is not None
        await connection.execute(
            text(
                """
                INSERT INTO service_workload_bindings
                  (environment_service_id, project_id, environment_id, service_id,
                   cluster_id, namespace, workload_kind, workload_name,
                   preset_key, health_policy_key, lifecycle)
                VALUES
                  (:es, :project, :environment, :service, :cluster, 'alpha-dev',
                   'Deployment', 'api', :preset, :policy, 'active')
                """
            ),
            {
                "es": service_binding,
                "project": anchor[1],
                "environment": anchor[2],
                "service": anchor[0],
                "cluster": world["cluster_a"].id,
                # The canonical keys, not literals: the table constrains their
                # SHAPE and the application validates their membership, so a
                # hardcoded string here would drift out of both silently.
                "preset": DEFAULT_PRESET_KEY,
                "policy": DEFAULT_POLICY_KEY,
            },
        )

    assert await metrics_state() == "unknown", "bound, but inventory has not resolved it"

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE service_workload_bindings
                SET resolved_resource_uid = 'uid-api', resolved_at = now()
                WHERE environment_service_id = :id
                """
            ),
            {"id": service_binding},
        )

    assert await metrics_state() == "ok", "resolved: there is a workload to query"


async def test_capabilities_with_no_source_stay_honestly_absent(engine: AsyncEngine) -> None:
    """Drake ingests neither logs nor traces, and says so.

    This is here so that the day a source is added, the person adding it has
    to change this test on purpose rather than discover that the screen had
    been claiming coverage nobody built.
    """
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    async with harness.api_client() as client:
        await harness.login(client, "user-env")
        detail = (
            await client.get(
                f"/v1/projects/{world['alpha'].id}/environments/{world['alpha_dev'].id}"
                f"/services/{world['dev_api'].id}"
            )
        ).json()
    assert detail["operational"]["logs"] == "not_configured"
    assert detail["operational"]["traces"] == "not_configured"


async def test_project_inventory_cannot_be_faked_by_an_integration_row(
    engine: AsyncEngine,
) -> None:
    """The `cluster-agent` row is written by onboarding and read by nothing.

    No code resolves it and no code writes its observed_state, so in
    production it sat at not_configured/unknown while enrolled agents were
    reporting fresh inventory — the cluster screen was right and the project
    screen was wrong about the same estate.

    This marks that row as configured AND observed `ok`, which is the most
    optimistic thing the old code path could have been told, and asserts the
    project still reports the truth: these clusters have no agent.
    """
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO integrations
                    (integration_type, scope_id, configuration_state, config_ref, observed_state)
                VALUES ('cluster-agent', :scope, 'configured', 'pretend-agent', 'ok')
                """
            ),
            {"scope": world["alpha"].scope_id},
        )

    async with harness.api_client() as client:
        await harness.login(client, "user-plain")
        detail = (await client.get(f"/v1/projects/{world['alpha'].id}")).json()

    assert detail["operational"]["inventory"] == "not_configured", (
        "no cluster in this world has an enrolled agent, and a row nobody "
        "feeds must not be able to claim otherwise"
    )
    # Protection still reads its integration row, because that IS its source.
    assert detail["operational"]["protection"] == "not_configured"


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


# --- Sprint 2 closure: bounded collections & SQL-boundary authorization -----


async def test_environment_pagination_filters_and_archived_default(
    engine: AsyncEngine,
) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    alpha_id = str(world["alpha"].id)

    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        base = f"/v1/projects/{alpha_id}/environments"

        first = (await owner.get(f"{base}?limit=1")).json()
        assert [e["environment_key"] for e in first["environments"]] == ["dev"]
        assert first["next_cursor"]
        second = (await owner.get(f"{base}?limit=1&cursor={first['next_cursor']}")).json()
        assert [e["environment_key"] for e in second["environments"]] == ["prod"]
        assert second["next_cursor"] is None
        assert (await owner.get(f"{base}?cursor=garbage")).status_code == 422

        search = (await owner.get(f"{base}?search=de")).json()
        assert [e["environment_key"] for e in search["environments"]] == ["dev"]
        critical = (await owner.get(f"{base}?criticality=critical")).json()
        assert [e["environment_key"] for e in critical["environments"]] == ["prod"]

        # Archived rows are hidden by default and reachable only explicitly.
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE environments SET lifecycle='archived', archived_at=now() WHERE id = :id"
                ),
                {"id": world["alpha_prod"].id},
            )
        default = (await owner.get(base)).json()
        assert [e["environment_key"] for e in default["environments"]] == ["dev"]
        everything = (await owner.get(f"{base}?lifecycle=all")).json()
        assert [e["environment_key"] for e in everything["environments"]] == ["dev", "prod"]
        archived = (await owner.get(f"{base}?lifecycle=archived")).json()
        assert [e["environment_key"] for e in archived["environments"]] == ["prod"]


async def test_environment_unauthorized_rows_cannot_extend_cursor(
    engine: AsyncEngine,
) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    alpha_id = str(world["alpha"].id)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        # alpha has two environments but the caller may see only dev: the
        # invisible prod row must not produce a next page.
        page = (await env_user.get(f"/v1/projects/{alpha_id}/environments?limit=1")).json()
        assert [e["environment_key"] for e in page["environments"]] == ["dev"]
        assert page["next_cursor"] is None


async def test_service_pagination_provenance_and_archived_default(
    engine: AsyncEngine,
) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    base = f"/v1/projects/{world['alpha'].id}/environments/{world['alpha_dev'].id}/services"

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        first = (await plain.get(f"{base}?limit=1")).json()
        assert [s["service_key"] for s in first["services"]] == ["api"]
        assert first["next_cursor"]
        second = (await plain.get(f"{base}?limit=1&cursor={first['next_cursor']}")).json()
        assert [s["service_key"] for s in second["services"]] == ["web"]
        assert second["next_cursor"] is None
        assert (await plain.get(f"{base}?cursor=garbage")).status_code == 422

        # Items carry safe scope/version/source — and nothing else.
        item = first["services"][0]
        assert item["scope"] == {"type": "service", "ref": "alpha/dev/api"}
        assert isinstance(item["version"], int)
        assert item["source"]["kind"] == "fixture"
        assert "config_ref" not in str(first)

        search = (await plain.get(f"{base}?search=we")).json()
        assert [s["service_key"] for s in search["services"]] == ["web"]

        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE environment_services SET lifecycle='archived' WHERE id = :id"),
                {"id": world["dev_web"].id},
            )
        default = (await plain.get(base)).json()
        assert [s["service_key"] for s in default["services"]] == ["api"]
        everything = (await plain.get(f"{base}?lifecycle=all")).json()
        assert [s["service_key"] for s in everything["services"]] == ["api", "web"]


async def test_cluster_search_and_lifecycle_default(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        hits = (await viewer.get("/v1/clusters?search=cluster-a")).json()
        assert [c["cluster_ref"] for c in hits["clusters"]] == ["cluster-a"]
        # Wildcards are literals:
        assert (await viewer.get("/v1/clusters?search=%25%25")).json()["clusters"] == []

        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE clusters SET lifecycle='archived', archived_at=now() WHERE id = :id"),
                {"id": world["cluster_b"].id},
            )
        default = (await viewer.get("/v1/clusters")).json()
        assert [c["cluster_ref"] for c in default["clusters"]] == ["cluster-a"]
        everything = (await viewer.get("/v1/clusters?lifecycle=all")).json()
        assert [c["cluster_ref"] for c in everything["clusters"]] == [
            "cluster-a",
            "cluster-b",
        ]
        archived = (await viewer.get("/v1/clusters?lifecycle=archived")).json()
        assert [c["cluster_ref"] for c in archived["clusters"]] == ["cluster-b"]


async def test_integration_pagination_filters_and_determinism(
    engine: AsyncEngine,
) -> None:
    await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

        # Keyset walk: bounded pages, no duplicates, deterministic order.
        seen: list[tuple[str, str]] = []
        cursor = ""
        for _ in range(10):
            url = f"/v1/integrations/health?limit=1{cursor}"
            body = (await owner.get(url)).json()
            assert len(body["integrations"]) <= 1
            seen.extend((e["scope"]["ref"], e["integration_type"]) for e in body["integrations"])
            if not body["next_cursor"]:
                break
            cursor = f"&cursor={body['next_cursor']}"
        assert seen == [
            ("alpha", "github"),
            ("alpha", "prometheus"),
            ("beta", "github"),
            ("beta", "prometheus"),
        ]
        assert len(seen) == len(set(seen))

        filtered = (await owner.get("/v1/integrations/health?integration_type=prometheus")).json()
        assert {e["integration_type"] for e in filtered["integrations"]} == {"prometheus"}
        assert len(filtered["integrations"]) == 2
        by_state = (
            await owner.get("/v1/integrations/health?configuration_state=configured")
        ).json()
        assert by_state["integrations"] == []
        by_observed = (await owner.get("/v1/integrations/health?observed_state=unknown")).json()
        assert len(by_observed["integrations"]) == 4

        # Bounded filter inputs and cursors reject invalid values.
        assert (
            await owner.get("/v1/integrations/health?integration_type=BAD%20TYPE")
        ).status_code == 422
        assert (
            await owner.get("/v1/integrations/health?configuration_state=weird")
        ).status_code == 422
        assert (await owner.get("/v1/integrations/health?cursor=garbage")).status_code == 422


async def test_integration_authorization_is_a_sql_boundary(engine: AsyncEngine) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    # Attach an integration at cluster scope as well.
    async with engine.begin() as connection:
        await CatalogService(connection).register_integration(
            "cluster-agent", world["cluster_a"].scope_id
        )

    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        # Project grant: alpha rows only — never beta, never cluster scopes,
        # regardless of filters or page walking.
        body = (await plain.get("/v1/integrations/health?limit=100")).json()
        assert {e["scope"]["ref"] for e in body["integrations"]} == {"alpha"}
        assert body["next_cursor"] is None
        filtered = (
            await plain.get("/v1/integrations/health?integration_type=cluster-agent")
        ).json()
        assert filtered["integrations"] == []

    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        # Cluster grant: the cluster-scope row only — no project metadata.
        body = (await viewer.get("/v1/integrations/health?limit=100")).json()
        assert [(e["scope"]["type"], e["scope"]["ref"]) for e in body["integrations"]] == [
            ("cluster", "cluster-a")
        ]


async def test_search_deterministic_order_across_projects(engine: AsyncEngine) -> None:
    """alpha and beta both define service 'api' and environment 'dev' — the
    tie-breaker (kind, project_key, key, id) must give one stable order."""
    await seed_catalog_world(engine)
    harness = await build_users(engine)

    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        runs = []
        for _ in range(3):
            hits = (await owner.get("/v1/catalog/search?q=api")).json()["results"]
            runs.append([(h["kind"], h["project_key"], h["key"], h["id"]) for h in hits])
        assert runs[0] == runs[1] == runs[2]
        service_projects = [h[1] for h in runs[0] if h[0] == "service"]
        assert service_projects == sorted(service_projects)  # alpha before beta

        dev_hits = (await owner.get("/v1/catalog/search?q=dev")).json()["results"]
        env_projects = [h["project_key"] for h in dev_hits if h["kind"] == "environment"]
        assert env_projects == ["alpha", "beta"]
