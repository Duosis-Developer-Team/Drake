"""Catalog persistence invariants (integration, disposable local stack).

Atomic entity+scope creation, multi-cluster topology, runtime constraints,
duplicate rejection, soft archive, rollback-together, fixture bootstrap
fail-closed, and Sprint 0/1 data preservation across 0001→0004.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from drake_api.catalog.service import CatalogService, CatalogValidationError
from drake_api.db import dispose_engines
from drake_api.rbac.catalog import seed_catalog
from harness_s1 import require_it_settings, reset_rbac_state
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="module", autouse=True)
def migrated_db() -> None:
    settings = require_it_settings()
    command.upgrade(alembic_config(settings.database_url), "head")


async def reset_catalog(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for table in (
            "integrations",
            "environment_services",
            "service_definitions",
            "environments",
            "project_owners",
            "projects",
            "clusters",
        ):
            await connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608
    await reset_rbac_state(engine)
    async with engine.begin() as connection:
        await seed_catalog(connection)


@pytest.fixture
async def engine() -> Any:
    settings = require_it_settings()
    eng = create_async_engine(settings.database_url)
    await reset_catalog(eng)
    yield eng
    await eng.dispose()
    await dispose_engines()


async def build_world(engine: AsyncEngine) -> dict[str, Any]:
    """Multi-cluster fictional world: alpha on cluster-a (dev) AND cluster-b
    (prod) — proving environments of one project span clusters."""
    async with engine.begin() as connection:
        service = CatalogService(connection, source_kind="fixture")
        cluster_a = await service.create_cluster("cluster-a", "Cluster A")
        cluster_b = await service.create_cluster("cluster-b", "Cluster B")
        project = await service.create_project(
            "alpha",
            "Alpha",
            repo_provider="github",
            repo_owner="example-org",
            repo_name="alpha",
            tenant_model="none",
            owners=[("platform", "primary")],
        )
        env_dev = await service.create_environment(
            project.id,
            "alpha",
            "dev",
            runtime="kubernetes",
            cluster_id=cluster_a.id,
            namespace="alpha-dev",
        )
        env_prod = await service.create_environment(
            project.id,
            "alpha",
            "prod",
            runtime="kubernetes",
            cluster_id=cluster_b.id,
            namespace="alpha-prod",
            criticality="critical",
        )
        api_service = await service.create_service_definition(
            project.id,
            "api",
            component="api",
            runtime="fastapi",
            metrics_profile="fastapi-v1",
            health={"livePath": "/health/live", "readyPath": "/health/ready"},
        )
        binding_dev = await service.bind_service(
            env_dev.id,
            api_service,
            project_key="alpha",
            environment_key="dev",
            service_key="api",
        )
        return {
            "cluster_a": cluster_a,
            "cluster_b": cluster_b,
            "project": project,
            "env_dev": env_dev,
            "env_prod": env_prod,
            "api_service": api_service,
            "binding_dev": binding_dev,
        }


async def test_atomic_entity_and_scope_creation(engine: AsyncEngine) -> None:
    world = await build_world(engine)
    async with engine.connect() as connection:
        for scope_type, ref, entity_table, entity_id in (
            ("project", "alpha", "projects", world["project"].id),
            ("environment", "alpha/dev", "environments", world["env_dev"].id),
            ("service", "alpha/dev/api", "environment_services", world["binding_dev"].id),
            ("cluster", "cluster-a", "clusters", world["cluster_a"].id),
        ):
            scope_row = (
                await connection.execute(
                    text("SELECT id FROM scopes WHERE scope_type = :t AND external_ref = :r"),
                    {"t": scope_type, "r": ref},
                )
            ).first()
            assert scope_row is not None, f"scope missing for {scope_type}/{ref}"
            entity_row = (
                await connection.execute(
                    text(
                        f"SELECT scope_id FROM {entity_table} WHERE id = :id"  # noqa: S608
                    ),
                    {"id": entity_id},
                )
            ).first()
            assert entity_row is not None and entity_row[0] == scope_row[0]

        # Scope parentage matches ADR-0014 topology.
        parent = (
            await connection.execute(
                text(
                    """
                    SELECT p.scope_type FROM scopes s JOIN scopes p ON p.id = s.parent_id
                    WHERE s.scope_type = 'environment' AND s.external_ref = 'alpha/dev'
                    """
                )
            )
        ).scalar_one()
        assert parent == "project"
        cluster_parent = (
            await connection.execute(
                text(
                    """
                    SELECT p.scope_type FROM scopes s JOIN scopes p ON p.id = s.parent_id
                    WHERE s.scope_type = 'cluster' AND s.external_ref = 'cluster-a'
                    """
                )
            )
        ).scalar_one()
        assert cluster_parent == "organization"


async def test_multi_cluster_project_topology(engine: AsyncEngine) -> None:
    world = await build_world(engine)
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT e.environment_key, c.cluster_ref
                    FROM environments e JOIN clusters c ON c.id = e.cluster_id
                    WHERE e.project_id = :pid ORDER BY e.environment_key
                    """
                ),
                {"pid": world["project"].id},
            )
        ).all()
    assert [(row[0], row[1]) for row in rows] == [
        ("dev", "cluster-a"),
        ("prod", "cluster-b"),
    ]


