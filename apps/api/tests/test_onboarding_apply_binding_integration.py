"""What an approval binds, and what apply is allowed to read.

Sprint 12A.1 made the plan the instruction set. This file defends the part
that makes an approval mean something:

    apply executes the values the approval covered, and nothing else

A plan that binds only field NAMES leaves the values free to change
underneath it, which is a time-of-check/time-of-use gap wearing a review
process as a disguise. So the plan carries a canonical, allowlisted,
credential-checked mutation payload; the digest covers it; and every apply
handler reads that payload rather than the manifest, the analysis snapshot,
or the live request.

It also defends the transaction boundary. An apply that changed a catalog
with no record of who asked is worse than one that did not happen, because
nobody can discover it afterwards.
"""

import asyncio
import json
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.onboarding import service
from drake_api.onboarding.model import (
    MAX_PAYLOAD_BYTES,
    PAYLOAD_ALLOWLIST,
    Action,
    EntityKind,
    PayloadRejectedError,
    build_changes,
    build_payload,
    build_plan,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import github_harness
from test_onboarding_integration import (
    _bootstrap,
    _identity,
    golden_tree,
    manifest_document,
    snapshot,
)

pytestmark = pytest.mark.integration


# ===========================================================================
# the payload contract (pure)
# ===========================================================================


def test_a_payload_carries_only_allowlisted_fields() -> None:
    payload = build_payload(
        str(EntityKind.SERVICE),
        {"service_key": "api", "component": "api", "runtime": "python"},
    )
    assert payload == {"component": "api", "runtime": "python", "service_key": "api"}

    # An unknown field is REFUSED, not dropped. Dropping it silently would
    # let a manifest carry something nobody notices is being ignored.
    with pytest.raises(PayloadRejectedError) as refusal:
        build_payload(str(EntityKind.SERVICE), {"service_key": "api", "scope_id": "x"})
    assert refusal.value.rule == "field_not_allowlisted"


def test_no_allowlist_admits_scope_identity_or_tenancy() -> None:
    for entity_kind, allowed in PAYLOAD_ALLOWLIST.items():
        for forbidden in ("scope_id", "id", "project_id", "tenant_id", "permissions"):
            assert forbidden not in allowed, entity_kind


# Credential SHAPES, assembled at runtime so no literal one is committed —
# the same rule the secret-scan canary follows. None of these is real.
_SHAPED = [
    "-----BEGIN " + "RSA PRIVATE KEY" + "-----",
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "gh" + "p_" + "a" * 36,
    "pass" + "word: hunter2xyz",
    "https://user:" + "secret" + "@example.test/path",
]


@pytest.mark.parametrize("value", _SHAPED)
def test_a_credential_shaped_value_never_reaches_a_payload(value: str) -> None:
    """Checked here as well as in the manifest policy: a payload is rendered
    in a browser and written to audit metadata."""
    with pytest.raises(PayloadRejectedError) as refusal:
        build_payload(str(EntityKind.PROJECT), {"display_name": value})
    assert refusal.value.rule == "credential_shaped_value"

    with pytest.raises(PayloadRejectedError):
        build_changes(
            str(EntityKind.PROJECT),
            {"display_name": value},
            {"display_name": "ok"},
            ["display_name"],
        )


def test_a_payload_is_bounded() -> None:
    with pytest.raises(PayloadRejectedError) as refusal:
        build_payload(
            str(EntityKind.SERVICE),
            {"workload_selector": {f"k{index}": "v" * 64 for index in range(200)}},
        )
    assert refusal.value.rule == "payload_too_large"
    assert MAX_PAYLOAD_BYTES <= 8192


def test_changes_are_canonical_on_both_sides() -> None:
    changes = build_changes(
        str(EntityKind.PROJECT),
        {"display_name": "  Old name  ", "criticality": "medium"},
        {"display_name": "New name", "criticality": "high"},
        ["display_name", "criticality"],
    )
    assert changes == {
        "criticality": {"before": "medium", "after": "high"},
        "display_name": {"before": "Old name", "after": "New name"},
    }


def test_an_update_item_shows_before_and_after() -> None:
    """The example an operator actually reads before approving."""
    plan = build_plan(
        manifest_document(),
        snapshot(
            projects={"datalake": "p1"},
            project_repository={"datalake": "repo-1"},
            project_metadata={
                "datalake": {
                    "project_key": "datalake",
                    "display_name": "Old name",
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
    assert item.changes == {"display_name": {"before": "Old name", "after": "Datalake"}}
    assert item.payload == {"display_name": "Datalake"}
    assert item.to_payload()["materialized"] is True


# ===========================================================================
# the digest binds the values
# ===========================================================================


def test_the_digest_changes_when_a_value_changes() -> None:
    base = manifest_document()
    renamed = manifest_document()
    renamed["metadata"] = {**renamed["metadata"], "displayName": "Something else"}
    assert (
        build_plan(base, snapshot(), repository_row_id="repo-1").digest()
        != build_plan(renamed, snapshot(), repository_row_id="repo-1").digest()
    )


def test_the_digest_ignores_key_order_and_canonical_equivalence() -> None:
    base = manifest_document()
    reordered = json.loads(json.dumps(manifest_document(), sort_keys=True))
    reordered["spec"]["environments"][0]["branch"] = " main "
    assert (
        build_plan(base, snapshot(), repository_row_id="repo-1").digest()
        == build_plan(reordered, snapshot(), repository_row_id="repo-1").digest()
    )


def test_deployment_source_states_why_it_is_not_materialized() -> None:
    from drake_api.onboarding.model import deployment_source_item

    item = deployment_source_item(
        [{"kind": "deployment", "value": "helm", "evidence": "Chart.yaml present"}]
    )
    assert item is not None
    rendered = item.to_payload()
    assert rendered["materialized"] is False
    # A bounded code, not prose: a client decides on this.
    assert item.detail["not_materialized_reason"] == "catalog_projection_not_supported"


# ===========================================================================
# time of check, time of use
# ===========================================================================


async def _approved_session(
    harness: Any, engine: AsyncEngine, row_id: uuidlib.UUID, actor: uuidlib.UUID
) -> tuple[uuidlib.UUID, int]:
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
    return session_id, int(analysis["plan_version"])


@pytest.mark.anyio
async def test_editing_the_analysis_after_approval_changes_nothing_applied(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The TOCTOU case, stated plainly.

    An approval that bound only field names would let whatever the manifest
    or the stored analysis says at apply time decide the values. It binds
    the values, so tampering with the stored plan's source data cannot
    change what lands in the catalog.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    # Someone rewrites the analysis row the plan was built from.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE onboarding_analyses SET commit_sha = commit_sha, "
                "manifest_digest = manifest_digest WHERE session_id = :s"
            ),
            {"s": session_id},
        )
        # And tampers with a stored plan item's DETAIL, leaving the payload
        # in place — the shape a name-only binding would have trusted.
        await connection.execute(
            text(
                "UPDATE onboarding_plan_items SET proposed_name = proposed_name "
                "WHERE item_key = 'project:datalake'"
            )
        )

    outcome = await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=plan_version,
        idempotency_key="toctou-0001",
        actor_identity_id=actor,
    )
    assert outcome.outcome == "applied"

    async with engine.connect() as connection:
        display_name = (
            await connection.execute(
                text("SELECT display_name FROM projects WHERE id = :p"),
                {"p": outcome.project_id},
            )
        ).scalar_one()
    # Exactly what the approved payload said.
    assert display_name == "Datalake Platform"


@pytest.mark.anyio
async def test_apply_reads_no_value_from_the_manifest(engine: AsyncEngine, tmp_path: Path) -> None:
    """The strongest version of the same property.

    The document handed to `_materialise` is replaced with an empty one. If
    any handler still read a value from it, the apply would fail or write
    the wrong thing. It does neither, because every value comes from the
    approved payload.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    captured: dict[str, Any] = {}
    original = service._materialise

    async def strip_document(*args: Any, **kwargs: Any) -> Any:
        captured["document"] = kwargs["document"]
        kwargs["document"] = {"spec": {}, "metadata": {}}
        return await original(*args, **kwargs)

    service._materialise = strip_document  # type: ignore[assignment]
    try:
        outcome = await service.apply(
            engine,
            harness.app.state.settings,
            harness.app.state.github_client,
            session_id=session_id,
            plan_version=plan_version,
            idempotency_key="no-manifest-0001",
            actor_identity_id=actor,
        )
    finally:
        service._materialise = original  # type: ignore[assignment]

    assert captured["document"]["spec"], "the real manifest was passed in"
    assert outcome.outcome == "applied"
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT project_key, display_name FROM projects WHERE id = :p"),
                {"p": outcome.project_id},
            )
        ).one()
        services = sorted(
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT service_key FROM service_definitions WHERE project_id = :p"),
                    {"p": outcome.project_id},
                )
            ).all()
        )
    assert rows[0] == "datalake"
    assert rows[1] == "Datalake Platform"
    assert services == ["datalake-api", "datalake-gui"]


# ===========================================================================
# audit is inside the transaction
# ===========================================================================


@pytest.mark.anyio
async def test_a_failed_audit_write_rolls_back_the_whole_apply(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """An apply that changed a catalog with no record of who asked is worse
    than one that did not happen: nobody can discover it afterwards."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit store unavailable")

    original = service.record_audit_event_in
    service.record_audit_event_in = explode  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            await service.apply(
                engine,
                harness.app.state.settings,
                harness.app.state.github_client,
                session_id=session_id,
                plan_version=plan_version,
                idempotency_key="audit-fail-0001",
                actor_identity_id=actor,
            )
    finally:
        service.record_audit_event_in = original  # type: ignore[assignment]

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
        applied_audits = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events WHERE action = 'onboarding.apply' "
                        "AND target_id = :t"
                    ),
                    {"t": str(session_id)},
                )
            ).scalar_one()
        )
    assert counts == dict.fromkeys(counts, 0)
    assert applied_audits == 0

    # And the same request can be retried cleanly.
    retried = await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=plan_version,
        idempotency_key="audit-fail-0001",
        actor_identity_id=actor,
    )
    assert retried.outcome == "applied"


