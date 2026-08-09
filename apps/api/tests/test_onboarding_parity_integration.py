"""Plan/apply parity: the plan is the instruction set, and it is complete.

Sprint 11 built a reviewable plan and then applied the MANIFEST, inferring
what to do from what already existed. The two drifted, and both directions
of the drift are silent:

- a plan could propose something apply never did — an operator approves a
  metadata change, apply takes the `link` branch, nothing happens, and the
  screen says it worked;
- apply could change something the plan never mentioned — nobody reviewed
  it because it was never shown.

The invariant this file defends, in both directions:

    every actionable item in an approved plan has exactly one apply handler
    every persistent mutation apply makes is represented in that plan
"""

import json
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app.onboarding_service import OnboardingError
from drake_api.onboarding import service
from drake_api.onboarding.model import (
    ACTIONABLE_ACTIONS,
    IMMUTABLE_ENVIRONMENT_FIELDS,
    IMMUTABLE_PROJECT_FIELDS,
    MUTABLE_ENVIRONMENT_FIELDS,
    MUTABLE_PROJECT_FIELDS,
    MUTABLE_SERVICE_FIELDS,
    Action,
    EntityKind,
    build_plan,
    canonical,
    metadata_differences,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import github_harness
from test_onboarding_integration import (
    _bootstrap,
    _identity,
    actions_for,
    bindings_for,
    datalake_manifest,
    golden_tree,
    manifest_document,
    snapshot,
)

pytestmark = pytest.mark.integration

CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts"


# ===========================================================================
# the invariant itself
# ===========================================================================


def test_every_actionable_plan_item_has_exactly_one_handler() -> None:
    """The registry is the contract. A gap here is a silent no-op in prod."""
    plan = build_plan(manifest_document(), snapshot(), repository_row_id="repo-1")
    for item in plan.items:
        if item.action not in ACTIONABLE_ACTIONS:
            continue
        assert (item.entity_kind, item.action) in service._HANDLERS, item.item_key


def test_every_registered_handler_is_reachable_from_some_plan() -> None:
    """A handler nothing can produce is dead code pretending to be coverage."""
    with_slo = manifest_document(
        slos=[
            {
                "name": "datalake-api-availability",
                "serviceRef": "datalake-api",
                "indicator": "availability",
                "objective": 99.9,
                "windowDays": 30,
            }
        ]
    )
    ids = {
        "projects": {"datalake": "p1"},
        "project_repository": {"datalake": "repo-1"},
        "environments": {("datalake", "dev"): "e1"},
        "services": {("datalake", "datalake-api"): "s1", ("datalake", "datalake-gui"): "s2"},
    }
    stale = {
        "project_metadata": {
            "datalake": {
                "project_key": "datalake",
                "display_name": "Stale",
                "criticality": "medium",
                "tenant_model": "none",
                "repo_provider": "github",
                "repo_owner": "Duosis-Developer-Team",
                "repo_name": "Hermes",
            }
        },
        "environment_metadata": {
            ("datalake", "dev"): {
                "environment_key": "dev",
                "branch": "stale-branch",
                "criticality": "medium",
                "runtime": "kubernetes",
                "namespace": "datalake-dev",
                "cluster_ref": "cluster-a",
            }
        },
        "service_metadata": {
            ("datalake", "datalake-api"): {
                "service_key": "datalake-api",
                "display_name": "datalake-api",
                "component": "api",
                "runtime": "stale-runtime",
                "metrics_profile": "fastapi-v1",
                "workload_selector": {},
                "health": {},
            }
        },
    }
    variants = [
        # fresh project → every create path
        (with_slo, snapshot()),
        # rows exist but Drake cannot see their metadata → link paths
        (with_slo, snapshot(**ids)),
        # an unclaimed project row this repository would take over → project link
        (
            with_slo,
            snapshot(
                projects={"datalake": "p1"},
                environments={("datalake", "dev"): "e1"},
                services={("datalake", "datalake-api"): "s1"},
            ),
        ),
        # an observed workload → the binding create path
        (
            with_slo,
            snapshot(
                observed_workloads={
                    ("dev", "datalake-api"): ({"kind": "Deployment", "name": "datalake-api"},)
                }
            ),
        ),
        # rows exist and differ → update paths
        (
            with_slo,
            snapshot(
                **ids,
                **stale,
                slo_definitions={
                    ("datalake", "datalake-api-availability"): (
                        "slo-1",
                        {"objective_ratio": 0.99, "window_seconds": 100},
                    )
                },
            ),
        ),
    ]
    reachable: set[tuple[str, str]] = set()
    for document, catalog in variants:
        for item in build_plan(document, catalog, repository_row_id="repo-1").items:
            if item.action in ACTIONABLE_ACTIONS:
                reachable.add((item.entity_kind, item.action))
    missing = set(service._HANDLERS) - reachable
    assert missing == set(), missing


def test_an_actionable_item_with_no_handler_stops_before_any_mutation() -> None:
    """The pre-flight check, in isolation.

    Applying the rest and reporting success would leave a catalog
    half-matching a plan somebody approved, with no way to tell which half.
    """
    unsupported = [
        {
            "entity_kind": str(EntityKind.DEPLOYMENT_SOURCE),
            "action": str(Action.CREATE),
            "item_key": "deployment_source:primary",
        },
        {
            "entity_kind": str(EntityKind.PROJECT),
            "action": str(Action.CREATE),
            "item_key": "project:x",
        },
    ]
    gaps = [
        item["item_key"]
        for item in unsupported
        if item["action"] in ACTIONABLE_ACTIONS
        and (item["entity_kind"], item["action"]) not in service._HANDLERS
    ]
    assert gaps == ["deployment_source:primary"]
    with pytest.raises(OnboardingError) as refusal:
        raise service.PlanNotApplicableError(gaps)
    assert refusal.value.code == "plan_item_unsupported"


# ===========================================================================
# update_metadata
# ===========================================================================


def test_identical_metadata_is_no_change_not_link() -> None:
    """`link` used to hide both outcomes, which is how an edit vanished."""
    current = {
        "project_key": "datalake",
        "display_name": "Datalake",
        "criticality": "medium",
        "tenant_model": "none",
        "repo_provider": "github",
        "repo_owner": "Duosis-Developer-Team",
        "repo_name": "Hermes",
    }
    plan = build_plan(
        manifest_document(),
        snapshot(
            projects={"datalake": "p1"},
            project_repository={"datalake": "repo-1"},
            project_metadata={"datalake": current},
        ),
        repository_row_id="repo-1",
    )
    assert actions_for(plan, str(EntityKind.PROJECT))["project:datalake"] == str(Action.NO_CHANGE)


def test_a_changed_mutable_field_becomes_update_metadata() -> None:
    plan = build_plan(
        manifest_document(),
        snapshot(
            projects={"datalake": "p1"},
            project_repository={"datalake": "repo-1"},
            project_metadata={
                "datalake": {
                    "project_key": "datalake",
                    "display_name": "Stale name",
                    "criticality": "medium",
                    "tenant_model": "none",
                    "repo_provider": "github",
                    "repo_owner": "Duosis-Developer-Team",
                    "repo_name": "Hermes",
                }
            },
        ),
        repository_row_id="repo-1",
    )
    item = next(entry for entry in plan.items if entry.item_key == "project:datalake")
    assert item.action == str(Action.UPDATE_METADATA)
    assert item.reason_code == "metadata_differs"
    # The FIELD names, never their values: a plan is rendered in a browser
    # and written to audit metadata.
    assert item.detail["fields"] == ["display_name"]
    assert "Stale name" not in json.dumps(item.detail)


def test_moving_an_immutable_field_is_a_conflict_not_an_update() -> None:
    """Identity is the catalog's, not the manifest's."""
    for field_name, stale in (
        ("tenant_model", "shared"),
        ("repo_owner", "someone-else"),
    ):
        current = {
            "project_key": "datalake",
            "display_name": "Datalake",
            "criticality": "medium",
            "tenant_model": "none",
            "repo_provider": "github",
            "repo_owner": "Duosis-Developer-Team",
            "repo_name": "Hermes",
        }
        current[field_name] = stale
        plan = build_plan(
            manifest_document(),
            snapshot(
                projects={"datalake": "p1"},
                project_repository={"datalake": "repo-1"},
                project_metadata={"datalake": current},
            ),
            repository_row_id="repo-1",
        )
        item = next(entry for entry in plan.items if entry.item_key == "project:datalake")
        assert item.action == str(Action.CONFLICT), field_name
        assert item.reason_code == "immutable_field_change"
        assert plan.blocking_items >= 1


def test_the_mutable_and_immutable_sets_never_overlap() -> None:
    """A field that is both would make the outcome depend on check order."""
    assert not (MUTABLE_PROJECT_FIELDS & IMMUTABLE_PROJECT_FIELDS)
    assert not (MUTABLE_ENVIRONMENT_FIELDS & IMMUTABLE_ENVIRONMENT_FIELDS)
    # Scope, identity and tenancy are never mutable by a manifest.
    for forbidden in ("project_key", "scope_id", "tenant_model", "id"):
        assert forbidden not in MUTABLE_PROJECT_FIELDS
        assert forbidden not in MUTABLE_SERVICE_FIELDS


@pytest.mark.parametrize(
    ("existing", "proposed"),
    [
        ({"branch": "main"}, {"branch": " main "}),
        ({"health": {"a": "1", "b": "2"}}, {"health": {"b": "2", "a": "1"}}),
        ({"workload_selector": {}}, {"workload_selector": {}}),
        ({"display_name": ""}, {"display_name": None}),
        ({"health": {"livePath": None}}, {"health": {}}),
    ],
)
def test_canonically_equal_metadata_produces_no_churn(
    existing: dict[str, Any], proposed: dict[str, Any]
) -> None:
    """Whitespace, key order and empty/None must not re-plan every analysis."""
    fields = frozenset(existing) | frozenset(proposed)
    assert metadata_differences(existing, proposed, fields) == []
    assert canonical(existing) == canonical(proposed)


def test_the_plan_digest_is_stable_across_identical_inputs() -> None:
    first = build_plan(manifest_document(), snapshot(), repository_row_id="repo-1")
    second = build_plan(manifest_document(), snapshot(), repository_row_id="repo-1")
    assert first.digest() == second.digest()


# ===========================================================================
# SLOs
# ===========================================================================


def test_an_slo_is_created_updated_or_left_alone() -> None:
    document = manifest_document(
        slos=[
            {
                "name": "api-availability",
                "serviceRef": "datalake-api",
                "indicator": "availability",
                "objective": 99.5,
                "windowDays": 30,
            }
        ]
    )
    fresh = build_plan(document, snapshot(), repository_row_id="repo-1")
    assert actions_for(fresh, str(EntityKind.SLO_PROFILE)) == {
        "slo_profile:api-availability": str(Action.CREATE)
    }

    identical = build_plan(
        document,
        snapshot(
            slo_definitions={
                ("datalake", "api-availability"): (
                    "slo-1",
                    {
                        "slo_key": "api-availability",
                        "display_name": "api-availability",
                        "indicator": "availability",
                        "objective_ratio": 0.995,
                        "window_seconds": 30 * 86_400,
                    },
                )
            }
        ),
        repository_row_id="repo-1",
    )
    assert actions_for(identical, str(EntityKind.SLO_PROFILE)) == {
        "slo_profile:api-availability": str(Action.NO_CHANGE)
    }

    moved = build_plan(
        document,
        snapshot(
            slo_definitions={
                ("datalake", "api-availability"): (
                    "slo-1",
                    {
                        "slo_key": "api-availability",
                        "display_name": "api-availability",
                        "indicator": "availability",
                        "objective_ratio": 0.99,
                        "window_seconds": 30 * 86_400,
                    },
                )
            }
        ),
        repository_row_id="repo-1",
    )
    item = next(entry for entry in moved.items if entry.item_key == "slo_profile:api-availability")
    assert item.action == str(Action.UPDATE_METADATA)
    assert item.detail["fields"] == ["objective_ratio"]


@pytest.mark.parametrize(
    ("objective", "window", "reason"),
    [
        (0, 30, "slo_objective_invalid"),
        (100.5, 30, "slo_objective_invalid"),
        (99.5, 0, "slo_objective_invalid"),
        (99.5, 400, "slo_objective_invalid"),
    ],
)
def test_an_unmeasurable_objective_is_refused_before_anything_is_stored(
    objective: float, window: int, reason: str
) -> None:
    """A target nobody can meet or miss is not a promise worth recording."""
    plan = build_plan(
        manifest_document(
            slos=[
                {
                    "name": "bad",
                    "serviceRef": "datalake-api",
                    "indicator": "availability",
                    "objective": objective,
                    "windowDays": window,
                }
            ]
        ),
        snapshot(),
        repository_row_id="repo-1",
    )
    item = next(entry for entry in plan.items if entry.item_key == "slo_profile:bad")
    assert item.reason_code == reason
    assert item.blocking


def test_an_slo_naming_an_undeclared_service_is_unmapped() -> None:
    plan = build_plan(
        manifest_document(
            slos=[
                {
                    "name": "ghost",
                    "serviceRef": "not-in-this-manifest",
                    "indicator": "availability",
                    "objective": 99.5,
                    "windowDays": 30,
                }
            ]
        ),
        snapshot(),
        repository_row_id="repo-1",
    )
    item = next(entry for entry in plan.items if entry.item_key == "slo_profile:ghost")
    assert item.reason_code == "slo_service_unknown"


def test_an_slo_removed_from_the_manifest_is_never_deleted() -> None:
    """Out of scope on purpose, and proved rather than assumed."""
    plan = build_plan(
        manifest_document(),
        snapshot(
            slo_definitions={
                ("datalake", "orphan"): ("slo-1", {"slo_key": "orphan"}),
            }
        ),
        repository_row_id="repo-1",
    )
    assert not any("orphan" in item.item_key for item in plan.items)
    assert "delete" not in {str(action) for action in Action}


# ===========================================================================
# workload bindings
# ===========================================================================


def test_a_binding_needs_an_observed_workload() -> None:
    without = build_plan(manifest_document(), snapshot(), repository_row_id="repo-1")
    assert bindings_for(without) == {
        "workload_binding:dev:datalake-api": str(Action.NO_CHANGE),
        "workload_binding:dev:datalake-gui": str(Action.NO_CHANGE),
    }
    assert all(
        item.entity_kind == str(EntityKind.WORKLOAD_BINDING)
        for item in without.items
        if item.item_key.startswith("workload_binding:")
    )
    # Not blocking: a project being onboarded for the first time has no
    # agent report yet, and refusing the import would make onboarding
    # impossible before the agent runs.
    assert without.blocking_items == 0

    with_evidence = build_plan(
        manifest_document(),
        snapshot(
            observed_workloads={
                ("dev", "datalake-api"): ({"kind": "Deployment", "name": "datalake-api"},)
            }
        ),
        repository_row_id="repo-1",
    )
    item = next(
        entry
        for entry in with_evidence.items
        if entry.item_key == "workload_binding:dev:datalake-api"
    )
    assert item.action == str(Action.CREATE)
    assert item.entity_kind == str(EntityKind.WORKLOAD_BINDING)
    # The values apply will execute, in the plan and therefore in the digest.
    assert item.payload["workload_kind"] == "Deployment"
    assert item.payload["workload_name"] == "datalake-api"


def test_two_matching_workloads_block_rather_than_choosing() -> None:
    """Picking one attributes another workload's health to this service."""
    plan = build_plan(
        manifest_document(),
        snapshot(
            observed_workloads={
                ("dev", "datalake-api"): (
                    {"kind": "Deployment", "name": "api-blue"},
                    {"kind": "Deployment", "name": "api-green"},
                )
            }
        ),
        repository_row_id="repo-1",
    )
    item = next(
        entry for entry in plan.items if entry.item_key == "workload_binding:dev:datalake-api"
    )
    assert item.action == str(Action.UNMAPPED)
    assert item.reason_code == "binding_ambiguous"
    assert plan.blocking_items >= 1


def test_an_existing_binding_is_no_change() -> None:
    plan = build_plan(
        manifest_document(),
        snapshot(
            existing_bindings={
                ("dev", "datalake-api"): {
                    "id": "b1",
                    "workload_kind": "Deployment",
                    "workload_name": "datalake-api",
                }
            }
        ),
        repository_row_id="repo-1",
    )
    item = next(
        entry for entry in plan.items if entry.item_key == "workload_binding:dev:datalake-api"
    )
    assert item.action == str(Action.NO_CHANGE)


# ===========================================================================
# deployment source
# ===========================================================================


def test_deployment_source_claims_nothing_apply_cannot_do() -> None:
    """The catalog has no column for it, so `link` would be a lie."""
    from drake_api.onboarding.model import deployment_source_item

    assert deployment_source_item([{"kind": "runtime", "value": "python"}]) is None
    item = deployment_source_item(
        [{"kind": "deployment", "value": "helm", "evidence": "Chart.yaml present"}]
    )
    assert item is not None
    assert item.action == str(Action.NO_CHANGE)
    assert item.reason_code == "deployment_source_informational"
    assert item.action not in ACTIONABLE_ACTIONS


# ===========================================================================
# end to end: what the plan promised is what the catalog got
# ===========================================================================


async def _plan_and_apply(
    harness: Any,
    engine: AsyncEngine,
    row_id: uuidlib.UUID,
    actor: uuidlib.UUID,
    *,
    key: str = "apply-parity-0001",
) -> Any:
    settings = harness.app.state.settings
    client = harness.app.state.github_client
    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
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
    outcome = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key=key,
        actor_identity_id=actor,
    )
    return session_id, analysis, outcome


