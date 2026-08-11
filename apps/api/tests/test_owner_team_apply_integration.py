"""Ownership survives the whole apply, not just the create path.

The defect: `project_owners` was written in exactly one place —
`_apply_project_create` — and every owner plan item said `no_change` /
`applied_with_parent`. That reads as "created with its parent", and it is
true right up until the project already exists. Then no parent is created,
nothing writes the row, and the plan reported success for work it never did.

Nothing caught it because the two halves agreed with each other: the plan
said there was nothing to do, and apply did nothing.

These tests drive both halves against real PostgreSQL and check them
against the DATABASE rather than against each other.
"""

from __future__ import annotations

import uuid as uuidlib
from pathlib import Path

import pytest
from drake_api.catalog.service import CatalogService
from drake_api.onboarding import service
from drake_api.onboarding.model import CatalogSnapshot, build_plan
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

PROJECT_KEY = "ownership"


def document(owners: str) -> str:
    """A minimal manifest whose only interesting axis is ownership."""
    return f"""
apiVersion: drake.duosis.com/v1alpha1
kind: ProjectObservability
metadata:
  name: {PROJECT_KEY}
  displayName: Ownership
spec:
  repository:
    provider: github
    owner: Duosis-Developer-Team
    name: Hermes
    defaultBranch: main
  owners:
{owners}
  environments:
    - name: prod
      runtime: external
      branch: main
      criticality: medium
      hostingProvider: vercel
  services:
    - name: ownership-web
      component: web
      runtime: nextjs
  tenantModel:
    mode: none
"""


ONE_OWNER = document("    - team: alpha-team\n      role: primary\n")
TWO_OWNERS = document(
    "    - team: alpha-team\n      role: primary\n    - team: beta-team\n      role: secondary\n"
)


async def _import(engine: AsyncEngine, tmp_path: Path, manifest: str):
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


async def _run(engine: AsyncEngine, path: Path, manifest: str, key: str) -> list[dict]:
    """One import applied; returns the owner plan items."""
    path.mkdir(exist_ok=True)
    _h, settings, client, session_id, analysis, actor = await _import(engine, path, manifest)
    result = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key=key,
        actor_identity_id=actor,
    )
    assert result.outcome == "applied"
    async with engine.connect() as connection:
        return [
            {"key": r[0], "action": r[1], "reason": r[2]}
            for r in (
                await connection.execute(
                    text(
                        "SELECT item_key, action, reason_code FROM onboarding_plan_items i "
                        "JOIN onboarding_plans p ON p.id = i.plan_id "
                        "WHERE i.entity_kind = 'owner_team' AND p.session_id = :s "
                        "ORDER BY item_key"
                    ),
                    {"s": session_id},
                )
            ).all()
        ]


async def _owners(engine: AsyncEngine, project_key: str = PROJECT_KEY) -> list[tuple[str, str]]:
    async with engine.connect() as connection:
        return [
            (str(r[0]), str(r[1]))
            for r in (
                await connection.execute(
                    text(
                        "SELECT o.team_key, o.owner_role FROM project_owners o "
                        "JOIN projects p ON p.id = o.project_id WHERE p.project_key = :k "
                        "ORDER BY o.team_key, o.owner_role"
                    ),
                    {"k": project_key},
                )
            ).all()
        ]


# --- new project -----------------------------------------------------------


async def test_a_new_project_records_its_owner(engine: AsyncEngine, tmp_path: Path) -> None:
    items = await _run(engine, tmp_path / "first", ONE_OWNER, "own-new-project")
    assert items == [
        {
            "key": "owner_team:alpha-team:primary",
            "action": "no_change",
            "reason": "applied_with_parent",
        }
    ]
    assert await _owners(engine) == [("alpha-team", "primary")]


# --- one team, two roles, one manifest -------------------------------------