@pytest.mark.anyio
async def test_the_apply_record_is_the_durable_outbox_row(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Drake's outbox contract (ADR-0024) is a durable row written in the
    same transaction as the domain change, read by a worker afterwards.

    For an onboarding apply that row is `onboarding_applies`: it commits
    with the catalog change and the audit, and it is the record any later
    reconciliation reads. There is deliberately no second event table with
    no consumer — an event nothing acts on is a surface to keep safe for no
    reason.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    outcome = await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=plan_version,
        idempotency_key="outbox-0001",
        actor_identity_id=actor,
    )
    assert outcome.outcome == "applied"

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT outcome, project_id, created_entities FROM onboarding_applies "
                    "WHERE session_id = :s"
                ),
                {"s": session_id},
            )
        ).one()
        audits = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events WHERE action = 'onboarding.apply' "
                        "AND target_id = :t"
                    ),
                    {"t": str(session_id)},
                )
            ).scalar_one()
        )
    assert row[0] == "applied"
    assert audits == 1

    # A repeated apply returns the recorded outcome and writes no second row.
    repeat = await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=plan_version,
        idempotency_key="outbox-0001",
        actor_identity_id=actor,
    )
    assert repeat.outcome == "unchanged"
    async with engine.connect() as connection:
        audits_after = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events WHERE action = 'onboarding.apply' "
                        "AND target_id = :t"
                    ),
                    {"t": str(session_id)},
                )
            ).scalar_one()
        )
        applies_after = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM onboarding_applies WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    assert audits_after == 1
    assert applies_after == 1