async def test_transaction_failure_rolls_back_entity_and_scope(engine: AsyncEngine) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        async with engine.begin() as connection:
            service = CatalogService(connection)
            await service.create_project(
                "rollback-proj",
                "Rollback",
                repo_provider="github",
                repo_owner="example-org",
                repo_name="rollback",
            )
            # Force a failure INSIDE the same transaction:
            await connection.execute(text("INSERT INTO projects (id) VALUES (NULL)"))
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT count(*) FROM projects WHERE project_key = 'rollback-proj'")
            )
        ).scalar_one() == 0
        assert (
            await connection.execute(
                text(
                    "SELECT count(*) FROM scopes "
                    "WHERE scope_type='project' AND external_ref='rollback-proj'"
                )
            )
        ).scalar_one() == 0  # scope rolled back together with the entity


async def test_kubernetes_environment_requires_cluster_and_namespace(
    engine: AsyncEngine,
) -> None:
    world = await build_world(engine)
    with pytest.raises((IntegrityError, DBAPIError)):
        async with engine.begin() as connection:
            service = CatalogService(connection)
            await service.create_environment(
                world["project"].id,
                "alpha",
                "broken",
                runtime="kubernetes",
                cluster_id=None,
                namespace=None,
            )


async def test_external_environment_needs_no_cluster(engine: AsyncEngine) -> None:
    world = await build_world(engine)
    async with engine.begin() as connection:
        service = CatalogService(connection)
        created = await service.create_environment(
            world["project"].id,
            "alpha",
            "legacy",
            runtime="external",
            cluster_id=None,
            namespace=None,
        )
    assert created.id


async def test_duplicate_rejections(engine: AsyncEngine) -> None:
    world = await build_world(engine)

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await CatalogService(connection).create_project(
                "alpha",
                "Duplicate",
                repo_provider="github",
                repo_owner="example-org",
                repo_name="other",
            )

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await CatalogService(connection).create_environment(
                world["project"].id,
                "alpha",
                "dev",
                runtime="kubernetes",
                cluster_id=world["cluster_a"].id,
                namespace="alpha-dev-2",
            )

    # Same active cluster/namespace pair claimed by another environment:
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await CatalogService(connection).create_environment(
                world["project"].id,
                "alpha",
                "dev2",
                runtime="kubernetes",
                cluster_id=world["cluster_a"].id,
                namespace="alpha-dev",
            )