SAME_TEAM_TWO_ROLES = document(
    "    - team: alpha-team\n      role: primary\n    - team: alpha-team\n      role: secondary\n"
)


async def test_one_team_in_two_roles_produces_two_persisted_plan_items(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Two associations, two reviewable items, two rows that survive.

    The item key used to be `owner_team:{team}`, so these two collided.
    `onboarding_plan_items` is `UNIQUE(plan_id, item_key)` written with
    `ON CONFLICT DO NOTHING`, so the second was silently discarded — the
    plan digest was computed over two items and the persisted plan held
    one. The approval then covered a plan that no longer represented it.

    Reading the items back FROM THE DATABASE is the whole point: an
    in-memory assertion would have passed throughout.
    """
    items = await _run(engine, tmp_path / "first", SAME_TEAM_TWO_ROLES, "own-two-roles")
    assert [item["key"] for item in items] == [
        "owner_team:alpha-team:primary",
        "owner_team:alpha-team:secondary",
    ]
    assert await _owners(engine) == [("alpha-team", "primary"), ("alpha-team", "secondary")]


async def test_re_importing_one_team_in_two_roles_changes_neither(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    await _run(engine, tmp_path / "first", SAME_TEAM_TWO_ROLES, "own-two-roles-1")
    items = await _run(engine, tmp_path / "second", SAME_TEAM_TWO_ROLES, "own-two-roles-2")

    assert {item["action"] for item in items} == {"no_change"}
    assert {item["reason"] for item in items} == {"owner_team_already_recorded"}
    assert await _owners(engine) == [("alpha-team", "primary"), ("alpha-team", "secondary")]


async def test_the_persisted_plan_verifies_against_the_digest_it_was_approved_under(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Integrity, checked exactly the way apply checks it.

    `plan_digest` is computed from the IN-MEMORY plan and stored;
    `verify_plan_integrity` recomputes it from the STORED items and refuses
    on a mismatch. With a colliding item key the two disagreed by one item,
    so this manifest was not merely mis-recorded — it could not be applied
    at all, and the refusal named plan tampering rather than the duplicate
    key that caused it.

    Called directly rather than inferred from a successful apply, because
    apply runs this check twice and a green apply would prove it only
    incidentally.
    """
    _h, settings, client, session_id, analysis, actor = await _import(
        engine, tmp_path, SAME_TEAM_TWO_ROLES
    )
    async with engine.connect() as connection:
        plan_id, plan_digest = (
            await connection.execute(
                text("SELECT id, plan_digest FROM onboarding_plans WHERE session_id = :s"),
                {"s": session_id},
            )
        ).one()
        # Raises PlanIntegrityError if the stored items no longer hash to
        # the digest the approval was taken over.
        items = await service.verify_plan_integrity(connection, plan_id, str(plan_digest))

    owner_keys = [i["item_key"] for i in items if i["entity_kind"] == "owner_team"]
    assert owner_keys == [
        "owner_team:alpha-team:primary",
        "owner_team:alpha-team:secondary",
    ], "both associations must survive persistence, or the digest cannot match"

    # And the apply that re-runs this check still succeeds end to end.
    result = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="own-digest-check",
        actor_identity_id=actor,
    )
    assert result.outcome == "applied"


# --- existing project, owner missing ---------------------------------------