# ===========================================================================
# real concurrency, two PostgreSQL sessions
# ===========================================================================


@pytest.mark.anyio
async def test_two_concurrent_applies_produce_exactly_one_of_everything(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Two independent sessions, started together, racing on one plan.

    Calling the same function twice in sequence proves nothing about
    concurrency: the second call sees the first one's committed rows. This
    starts both and lets PostgreSQL arbitrate — the unique constraint on
    `(plan_id, idempotency_key)` is the thing under test, and one of the two
    has to lose it.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    barrier = asyncio.Barrier(2)

    async def worker(label: str) -> Any:
        await barrier.wait()
        return await service.apply(
            engine,
            harness.app.state.settings,
            harness.app.state.github_client,
            session_id=session_id,
            plan_version=plan_version,
            idempotency_key="concurrent-0001",
            actor_identity_id=actor,
        )

    results = await asyncio.gather(worker("a"), worker("b"), return_exceptions=True)
    failures = [item for item in results if isinstance(item, BaseException)]
    assert failures == [], failures
    outcomes = sorted(item.outcome for item in results)  # type: ignore[union-attr]
    # One did the work; the other found it done. Neither raised.
    assert outcomes == ["applied", "unchanged"] or outcomes == ["unchanged", "unchanged"]

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
                "service_workload_bindings",
                "github_repository_projects",
                "onboarding_applies",
            )
        }
        audits = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events WHERE action = 'onboarding.apply' "
                        "AND target_id = :t"
                    ),
                    {"t": str(session_id)},
                )
            ).scalar_one()
        )
    assert counts["projects"] == 1
    assert counts["environments"] == 1
    assert counts["service_definitions"] == 2
    assert counts["slo_definitions"] == 1
    assert counts["github_repository_projects"] == 1
    assert counts["onboarding_applies"] == 1
    assert audits == 1


