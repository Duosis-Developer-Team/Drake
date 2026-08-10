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
from drake_api.github_app.onboarding_service import OnboardingError
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

    # Class-aware, per dependency: Drake runs the in-cluster cache, so its
    # workload semantics apply; it does not run the managed platform.
    detail_by_key = {r[2].split(":", 1)[1]: dict(r[3]) for r in rows if r[0] == "dependency"}
    assert detail_by_key["epsilon-db"]["workload_applicability"] == "not_applicable"
    assert detail_by_key["epsilon-cache"]["workload_applicability"] == "applicable"


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


# --------------------------------------------------------------------------
# Review fixes, end to end
# --------------------------------------------------------------------------


async def _set_verification(engine: AsyncEngine, key: str, level: str) -> None:
    """Stand in for an out-of-band confirmation or a real observation.

    Written directly because there is no import path that can produce these
    levels — which is the property the tests below defend.
    """
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE project_dependencies SET verification = :v WHERE dependency_key = :k"),
            {"v": level, "k": key},
        )


@pytest.mark.parametrize("held", ["owner_confirmed", "provider_observed"])
async def test_a_reimport_does_not_erase_out_of_band_verification(
    engine: AsyncEngine, tmp_path: Path, held: str
) -> None:
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key=f"dep-hold-{held}",
        actor_identity_id=actor,
    )
    await _set_verification(engine, "epsilon-db", held)

    second = tmp_path / f"again-{held}"
    second.mkdir()
    _, settings2, client2, session2, analysis2, actor2 = await _import(engine, second)
    await service.apply(
        engine,
        settings2,
        client2,
        session_id=session2,
        plan_version=analysis2["plan_version"],
        idempotency_key=f"dep-again-{held}",
        actor_identity_id=actor2,
    )

    rows = await _rows(engine)
    managed = next(r for r in rows if r["key"] == "epsilon-db")
    assert managed["verification"] == held, "a re-import destroyed established evidence"


async def test_a_provider_change_with_held_evidence_blocks_rather_than_resetting(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-provider-1",
        actor_identity_id=actor,
    )
    await _set_verification(engine, "epsilon-db", "provider_observed")

    # Evidence obtained for supabase does not carry to another provider, and
    # an import must not silently reset it either.
    moved = EXTERNAL_MANIFEST.replace("provider: supabase", "provider: aws", 1)
    second = tmp_path / "moved"
    second.mkdir()
    with pytest.raises(OnboardingError, match="conflicts or unmapped"):
        await _import(engine, second, moved)

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT action, reason_code FROM onboarding_plan_items "
                    "WHERE item_key = 'dependency:epsilon-db' AND action = 'conflict'"
                )
            )
        ).all()
    assert rows, "a provider change over held evidence produced no conflict"
    assert rows[0][1] == "dependency_provider_change_resets_verification"

    # And nothing was applied: the stored evidence is untouched.
    managed = next(r for r in await _rows(engine) if r["key"] == "epsilon-db")
    assert managed["verification"] == "provider_observed"
    assert managed["provider"] == "supabase"


async def test_a_removed_dependency_is_reported_not_deleted(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-removal-1",
        actor_identity_id=actor,
    )

    # Drop the cache from the manifest entirely.
    without_cache = EXTERNAL_MANIFEST.split("    - name: epsilon-cache")[0]
    second = tmp_path / "removed"
    second.mkdir()
    # The catalog-only item is unmapped, which blocks — deliberately.
    with pytest.raises(OnboardingError, match="conflicts or unmapped"):
        await _import(engine, second, without_cache)

    async with engine.connect() as connection:
        catalog_only = (
            await connection.execute(
                text(
                    "SELECT action, reason_code, existing_name FROM onboarding_plan_items "
                    "WHERE item_key = 'dependency_catalog_only:epsilon-cache'"
                )
            )
        ).all()
    assert catalog_only, "a dependency removed from the manifest produced no plan item"
    assert catalog_only[0][0] == "unmapped"
    assert catalog_only[0][1] == "catalog_only"

    # Neither deleted nor archived: Drake does not remove on a diff.
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT lifecycle FROM project_dependencies "
                    "WHERE dependency_key = 'epsilon-cache'"
                )
            )
        ).all()
    assert row, "the dependency row was silently deleted"
    assert row[0][0] == "active", "the dependency row was silently archived"