async def test_an_owner_added_to_an_existing_project_is_planned_and_applied(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The regression. Import once, then import again with a second owner.

    Before the fix the second import planned `no_change` and wrote nothing,
    so `beta-team` never existed and the plan looked like it had succeeded.
    """
    await _run(engine, tmp_path / "first", ONE_OWNER, "own-add-1")
    assert await _owners(engine) == [("alpha-team", "primary")]

    items = await _run(engine, tmp_path / "second", TWO_OWNERS, "own-add-2")
    by_key = {item["key"]: item for item in items}
    # The one already recorded is genuinely nothing to do...
    assert by_key["owner_team:alpha-team:primary"]["action"] == "no_change"
    assert by_key["owner_team:alpha-team:primary"]["reason"] == "owner_team_already_recorded"
    # ...and the new one is a real add, said out loud.
    assert by_key["owner_team:beta-team:secondary"]["action"] == "create"

    # And the plan is confirmed by the database, not by the plan.
    assert await _owners(engine) == [("alpha-team", "primary"), ("beta-team", "secondary")]


async def test_re_importing_the_same_owners_adds_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    await _run(engine, tmp_path / "first", TWO_OWNERS, "own-idem-1")
    before = await _owners(engine)
    items = await _run(engine, tmp_path / "second", TWO_OWNERS, "own-idem-2")

    assert {item["action"] for item in items} == {"no_change"}
    assert {item["reason"] for item in items} == {"owner_team_already_recorded"}
    assert (
        await _owners(engine)
        == before
        == [
            ("alpha-team", "primary"),
            ("beta-team", "secondary"),
        ]
    )


async def test_an_owner_the_manifest_drops_is_not_removed(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Non-destructive, deliberately.

    A manifest edit is not evidence that somebody's recorded ownership
    decision was withdrawn, and a catalog that deletes on a diff will one
    day delete on a mistake. Removing an owner stays a human act.
    """
    await _run(engine, tmp_path / "first", TWO_OWNERS, "own-drop-1")
    await _run(engine, tmp_path / "second", ONE_OWNER, "own-drop-2")
    assert await _owners(engine) == [("alpha-team", "primary"), ("beta-team", "secondary")]


async def test_the_same_team_in_another_role_is_added_beside_the_existing_row(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Identity is (project, team, role), so this is an add — and the
    existing association is left exactly as it was rather than reassigned."""
    await _run(
        engine,
        tmp_path / "first",
        document("    - team: alpha-team\n      role: secondary\n"),
        "own-role-1",
    )
    assert await _owners(engine) == [("alpha-team", "secondary")]

    await _run(engine, tmp_path / "second", ONE_OWNER, "own-role-2")
    assert await _owners(engine) == [("alpha-team", "primary"), ("alpha-team", "secondary")]


# --- the association is satisfied between approval and apply ---------------


async def test_an_owner_added_after_approval_applies_as_a_no_op(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The window nothing holds still.

    The plan is built during analyze and executed after approval. In
    between, another legitimate operation can record the same
    (project, team, role). `ON CONFLICT DO NOTHING` makes the apply a safe
    no-op — the approved intent is satisfied either way, so it must not
    fail and must not duplicate — but the receipt must not claim a create
    that never happened.
    """
    await _run(engine, tmp_path / "first", ONE_OWNER, "own-race-first")

    # Plan and approve an import that ADDS beta-team...
    second = tmp_path / "second"
    second.mkdir()
    _h, settings, client, session_id, analysis, actor = await _import(engine, second, TWO_OWNERS)
    async with engine.connect() as connection:
        planned = (
            await connection.execute(
                text(
                    "SELECT action FROM onboarding_plan_items i "
                    "JOIN onboarding_plans p ON p.id = i.plan_id "
                    "WHERE p.session_id = :s AND i.item_key = 'owner_team:beta-team:secondary'"
                ),
                {"s": session_id},
            )
        ).scalar_one()
    assert planned == "create", "precondition: the plan must intend a real add"

    # ...then somebody else records exactly that association first.
    async with engine.begin() as connection:
        project_id = (
            await connection.execute(
                text("SELECT id FROM projects WHERE project_key = :k"), {"k": PROJECT_KEY}
            )
        ).scalar_one()
        inserted = await CatalogService(connection).add_project_owner(
            project_id, "beta-team", "secondary"
        )
    assert inserted is True

    result = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="own-race-apply",
        actor_identity_id=actor,
    )

    # Succeeds, because the approved intent is satisfied.
    assert result.outcome == "applied"
    # No duplicate, and the existing association is untouched.
    assert await _owners(engine) == [("alpha-team", "primary"), ("beta-team", "secondary")]

    # And the receipt reports what committed, not what was intended. Read
    # from the stored row: a retry replays this rather than recounting.
    async with engine.connect() as connection:
        created, unchanged = (
            await connection.execute(
                text(
                    "SELECT created_entities, unchanged_entities FROM onboarding_applies "
                    "WHERE idempotency_key = :k"
                ),
                {"k": "own-race-apply"},
            )
        ).one()
    assert created == 0, "the receipt claims a create this apply did not perform"
    assert unchanged >= 1, "the no-op was not counted as unchanged"


async def test_the_receipt_and_the_response_agree(engine: AsyncEngine, tmp_path: Path) -> None:
    """Whatever the counters say, the stored row and the response say it
    together — otherwise an audit and a UI disagree about the same apply."""
    await _run(engine, tmp_path / "first", ONE_OWNER, "own-receipt-1")

    second = tmp_path / "second"
    second.mkdir()
    _h, settings, client, session_id, analysis, actor = await _import(engine, second, TWO_OWNERS)
    result = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="own-receipt-2",
        actor_identity_id=actor,
    )
    async with engine.connect() as connection:
        created, unchanged = (
            await connection.execute(
                text(
                    "SELECT created_entities, unchanged_entities FROM onboarding_applies "
                    "WHERE idempotency_key = :k"
                ),
                {"k": "own-receipt-2"},
            )
        ).one()
    assert created == result.created
    assert unchanged == result.unchanged
    # The owner genuinely was created this time, so it is counted.
    assert created >= 1


