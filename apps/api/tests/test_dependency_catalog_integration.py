"""A managed dependency, from manifest to API response.

The gap this closes: `dataStores` were validated and then discarded. A
provider-managed database survived schema validation and drift and vanished
at import, so `provider` and `verification` were decorative.

The whole chain is exercised here — parse, plan, transactional apply, real
PostgreSQL, API serialization — because each link previously existed and the
one in the middle silently dropped the row.
"""

import uuid as uuidlib
from pathlib import Path

import pytest
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


# Built on the SAME repository and owner team the golden path already
# resolves — `_bootstrap` seeds repository `Hermes` and its owner team, and a
# manifest naming anything else produces unmapped items that block the apply
# for reasons unrelated to dependencies.
EXTERNAL_MANIFEST = """
apiVersion: drake.duosis.com/v1alpha1
kind: ProjectObservability
metadata:
  name: epsilon
  displayName: Epsilon
spec:
  repository:
    provider: github
    owner: Duosis-Developer-Team
    name: Hermes
    defaultBranch: main
  owners:
    - team: data-platform
      role: primary
  environments:
    - name: prod
      runtime: external
      branch: main
      criticality: medium
      hostingProvider: vercel
  services:
    - name: epsilon-web
      component: web
      runtime: nextjs
  tenantModel:
    mode: none
  dataStores:
    - name: epsilon-db
      engine: postgresql
      scope: project
      dependencyClass: managed_data_platform
      provider: supabase
      # A repository asserting that Drake OBSERVED something. The import
      # must record repository_intent regardless.
      verification: provider_observed
    - name: epsilon-cache
      engine: redis
      scope: environment
      measurementProfile: postgres-v1
"""


async def _import(engine: AsyncEngine, tmp_path: Path, manifest: str = EXTERNAL_MANIFEST):
    """Run one full onboarding to completion and return the harness pieces."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree(manifest))
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


async def _rows(engine: AsyncEngine) -> list[dict]:
    async with engine.connect() as connection:
        return [
            {
                "key": r[0],
                "class": r[1],
                "engine": r[2],
                "scope": r[3],
                "provider": r[4],
                "verification": r[5],
                "version": r[6],
            }
            for r in (
                await connection.execute(
                    text(
                        "SELECT dependency_key, dependency_class, engine, store_scope, provider, "
                        "verification, version FROM project_dependencies ORDER BY dependency_key"
                    )
                )
            ).all()
        ]


async def test_the_plan_creates_a_dependency_and_no_workload(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    await _import(engine, tmp_path)
    # Read from the persisted plan rather than the analyze() response: the
    # plan rows are what the approval covers and what apply executes.
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT entity_kind, action, item_key, detail FROM onboarding_plan_items "
                    "ORDER BY entity_kind, item_key"
                )
            )
        ).all()
    kinds = [r[0] for r in rows]
    assert kinds.count("dependency") == 2
    # The point of the whole exercise: nothing Kubernetes-shaped.
    assert "workload_binding" not in kinds
    assert "cluster_binding" not in kinds
    assert "namespace_binding" not in kinds

    dependency = next(r for r in rows if r[0] == "dependency")
    assert dict(dependency[3])["workload_applicability"] == "not_applicable"


async def test_apply_persists_class_provider_and_verification(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    result = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-apply-1",
        actor_identity_id=actor,
    )
    assert result.outcome == "applied"

    rows = await _rows(engine)
    assert [r["key"] for r in rows] == ["epsilon-cache", "epsilon-db"]

    managed = next(r for r in rows if r["key"] == "epsilon-db")
    assert managed["class"] == "managed_data_platform"
    assert managed["provider"] == "supabase"
    # The manifest asked for provider_observed. A repository cannot attest to
    # Drake having observed anything, so the import clamps it.
    assert managed["verification"] == "repository_intent"

    in_cluster = next(r for r in rows if r["key"] == "epsilon-cache")
    assert in_cluster["class"] == "in_cluster"
    # A provider on something Drake runs itself would be a claim about its
    # own infrastructure; the database refuses it too.
    assert in_cluster["provider"] is None


async def test_applying_twice_creates_no_duplicate_dependency(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    for _ in range(2):
        await service.apply(
            engine,
            settings,
            client,
            session_id=session_id,
            plan_version=analysis["plan_version"],
            idempotency_key="dep-apply-idem",
            actor_identity_id=actor,
        )
    rows = await _rows(engine)
    assert len(rows) == 2, "a repeated apply produced duplicate dependencies"


async def test_a_second_import_reconciles_rather_than_duplicating(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The same project, imported again with changed metadata.

    Identity is (project, dependency_key), so the second import must find
    the existing row rather than create a parallel one.
    """
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-first-import",
        actor_identity_id=actor,
    )
    before = await _rows(engine)

    # Same project, same dependency key, different mutable metadata.
    changed = EXTERNAL_MANIFEST.replace("scope: project", "scope: environment", 1)
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    _, settings2, client2, session2, analysis2, actor2 = await _import(engine, second_dir, changed)
    await service.apply(
        engine,
        settings2,
        client2,
        session_id=session2,
        plan_version=analysis2["plan_version"],
        idempotency_key="dep-second-import",
        actor_identity_id=actor2,
    )

    after = await _rows(engine)
    assert len(after) == len(before) == 2, "reconcile created a parallel row"
    managed = next(r for r in after if r["key"] == "epsilon-db")
    assert managed["scope"] == "environment", "the mutable field did not reconcile"
    assert managed["class"] == "managed_data_platform", "an immutable field changed"