async def test_soft_archive_preserves_history_and_frees_namespace(
    engine: AsyncEngine,
) -> None:
    world = await build_world(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE environments SET lifecycle='archived', archived_at=now() WHERE id = :id"),
            {"id": world["env_dev"].id},
        )
    # Row still exists (history preserved)…
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT lifecycle FROM environments WHERE id = :id"),
                {"id": world["env_dev"].id},
            )
        ).scalar_one() == "archived"
    # …and the active cluster/namespace slot is reusable.
    async with engine.begin() as connection:
        created = await CatalogService(connection).create_environment(
            world["project"].id,
            "alpha",
            "dev-v2",
            runtime="kubernetes",
            cluster_id=world["cluster_a"].id,
            namespace="alpha-dev",
        )
    assert created.id


async def test_bounded_metadata_validation(engine: AsyncEngine) -> None:
    world = await build_world(engine)
    async with engine.begin() as connection:
        service = CatalogService(connection)
        with pytest.raises(CatalogValidationError):
            await service.create_service_definition(
                world["project"].id,
                "bad-health",
                component="api",
                runtime="fastapi",
                metrics_profile="fastapi-v1",
                health={"livePath": "https://example.test/live"},  # URL, not a path
            )
        with pytest.raises(CatalogValidationError):
            await service.create_service_definition(
                world["project"].id,
                "bad-selector",
                component="api",
                runtime="fastapi",
                metrics_profile="fastapi-v1",
                workload_selector={"conn": "postgresql://u:fakepw@db/x"},
            )
        with pytest.raises(CatalogValidationError):
            await service.create_service_definition(
                world["project"].id,
                "fat-selector",
                component="api",
                runtime="fastapi",
                metrics_profile="fastapi-v1",
                workload_selector={"blob": "x" * 10_000},
            )


async def test_fixture_bootstrap_fails_closed_outside_local_test(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import drake_api.catalog.bootstrap as bootstrap_module
    from drake_api.testing import make_settings

    monkeypatch.setattr(bootstrap_module, "get_settings", lambda: make_settings(env="prod"))
    with pytest.raises(RuntimeError, match="local/test only"):
        await bootstrap_module.bootstrap()


async def test_migration_cycle_and_sprint1_data_preservation(engine: AsyncEngine) -> None:
    settings = require_it_settings()
    config = alembic_config(settings.database_url)

    # Plant Sprint 1 data + catalog data, then walk 0004 down and up.
    # Audit is append-only across suite runs, so the marker must be unique.
    marker = f"preserve-0004-{uuidlib.uuid4().hex}"
    world = await build_world(engine)
    async with engine.begin() as connection:
        identity_id = (
            await connection.execute(
                text(
                    """
                    INSERT INTO identities (issuer, subject, display_name)
                    VALUES ('it-issuer', 'preserve-me', 'Preserve Me') RETURNING id
                    """
                )
            )
        ).scalar_one()
        await connection.execute(
            text(
                """
                INSERT INTO audit_events
                    (actor_type, actor_id, action, result, correlation_id, metadata,
                     schema_version)
                VALUES ('system', 'migration-test', 'catalog.preserve.check', 'success',
                        :marker, '{}'::jsonb, 1)
                """
            ),
            {"marker": marker},
        )

    command.downgrade(config, "0003")
    command.upgrade(config, "head")

    async with engine.connect() as connection:
        # Sprint 1 data intact after the 0004 cycle:
        assert (
            await connection.execute(
                text("SELECT count(*) FROM identities WHERE subject = 'preserve-me'")
            )
        ).scalar_one() == 1
        assert (
            await connection.execute(
                text("SELECT count(*) FROM audit_events WHERE correlation_id = :marker"),
                {"marker": marker},
            )
        ).scalar_one() == 1
        # Catalog tables exist again (empty after downgrade — disposable DB).
        assert (await connection.execute(text("SELECT count(*) FROM projects"))).scalar_one() == 0
    del world, identity_id