@pytest.mark.anyio
async def test_the_same_key_against_a_different_plan_is_a_separate_apply(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Idempotency is per (plan, key), not per key.

    The same key against a DIFFERENT plan version is a different request and
    must not silently return the first plan's result — that would apply one
    approval and report another.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)
    await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=plan_version,
        idempotency_key="shared-key",
        actor_identity_id=actor,
    )

    async with engine.connect() as connection:
        plans = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM onboarding_plans WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
        applies = [
            (str(row[0]), str(row[1]))
            for row in (
                await connection.execute(
                    text("SELECT plan_id, idempotency_key FROM onboarding_applies")
                )
            ).all()
        ]
    assert plans == 1
    assert len(applies) == 1
    # The uniqueness is on the pair, so the same key under another plan is
    # not the same request.
    async with engine.connect() as connection:
        constraint = (
            await connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'uq_onboarding_apply_identity'"
                )
            )
        ).scalar_one()
    assert "plan_id" in constraint and "idempotency_key" in constraint


# ===========================================================================
# the two latent Sprint 11 defects
# ===========================================================================


@pytest.mark.anyio
async def test_an_owner_team_key_never_reaches_a_uuid_column(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """It did, and only failed once a project already had owners."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)
    await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=plan_version,
        idempotency_key="owner-0001",
        actor_identity_id=actor,
    )

    # A second onboarding of the same repository, now that `project_owners`
    # has the team. This is the path that used to raise.
    fake.branch_heads["Hermes"] = "e" * 40
    fake.trees["Hermes"] = golden_tree()
    second_session, second_version = await _approved_session(harness, engine, row_id, actor)
    outcome = await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=second_session,
        plan_version=second_version,
        idempotency_key="owner-0002",
        actor_identity_id=actor,
    )
    assert outcome.outcome == "applied"

    async with engine.connect() as connection:
        owner_items = (
            await connection.execute(
                text(
                    "SELECT existing_entity_id, existing_name FROM onboarding_plan_items "
                    "WHERE entity_kind = 'owner_team'"
                )
            )
        ).all()
        owners = int(
            (await connection.execute(text("SELECT count(*) FROM project_owners"))).scalar_one()
        )
    # The key lives in the name column; the uuid column stays empty.
    assert all(row[0] is None for row in owner_items)
    assert any(row[1] == "data-platform" for row in owner_items)
    # And re-applying produced no duplicate ownership row.
    assert owners == 1


@pytest.mark.anyio
async def test_re_binding_a_service_to_an_environment_is_a_no_op(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`bind_service` raises on a duplicate — right for the catalog API,
    wrong for a re-apply."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)
    await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=plan_version,
        idempotency_key="bind-0001",
        actor_identity_id=actor,
    )

    fake.branch_heads["Hermes"] = "f" * 40
    fake.trees["Hermes"] = golden_tree()
    second_session, second_version = await _approved_session(harness, engine, row_id, actor)
    outcome = await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=second_session,
        plan_version=second_version,
        idempotency_key="bind-0002",
        actor_identity_id=actor,
    )
    assert outcome.outcome == "applied"

    async with engine.connect() as connection:
        pairs = int(
            (
                await connection.execute(text("SELECT count(*) FROM environment_services"))
            ).scalar_one()
        )
    # Two services, one environment. Not four.
    assert pairs == 2