def manifest_with_slo() -> str:
    """The sanitized Datalake fixture, which already declares one SLO.

    Appending a second `slos:` block would be a duplicate key — which the
    parser now refuses, and rightly: a reviewer would see one and the parser
    would use the other.
    """
    return datalake_manifest()


@pytest.mark.anyio
async def test_an_slo_in_the_manifest_reaches_slo_definitions(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Sprint 11 proposed a profile and stored nothing."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree(manifest_with_slo()))
    actor = await _identity(engine)

    _session, _analysis, outcome = await _plan_and_apply(harness, engine, row_id, actor)
    assert outcome.outcome == "applied"
    assert outcome.slo_definitions_created == 1

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT slo_key, indicator, objective_ratio, window_seconds, "
                    "sli_template_key FROM slo_definitions WHERE project_id = :p"
                ),
                {"p": outcome.project_id},
            )
        ).one()
    assert row[0] == "datalake-api-availability"
    assert row[1] == "availability"
    # A PERCENTAGE in the manifest, a RATIO in the database. Converted once.
    assert float(row[2]) == pytest.approx(0.995)
    assert row[3] == 30 * 86_400
    # A curated template key, never an expression.
    assert row[4] == "service.error-ratio.v1"


@pytest.mark.anyio
async def test_a_changed_manifest_actually_updates_the_catalog(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The defect this slice exists for: approve an edit, get the edit."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    _s, _a, first = await _plan_and_apply(harness, engine, row_id, actor, key="first-0001")
    assert first.outcome == "applied"

    # The manifest changes a MUTABLE field and the branch moves with it.
    edited = datalake_manifest().replace(
        "displayName: Datalake Platform", "displayName: Datalake Platform (renamed)"
    )
    fake.branch_heads["Hermes"] = "d" * 40
    fake.trees["Hermes"] = golden_tree(edited)

    session_id, analysis, second = await _plan_and_apply(
        harness, engine, row_id, actor, key="second-0001"
    )
    assert second.outcome == "applied"
    assert second.metadata_updated >= 1

    async with engine.connect() as connection:
        display_name = (
            await connection.execute(
                text("SELECT display_name FROM projects WHERE id = :p"),
                {"p": second.project_id},
            )
        ).scalar_one()
        item = (
            await connection.execute(
                text(
                    "SELECT action, reason_code FROM onboarding_plan_items i "
                    "JOIN onboarding_plans p ON p.id = i.plan_id "
                    "WHERE p.session_id = :s AND i.item_key = 'project:datalake'"
                ),
                {"s": session_id},
            )
        ).one()
    assert display_name == "Datalake Platform (renamed)"
    assert item[0] == "update_metadata"
    assert item[1] == "metadata_differs"
    _ = analysis


@pytest.mark.anyio
async def test_every_mutation_apply_made_is_represented_in_the_approved_plan(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The invariant, checked against a real committed apply.

    Each catalog row the apply produced is traced back to an actionable item
    in the plan that was approved. A row with no item behind it would be a
    change nobody reviewed.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree(manifest_with_slo()))
    actor = await _identity(engine)
    session_id, _analysis, outcome = await _plan_and_apply(harness, engine, row_id, actor)
    assert outcome.outcome == "applied"

    async with engine.connect() as connection:
        items = {
            str(row[0]): (str(row[1]), str(row[2]))
            for row in (
                await connection.execute(
                    text(
                        "SELECT i.item_key, i.entity_kind, i.action "
                        "FROM onboarding_plan_items i "
                        "JOIN onboarding_plans p ON p.id = i.plan_id "
                        "WHERE p.session_id = :s"
                    ),
                    {"s": session_id},
                )
            ).all()
        }
        project_keys = [
            str(row[0])
            for row in (await connection.execute(text("SELECT project_key FROM projects"))).all()
        ]
        environment_keys = [
            str(row[0])
            for row in (
                await connection.execute(text("SELECT environment_key FROM environments"))
            ).all()
        ]
        service_keys = [
            str(row[0])
            for row in (
                await connection.execute(text("SELECT service_key FROM service_definitions"))
            ).all()
        ]
        slo_keys = [
            str(row[0])
            for row in (await connection.execute(text("SELECT slo_key FROM slo_definitions"))).all()
        ]
        repository_links = int(
            (
                await connection.execute(text("SELECT count(*) FROM github_repository_projects"))
            ).scalar_one()
        )

    for key in project_keys:
        assert items.get(f"project:{key}", ("", ""))[1] in ACTIONABLE_ACTIONS
    for key in environment_keys:
        assert items.get(f"environment:{key}", ("", ""))[1] in ACTIONABLE_ACTIONS
    for key in service_keys:
        assert items.get(f"service:{key}", ("", ""))[1] in ACTIONABLE_ACTIONS
    for key in slo_keys:
        assert items.get(f"slo_profile:{key}", ("", ""))[1] in ACTIONABLE_ACTIONS
    if repository_links:
        assert items.get("repository:datalake", ("", ""))[1] in ACTIONABLE_ACTIONS


@pytest.mark.anyio
async def test_no_approved_actionable_item_is_silently_skipped(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The other direction of the same invariant."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree(manifest_with_slo()))
    actor = await _identity(engine)
    session_id, _analysis, outcome = await _plan_and_apply(harness, engine, row_id, actor)

    async with engine.connect() as connection:
        actionable = [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in (
                await connection.execute(
                    text(
                        "SELECT i.entity_kind, i.action, i.item_key "
                        "FROM onboarding_plan_items i "
                        "JOIN onboarding_plans p ON p.id = i.plan_id "
                        "WHERE p.session_id = :s AND i.action = ANY(:actions)"
                    ),
                    {"s": session_id, "actions": sorted(ACTIONABLE_ACTIONS)},
                )
            ).all()
        ]
    assert actionable, "the golden path should propose real work"
    for entity_kind, action, item_key in actionable:
        assert (entity_kind, action) in service._HANDLERS, item_key
    _ = outcome


@pytest.mark.anyio
async def test_applying_twice_updates_nothing_a_second_time(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree(manifest_with_slo()))
    actor = await _identity(engine)
    session_id, analysis, first = await _plan_and_apply(harness, engine, row_id, actor)

    repeat = await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="apply-parity-0001",
        actor_identity_id=actor,
    )
    assert first.outcome == "applied"
    assert repeat.outcome == "unchanged"

    async with engine.connect() as connection:
        counts = {
            table: int(
                (
                    await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                ).scalar_one()
            )
            for table in (
                "projects",
                "environments",
                "service_definitions",
                "slo_definitions",
                "onboarding_applies",
                "github_repository_projects",
            )
        }
    assert counts["projects"] == 1
    assert counts["slo_definitions"] == 1
    assert counts["onboarding_applies"] == 1
    assert counts["github_repository_projects"] == 1


@pytest.mark.anyio
async def test_a_failure_mid_apply_leaves_no_catalog_rows_behind(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """One transaction: half a plan applied and reported as success would
    leave a catalog nobody can reconcile against the plan they approved."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree(manifest_with_slo()))
    actor = await _identity(engine)
    settings = harness.app.state.settings
    client = harness.app.state.github_client

    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
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

    # The SLO handler fails after the project and its services are written.
    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise OnboardingError("injected", "handler failed")

    original = service._HANDLERS[(str(EntityKind.SLO_PROFILE), str(Action.CREATE))]
    service._HANDLERS[(str(EntityKind.SLO_PROFILE), str(Action.CREATE))] = explode
    try:
        with pytest.raises(OnboardingError):
            await service.apply(
                engine,
                settings,
                client,
                session_id=session_id,
                plan_version=analysis["plan_version"],
                idempotency_key="rollback-0001",
                actor_identity_id=actor,
            )
    finally:
        service._HANDLERS[(str(EntityKind.SLO_PROFILE), str(Action.CREATE))] = original

    async with engine.connect() as connection:
        counts = {
            table: int(
                (
                    await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                ).scalar_one()
            )
            for table in ("projects", "environments", "service_definitions", "onboarding_applies")
        }
    # Nothing at all: not the project, not the idempotency claim.
    assert counts == {
        "projects": 0,
        "environments": 0,
        "service_definitions": 0,
        "onboarding_applies": 0,
    }

    # And the apply can be retried cleanly afterwards.
    retried = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="rollback-0002",
        actor_identity_id=actor,
    )
    assert retried.outcome == "applied"