# --- isolation -------------------------------------------------------------


async def test_another_project_owning_the_same_team_does_not_satisfy_this_one(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The precise defect in the old snapshot.

    `owner_teams` was a GLOBAL `SELECT DISTINCT team_key`, so any other
    project using the name made this project's missing owner look settled.
    """
    async with engine.begin() as connection:
        catalog = CatalogService(connection)
        other = await catalog.create_project(
            "neighbour",
            "Neighbour",
            repo_provider="github",
            repo_owner="Duosis-Developer-Team",
            repo_name="Neighbour",
        )
        await catalog.add_project_owner(other.id, "alpha-team", "primary")

    items = await _run(engine, tmp_path / "first", ONE_OWNER, "own-isolation")
    # A new project, so the association comes with it — the point is that
    # the row landed under THIS project.
    assert items[0]["action"] == "no_change"
    assert await _owners(engine) == [("alpha-team", "primary")]
    assert await _owners(engine, "neighbour") == [("alpha-team", "primary")]


async def test_an_import_never_touches_another_project_ownership(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    async with engine.begin() as connection:
        catalog = CatalogService(connection)
        other = await catalog.create_project(
            "neighbour",
            "Neighbour",
            repo_provider="github",
            repo_owner="Duosis-Developer-Team",
            repo_name="Neighbour",
        )
        await catalog.add_project_owner(other.id, "gamma-team", "primary")

    await _run(engine, tmp_path / "first", ONE_OWNER, "own-cross-1")
    await _run(engine, tmp_path / "second", TWO_OWNERS, "own-cross-2")

    assert await _owners(engine, "neighbour") == [("gamma-team", "primary")]


# --- what an ownership row is NOT ------------------------------------------


async def test_recording_an_owner_grants_no_permission(engine: AsyncEngine, tmp_path: Path) -> None:
    """An ownership row is catalog metadata. If it minted a principal, a
    role or a grant, this is where that would show up.

    Measured around `add_project_owner` alone rather than around a whole
    import. A full import legitimately seeds an admin identity and a
    platform-owner grant through the test harness, and counting across it
    would have blamed ownership for the harness's work — which is exactly
    the kind of assertion that passes for the wrong reason.
    """

    async def counts() -> dict[str, int]:
        async with engine.connect() as connection:
            return {
                table: (
                    await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                ).scalar_one()
                for table in ("grants", "roles", "identities", "scopes")
            }

    async with engine.begin() as connection:
        project = await CatalogService(connection).create_project(
            "rbac-probe",
            "RBAC probe",
            repo_provider="github",
            repo_owner="Duosis-Developer-Team",
            repo_name="RbacProbe",
        )

    before = await counts()
    async with engine.begin() as connection:
        added = await CatalogService(connection).add_project_owner(
            project.id, "alpha-team", "primary"
        )
    assert added is True

    after = await counts()
    assert after == before, "recording an owner created a principal, role, grant or scope"
    assert await _owners(engine, "rbac-probe") == [("alpha-team", "primary")]


async def test_the_plan_records_that_ownership_grants_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Stated on the item itself, so an approver reads it rather than
    having to know it."""
    _h, _s, _c, session_id, _a, _actor = await _import(engine, tmp_path, ONE_OWNER)
    async with engine.connect() as connection:
        detail = (
            await connection.execute(
                text(
                    "SELECT detail FROM onboarding_plan_items i "
                    "JOIN onboarding_plans p ON p.id = i.plan_id "
                    "WHERE i.entity_kind = 'owner_team' AND p.session_id = :s"
                ),
                {"s": session_id},
            )
        ).scalar_one()
    assert dict(detail)["grants_no_permissions"] is True
    assert dict(detail)["role"] == "primary"


# --- plan/apply agreement --------------------------------------------------


async def test_every_owner_the_manifest_declares_ends_up_recorded(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The invariant the split halves violated: whatever route the plan
    takes, the manifest's owners exist afterwards."""
    await _run(engine, tmp_path / "first", ONE_OWNER, "own-inv-1")
    await _run(engine, tmp_path / "second", TWO_OWNERS, "own-inv-2")

    recorded = set(await _owners(engine))
    assert {("alpha-team", "primary"), ("beta-team", "secondary")} <= recorded


async def test_a_missing_owner_is_never_reported_as_applied_with_parent(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The exact wording that hid the bug.

    `applied_with_parent` is only honest while a parent is being created.
    On an existing project it must never appear for an owner that is not
    already recorded.
    """
    await _run(engine, tmp_path / "first", ONE_OWNER, "own-word-1")
    items = await _run(engine, tmp_path / "second", TWO_OWNERS, "own-word-2")
    for item in items:
        assert item["reason"] != "applied_with_parent", item


def test_the_planner_can_no_longer_emit_a_dead_owner_reason_code() -> None:
    """`owner_team_unknown` is gone from the vocabulary.

    It was defined and emitted by nothing, which read as a guarantee that an
    unrecognised team would be refused — and two manifests documented that
    guarantee in prose.
    """
    from drake_api.onboarding.model import REASON_TEXT

    assert "owner_team_unknown" not in REASON_TEXT

    snapshot = CatalogSnapshot()
    plan = build_plan(
        {
            "apiVersion": "drake.duosis.com/v1alpha1",
            "kind": "ProjectObservability",
            "metadata": {"name": "x", "displayName": "X"},
            "spec": {
                "repository": {
                    "provider": "github",
                    "owner": "o",
                    "name": "r",
                    "defaultBranch": "main",
                },
                "owners": [{"team": "never-seen-before", "role": "primary"}],
                "environments": [
                    {"name": "prod", "runtime": "external", "branch": "main", "criticality": "low"}
                ],
                "services": [{"name": "web", "component": "web", "runtime": "nextjs"}],
                "tenantModel": {"mode": "none"},
            },
        },
        snapshot,
        repository_row_id="11111111-1111-4111-8111-111111111111",
    )
    reasons = {item.reason_code for item in plan.items if item.entity_kind == "owner_team"}
    assert reasons == {"applied_with_parent"}
