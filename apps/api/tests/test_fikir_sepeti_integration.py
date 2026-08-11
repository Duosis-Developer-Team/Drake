"""Fikir Sepeti, end to end, against real PostgreSQL.

The unit tests next door prove the manifest and the plan. This proves the
part that cannot be proven in memory: that the values survive a
transactional apply, a real schema with real check constraints, and the
catalog API serializer — and that a second import of the same manifest adds
nothing.

The manifest is READ FROM DISK, not restated here, so this exercises the
document that would actually be admitted. Only the repository coordinates
are retargeted, and only because the fixture harness serves exactly one
repository (`Hermes`); every other value — external runtime, Vercel,
Supabase, verification, tenant model, the absent metrics profile, the
unevidenced owner — is the real one.

Nothing here touches the production catalog. The engine is the ephemeral
per-test database the integration harness creates and drops.
"""

from __future__ import annotations

import uuid as uuidlib
from pathlib import Path

import pytest
from drake_api.catalog.service import CatalogService
from drake_api.onboarding import service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import github_harness
from test_onboarding_integration import (
    _bootstrap,
    _identity,
    _principal,
    golden_tree,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "packages" / "contracts" / "onboarding" / "fikir-sepeti.project.yaml"

PROJECT_KEY = "fikir-sepeti"


def manifest() -> str:
    """The real manifest, pointed at the fixture repository.

    The substitution is deliberately narrow and asserted: the harness serves
    `Duosis-Developer-Team/Hermes`, and a manifest naming a repository the
    harness does not serve fails to resolve for reasons that have nothing to
    do with what these tests are about. Replacing the whole document with a
    hand-written copy would be worse — it would test a manifest nobody ships.
    """
    text_ = MANIFEST_PATH.read_text()
    assert "name: Fikir-Sepeti" in text_, "manifest repository block changed shape"
    return text_.replace("name: Fikir-Sepeti", "name: Hermes", 1)


async def _import(engine: AsyncEngine, tmp_path: Path, document: str | None = None):
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree(document or manifest()))
    settings = harness.app.state.settings
    client = harness.app.state.github_client
    actor = await _identity(engine)

    created = await service.create_session(
        engine,
        settings,
        repository_row_id=row_id,
        actor_identity_id=actor,
        principal=await _principal(harness, engine),
    )
    session_id = uuidlib.UUID(created["session_id"])
    analysis = await service.analyze(engine, settings, client, session_id=session_id)
    async with engine.connect() as connection:
        version = int(
            (
                await connection.execute(
                    text("SELECT version FROM onboarding_sessions WHERE id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    await service.approve(
        engine,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        expected_version=version,
        actor_identity_id=actor,
    )
    return harness, settings, client, session_id, analysis, actor


async def _apply(engine, settings, client, session_id, analysis, actor, key: str):
    return await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key=key,
        actor_identity_id=actor,
    )


async def _imported(engine: AsyncEngine, tmp_path: Path, key: str):
    """One complete admission, applied."""
    harness, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    result = await _apply(engine, settings, client, session_id, analysis, actor, key)
    assert result.outcome == "applied"
    return harness


async def _project_id(engine: AsyncEngine):
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT id FROM projects WHERE project_key = :k"), {"k": PROJECT_KEY}
            )
        ).scalar_one()


async def _get(harness, path: str) -> dict:
    async with harness.api_client() as http:
        await harness.login(http, "user-owner")
        response = await http.get(path)
    assert response.status_code == 200, f"{path} -> {response.status_code}"
    return response.json()


async def _detail(harness, engine: AsyncEngine) -> dict:
    return await _get(harness, f"/v1/projects/{await _project_id(engine)}")


async def _environments(harness, engine: AsyncEngine) -> list[dict]:
    project_id = await _project_id(engine)
    return (await _get(harness, f"/v1/projects/{project_id}/environments"))["environments"]


# --- the apply -------------------------------------------------------------


async def test_the_apply_persists_an_external_environment_with_its_provider(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    await _imported(engine, tmp_path, "fs-apply-env")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT e.environment_key, e.runtime, e.hosting_provider FROM environments e "
                    "JOIN projects p ON p.id = e.project_id WHERE p.project_key = :k"
                ),
                {"k": PROJECT_KEY},
            )
        ).all()
    assert [tuple(r) for r in rows] == [("prod", "external", "vercel")]