async def test_the_api_round_trips_every_dependency_field(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-api-roundtrip",
        actor_identity_id=actor,
    )

    async with engine.connect() as connection:
        project_id = (
            await connection.execute(text("SELECT id FROM projects WHERE project_key = 'epsilon'"))
        ).scalar_one()

    async with harness.api_client() as http:
        await harness.login(http, "user-owner")
        response = await http.get(f"/v1/projects/{project_id}")
    assert response.status_code == 200
    body = response.json()
    dependencies = {d["dependency_key"]: d for d in body["dependencies"]}
    assert set(dependencies) == {"epsilon-db", "epsilon-cache"}

    managed = dependencies["epsilon-db"]
    assert managed["dependency_class"] == "managed_data_platform"
    assert managed["provider"] == "supabase"
    assert managed["verification"] == "repository_intent"
    assert managed["workload_applicability"] == "not_applicable"
    # No observation exists, and importing a manifest is not one.
    assert managed["health"]["status"] == "unknown"
    assert managed["health"]["freshness"] == "unavailable"
    assert managed["health"]["last_observed_at"] is None

    # A provider nobody recorded reads as `unknown`, not as null.
    assert dependencies["epsilon-cache"]["provider"] == "unknown"


async def test_a_dependency_carries_no_credential_or_endpoint(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-leak-scan",
        actor_identity_id=actor,
    )
    async with engine.connect() as connection:
        project_id = (
            await connection.execute(text("SELECT id FROM projects WHERE project_key = 'epsilon'"))
        ).scalar_one()
    async with harness.api_client() as http:
        await harness.login(http, "user-owner")
        body = (await http.get(f"/v1/projects/{project_id}")).json()
    serialized = str(body["dependencies"]).lower()
    for forbidden in ("://", "password", "secret", "token", "connectionsecretref", "key="):
        assert forbidden not in serialized, f"dependency payload contains {forbidden!r}"


async def test_a_principal_without_project_scope_sees_no_dependency(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-rbac-scope",
        actor_identity_id=actor,
    )
    async with engine.connect() as connection:
        project_id = (
            await connection.execute(text("SELECT id FROM projects WHERE project_key = 'epsilon'"))
        ).scalar_one()

    # A principal with no grant on this project must not receive the project
    # at all, so its dependencies cannot leak with it. `user-plain` exists in
    # the fake provider and holds no project grant — an unknown subject would
    # fail at login and prove nothing about authorization.
    async with harness.api_client() as http:
        await harness.login(http, "user-plain")
        response = await http.get(f"/v1/projects/{project_id}")
    assert response.status_code == 404
    assert "supabase" not in response.text.lower()