async def test_an_unchanged_reimport_reports_no_catalog_only(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-nofalse-1",
        actor_identity_id=actor,
    )
    second = tmp_path / "same"
    second.mkdir()
    await _import(engine, second)

    async with engine.connect() as connection:
        false_positives = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM onboarding_plan_items "
                    "WHERE reason_code = 'catalog_only' AND item_key LIKE 'dependency_%'"
                )
            )
        ).scalar_one()
    assert false_positives == 0, "an unchanged re-import reported a phantom removal"


async def test_workload_applicability_is_class_aware_end_to_end(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-applicability",
        actor_identity_id=actor,
    )
    async with engine.connect() as connection:
        project_id = (
            await connection.execute(text("SELECT id FROM projects WHERE project_key = 'epsilon'"))
        ).scalar_one()
    async with harness.api_client() as http:
        await harness.login(http, "user-owner")
        body = (await http.get(f"/v1/projects/{project_id}")).json()

    by_key = {d["dependency_key"]: d for d in body["dependencies"]}
    # Drake runs the cache: workload semantics apply, and no external health
    # verdict speaks for it.
    assert by_key["epsilon-cache"]["workload_applicability"] == "applicable"
    assert "health" not in by_key["epsilon-cache"]
    # Drake does not run the managed platform.
    assert by_key["epsilon-db"]["workload_applicability"] == "not_applicable"
    assert by_key["epsilon-db"]["health"]["status"] == "unknown"


async def test_another_project_with_the_same_dependency_key_is_not_reconciled(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Identity is (project, key). A same-named dependency elsewhere must not
    appear as this project's catalog-only removal."""
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-crossproj",
        actor_identity_id=actor,
    )
    async with engine.begin() as connection:
        other_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, display_name) "
                    "VALUES ('project', 'otherproj', 'other') RETURNING id"
                )
            )
        ).scalar_one()
        other = (
            await connection.execute(
                text(
                    "INSERT INTO projects (project_key, display_name, repo_provider, repo_owner, "
                    "repo_name, default_branch, criticality, tenant_model, catalog_source_kind, "
                    "catalog_source_ref, source_revision, scope_id) VALUES "
                    "('otherproj', 'other', 'github', 'o', 'r2', 'main', 'low', 'none', "
                    "'fixture', 'test', 'rev', :s) RETURNING id"
                ),
                {"s": other_scope},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO project_dependencies (project_id, dependency_key, display_name, "
                "dependency_class, engine, store_scope, verification, catalog_source_kind, "
                "catalog_source_ref, source_revision) VALUES "
                "(:p, 'epsilon-db', 'epsilon-db', 'in_cluster', 'postgresql', 'project', "
                "'repository_intent', 'fixture', 'test', 'rev')"
            ),
            {"p": other},
        )

    second = tmp_path / "cross"
    second.mkdir()
    await _import(engine, second)

    async with engine.connect() as connection:
        items = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM onboarding_plan_items "
                    "WHERE item_key LIKE 'dependency_catalog_only:%'"
                )
            )
        ).scalar_one()
    assert items == 0, "another project's dependency leaked into this plan"


async def test_a_metadata_change_is_auditable_field_by_field(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A counter is not evidence.

    `metadata_updated=1` says something changed; it does not say what it was
    or what it became. The plan item carries before/after per field, which is
    what makes the approval informed and the change reviewable afterwards.
    """
    _, settings, client, session_id, analysis, actor = await _import(engine, tmp_path)
    await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="dep-audit-1",
        actor_identity_id=actor,
    )

    changed = EXTERNAL_MANIFEST.replace("scope: project", "scope: environment", 1)
    second = tmp_path / "audited"
    second.mkdir()
    await _import(engine, second, changed)

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    # `changes` is nested inside `detail`; the table has no
                    # column of its own for it.
                    "SELECT action, detail FROM onboarding_plan_items "
                    "WHERE item_key = 'dependency:epsilon-db' AND action = 'update_metadata' "
                    "ORDER BY id DESC LIMIT 1"
                )
            )
        ).all()
    assert row, "a changed dependency produced no update_metadata item"
    changes = dict((row[0][1] or {}).get("changes") or {})
    assert "store_scope" in changes, f"the changed field is not itemised: {sorted(changes)}"
    assert changes["store_scope"]["before"] == "project"
    assert changes["store_scope"]["after"] == "environment"
    # The verification that was preserved is visible as unchanged rather
    # than absent from the record.
    assert "verification" not in changes or (
        changes["verification"]["before"] == changes["verification"]["after"]
    )