async def test_the_apply_records_exactly_one_operator_confirmed_owner(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`fikir-sepeti` / `primary`, once, and nothing else.

    The value is an operator decision rather than a repository fact — there
    is no CODEOWNERS — so what matters is that exactly what was decided is
    what got recorded.
    """
    await _imported(engine, tmp_path, "fs-apply-owner")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT o.team_key, o.owner_role FROM project_owners o "
                    "JOIN projects p ON p.id = o.project_id WHERE p.project_key = :k"
                ),
                {"k": PROJECT_KEY},
            )
        ).all()
    assert [tuple(r) for r in rows] == [("fikir-sepeti", "primary")]


async def test_the_apply_creates_no_kubernetes_binding(engine: AsyncEngine, tmp_path: Path) -> None:
    """The check constraints would not have caught this — there is nothing
    invalid about a binding row, or about a cluster reference. Only their
    absence is correct, and only for this runtime."""
    await _imported(engine, tmp_path, "fs-apply-nok8s")
    async with engine.connect() as connection:
        # Cluster and namespace live on the environment row itself.
        cluster_id, namespace = (
            await connection.execute(
                text(
                    "SELECT e.cluster_id, e.namespace FROM environments e "
                    "JOIN projects p ON p.id = e.project_id WHERE p.project_key = :k"
                ),
                {"k": PROJECT_KEY},
            )
        ).one()
        assert cluster_id is None, "an external environment acquired a cluster"
        assert not namespace, "an external environment acquired a namespace"

        bindings = (
            await connection.execute(text("SELECT count(*) FROM service_workload_bindings"))
        ).scalar_one()
        assert bindings == 0, "a project with no workloads gained a workload binding"


async def test_the_service_persists_without_a_metrics_profile(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """NULL, not a placeholder. 0021 made the column nullable so that a
    service nothing scrapes could be recorded truthfully."""
    await _imported(engine, tmp_path, "fs-apply-svc")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT s.service_key, s.metrics_profile FROM service_definitions s "
                    "JOIN projects p ON p.id = s.project_id WHERE p.project_key = :k"
                ),
                {"k": PROJECT_KEY},
            )
        ).all()
    assert [tuple(r) for r in rows] == [("fikir-sepeti-web", None)]


async def test_the_apply_persists_the_managed_supabase_dependency(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    await _imported(engine, tmp_path, "fs-apply-dep")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT d.dependency_key, d.dependency_class, d.engine, d.store_scope, "
                    "d.provider, d.verification FROM project_dependencies d "
                    "JOIN projects p ON p.id = d.project_id WHERE p.project_key = :k"
                ),
                {"k": PROJECT_KEY},
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        (
            "fikir-sepeti-db",
            "managed_data_platform",
            "postgresql",
            "project",
            "supabase",
            "repository_intent",
        )
    ]


async def test_the_import_records_no_observation(engine: AsyncEngine, tmp_path: Path) -> None:
    """The central claim of the whole external-runtime model.

    Reading a repository establishes intent. It does not establish that
    anything was observed, and there must be no row anywhere saying it was.
    """
    await _imported(engine, tmp_path, "fs-apply-noobs")
    async with engine.connect() as connection:
        columns = (
            await connection.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE column_name IN ('last_observed_at', 'observed_at') "
                    "AND table_schema = 'public'"
                )
            )
        ).all()
        for table_name, column_name in columns:
            count = (
                await connection.execute(
                    text(f"SELECT count(*) FROM {table_name} WHERE {column_name} IS NOT NULL")  # noqa: S608
                )
            ).scalar_one()
            assert count == 0, f"{table_name}.{column_name} was set by a manifest import"


# --- idempotency -----------------------------------------------------------


async def test_a_second_import_of_the_same_manifest_duplicates_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The same document admitted twice, through two independent sessions.

    Not a repeated `apply` of one plan — that is guarded by the idempotency
    key. This is the case that actually happens: the manifest is imported
    again later and must reconcile onto the rows it already created.
    """
    await _imported(engine, tmp_path, "fs-idem-first")

    second = tmp_path / "second"
    second.mkdir()
    _harness, settings, client, session_id, analysis, actor = await _import(engine, second)
    await _apply(engine, settings, client, session_id, analysis, actor, "fs-idem-second")

    async with engine.connect() as connection:

        async def count(sql: str) -> int:
            return (await connection.execute(text(sql), {"k": PROJECT_KEY})).scalar_one()

        assert await count("SELECT count(*) FROM projects WHERE project_key = :k") == 1
        assert (
            await count(
                "SELECT count(*) FROM environments e JOIN projects p ON p.id = e.project_id "
                "WHERE p.project_key = :k"
            )
            == 1
        )
        # The owner is now planned as a real add on an existing project, so
        # a second import is exactly where a duplicate would appear.
        assert (
            await count(
                "SELECT count(*) FROM project_owners o JOIN projects p ON p.id = o.project_id "
                "WHERE p.project_key = :k"
            )
            == 1
        )
        assert (
            await count(
                "SELECT count(*) FROM service_definitions s JOIN projects p ON p.id = s.project_id "
                "WHERE p.project_key = :k"
            )
            == 1
        )
        assert (
            await count(
                "SELECT count(*) FROM project_dependencies d "
                "JOIN projects p ON p.id = d.project_id WHERE p.project_key = :k"
            )
            == 1
        )
        # And the level did not drift upward on the way through.
        verification = (
            await connection.execute(
                text(
                    "SELECT d.verification FROM project_dependencies d "
                    "JOIN projects p ON p.id = d.project_id WHERE p.project_key = :k"
                ),
                {"k": PROJECT_KEY},
            )
        ).scalar_one()
        assert verification == "repository_intent"


# --- the API ---------------------------------------------------------------


async def test_the_api_reports_external_vercel_and_no_kubernetes_identity(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness = await _imported(engine, tmp_path, "fs-api-env")
    (environment,) = await _environments(harness, engine)
    assert environment["runtime"] == "external"
    assert environment["cluster"] is None
    assert not environment["namespace"]
    assert environment["hosting_provider"] == "vercel"
    # Named as inapplicable rather than reported missing.
    assert set(environment["not_applicable"]) >= {"cluster", "namespace", "workload_binding"}
    assert environment["health"]["status"] == "unknown"
    assert environment["health"]["freshness"] == "unavailable"


async def test_the_api_reports_the_supabase_dependency_honestly(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness = await _imported(engine, tmp_path, "fs-api-dep")
    body = await _detail(harness, engine)
    (dependency,) = body["dependencies"]
    assert dependency["dependency_key"] == "fikir-sepeti-db"
    assert dependency["dependency_class"] == "managed_data_platform"
    assert dependency["provider"] == "supabase"
    assert dependency["verification"] == "repository_intent"
    assert dependency["workload_applicability"] == "not_applicable"
    assert dependency["health"]["status"] == "unknown"
    assert dependency["health"]["freshness"] == "unavailable"
    assert dependency["health"]["last_observed_at"] is None
    assert dependency["health"]["source"]["status"] == "not_configured"


async def test_the_managed_dependency_never_appears_among_services(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A Supabase database with a restart button is the failure this
    prevents. It is a dependency, and only a dependency.

    Checked against the service catalog rows rather than the per-environment
    service list, because an external environment binds no services at all —
    so an empty list there would pass this test for the wrong reason.
    """
    harness = await _imported(engine, tmp_path, "fs-api-notsvc")
    async with engine.connect() as connection:
        services = [
            r[0]
            for r in (
                await connection.execute(
                    text(
                        "SELECT s.service_key FROM service_definitions s "
                        "JOIN projects p ON p.id = s.project_id WHERE p.project_key = :k"
                    ),
                    {"k": PROJECT_KEY},
                )
            ).all()
        ]
    assert services == ["fikir-sepeti-web"]
    assert "fikir-sepeti-db" not in services

    # And the API agrees: the dependency is in `dependencies`, nowhere else.
    body = await _detail(harness, engine)
    assert [d["dependency_key"] for d in body["dependencies"]] == ["fikir-sepeti-db"]
    # The web service IS bound to prod — it is deployed there, on Vercel. The
    # thing that must not exist is the KUBERNETES workload binding, asserted
    # separately; a catalog binding and a workload binding are not the same
    # claim, and collapsing them here would have made this test pass by
    # denying that Fikir Sepeti runs anywhere.
    assert body["counts"]["services"] == 1


async def test_the_api_payload_carries_no_credential_or_endpoint(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness = await _imported(engine, tmp_path, "fs-api-leak")
    body = await _detail(harness, engine)
    serialized = str(body).lower()
    for forbidden in (
        "://",
        "supabase.co",
        "service_role",
        "anon_key",
        "password",
        "secret",
        "token",
        "connectionsecretref",
    ):
        assert forbidden not in serialized, f"project payload contains {forbidden!r}"


async def test_dependencies_do_not_leak_across_projects(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Identity is (project, dependency_key), so the same key under two
    projects must be two rows — and one project's API response must never
    carry the other's.

    The second project is created through the catalog service rather than by
    importing a second manifest: `uq_project_repo_active` allows one active
    project per repository triple, and the onboarding harness serves exactly
    one repository. Driving the same code path apply uses keeps this a test
    of the constraint and the query scope, which is what isolation means
    here, rather than a test of the fixture.
    """
    harness = await _imported(engine, tmp_path, "fs-iso-first")

    async with engine.begin() as connection:
        catalog = CatalogService(connection)
        other = await catalog.create_project(
            "neighbour",
            "Neighbour",
            repo_provider="github",
            repo_owner="Duosis-Developer-Team",
            repo_name="Neighbour",
        )
        # Deliberately the SAME dependency key Fikir Sepeti uses.
        await catalog.create_dependency(
            other.id,
            "fikir-sepeti-db",
            dependency_class="managed_data_platform",
            engine="postgresql",
            store_scope="project",
            provider="supabase",
        )

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT p.project_key, d.dependency_key FROM project_dependencies d "
                    "JOIN projects p ON p.id = d.project_id ORDER BY p.project_key"
                )
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("fikir-sepeti", "fikir-sepeti-db"),
        ("neighbour", "fikir-sepeti-db"),
    ], "the same dependency key under two projects did not produce two rows"

    # The API for Fikir Sepeti returns exactly one — its own.
    body = await _detail(harness, engine)
    assert len(body["dependencies"]) == 1
    assert body["dependencies"][0]["dependency_key"] == "fikir-sepeti-db"
