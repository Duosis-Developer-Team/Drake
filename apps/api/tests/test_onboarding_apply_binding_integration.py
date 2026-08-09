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
    BINDABLE_WORKLOAD_KINDS,
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


async def _session_version(engine: AsyncEngine, session_id: uuidlib.UUID) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT version FROM onboarding_sessions WHERE id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )


async def _apply(
    harness: Any,
    engine: AsyncEngine,
    session_id: uuidlib.UUID,
    version: int,
    actor: uuidlib.UUID,
    key: str,
) -> Any:
    return await service.apply(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
        plan_version=version,
        idempotency_key=key,
        actor_identity_id=actor,
    )


_TAMPER_TARGET = (
    "WHERE plan_id = (SELECT id FROM onboarding_plans WHERE session_id = :s "
    "ORDER BY plan_version DESC LIMIT 1) AND item_key = 'project:datalake'"
)


async def _tamper(engine: AsyncEngine, session_id: uuidlib.UUID, sql: str) -> None:
    """Rewrite an approved plan item behind the approval's back."""
    # `sql` is one of this module's own literals — the point of the test is
    # that the tamper is real, not that it is user-supplied.
    statement = f"{sql} {_TAMPER_TARGET}"
    async with engine.begin() as connection:
        changed = (await connection.execute(text(statement), {"s": session_id})).rowcount
    # A tamper that changed nothing would make the test pass for the wrong
    # reason — the earlier version of this test did exactly that.
    assert changed == 1, "the tamper matched no row"


@pytest.mark.anyio
async def test_a_tampered_payload_value_refuses_the_apply(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The TOCTOU case, with a tamper that actually changes something.

    An earlier version of this test issued `SET proposed_name =
    proposed_name` — a no-op that asserted nothing. Here the stored payload
    really is rewritten between approval and apply, which is the whole
    attack: the approval record still points at these items, so if apply
    trusts them without checking, it executes a value nobody approved.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    await _tamper(
        engine,
        session_id,
        "UPDATE onboarding_plan_items SET detail = "
        "jsonb_set(detail, '{payload,display_name}', '\"Datalake Pwned\"')",
    )

    with pytest.raises(service.OnboardingError) as raised:
        await _apply(harness, engine, session_id, plan_version, actor, "tamper-0001")
    assert raised.value.code == "plan_integrity_mismatch"
    assert raised.value.status == 409

    # Nothing was written — not the project, and not a receipt that a retry
    # could replay as though the apply had succeeded.
    async with engine.connect() as connection:
        projects = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM projects WHERE project_key = 'datalake'")
                )
            ).scalar_one()
        )
        receipts = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM onboarding_applies WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    assert projects == 0
    assert receipts == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("label", "sql"),
    [
        (
            "changes",
            "UPDATE onboarding_plan_items SET detail = "
            "jsonb_set(detail, '{changes}', '{\"display_name\": "
            '{"before": "a", "after": "b"}}\')',
        ),
        ("reason_code", "UPDATE onboarding_plan_items SET reason_code = 'rewritten'"),
        ("action", "UPDATE onboarding_plan_items SET action = 'no_change'"),
        ("identity", "UPDATE onboarding_plan_items SET proposed_name = 'Somebody Else'"),
    ],
)
async def test_every_digested_field_is_covered_by_the_integrity_check(
    engine: AsyncEngine, tmp_path: Path, label: str, sql: str
) -> None:
    """Whatever the digest covers, the check covers.

    A gap in any one of these is a field an attacker can move freely: a
    rewritten `reason_code` changes why a reviewer said yes, a rewritten
    `changes` block changes what they were shown, and an action flipped to
    `no_change` silently removes work from an approved plan.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    await _tamper(engine, session_id, sql)

    with pytest.raises(service.OnboardingError) as raised:
        await _apply(harness, engine, session_id, plan_version, actor, f"tamper-{label}")
    assert raised.value.code == "plan_integrity_mismatch", label


@pytest.mark.anyio
async def test_a_canonically_equivalent_rewrite_is_not_a_mismatch(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The check is on meaning, not on bytes.

    JSONB does not preserve key order, and a dump/restore or a routine
    re-serialization may hand the payload back in a different one. If that
    read as tampering, the integrity check would fail closed on healthy
    plans — an alarm that fires without an intruder gets switched off.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    # Rebuild every payload object with its keys in reverse order. Same
    # mapping, different serialization.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE onboarding_plan_items AS i SET detail = jsonb_set(
                    i.detail, '{payload}',
                    COALESCE(
                        (SELECT jsonb_object_agg(k, v)
                         FROM (SELECT key AS k, value AS v
                               FROM jsonb_each(i.detail->'payload')
                               ORDER BY key DESC) AS reordered),
                        i.detail->'payload'
                    )
                )
                WHERE i.plan_id = (SELECT id FROM onboarding_plans
                                   WHERE session_id = :s ORDER BY plan_version DESC LIMIT 1)
                  AND i.detail ? 'payload'
                """
            ),
            {"s": session_id},
        )

    outcome = await _apply(harness, engine, session_id, plan_version, actor, "reorder-0001")
    assert outcome.outcome == "applied"

    async with engine.connect() as connection:
        display_name = (
            await connection.execute(
                text("SELECT display_name FROM projects WHERE id = :p"),
                {"p": outcome.project_id},
            )
        ).scalar_one()
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
        # The repository projection. Its `manifest_digest` used to be
        # recomputed from the live document, which is exactly the value an
        # empty document would have made wrong — so it is asserted here
        # rather than left to the apply's return code.
        projection = (
            await connection.execute(
                text(
                    "SELECT commit_sha, manifest_digest, repository_id FROM "
                    "github_repository_projects WHERE project_id = :p"
                ),
                {"p": outcome.project_id},
            )
        ).one()
        plan_bound = (
            await connection.execute(
                text(
                    "SELECT a.commit_sha, a.manifest_digest FROM onboarding_analyses AS a "
                    "WHERE a.session_id = :s"
                ),
                {"s": session_id},
            )
        ).one()
        slo = (
            await connection.execute(
                text(
                    "SELECT slo_key, indicator, objective_ratio, window_seconds "
                    "FROM slo_definitions WHERE project_id = :p"
                ),
                {"p": outcome.project_id},
            )
        ).all()
        bindings = (
            await connection.execute(
                text(
                    "SELECT namespace, workload_kind, workload_name FROM "
                    "service_workload_bindings WHERE project_id = :p ORDER BY workload_name"
                ),
                {"p": outcome.project_id},
            )
        ).all()
        environments = sorted(
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT environment_key FROM environments WHERE project_id = :p"),
                    {"p": outcome.project_id},
                )
            ).all()
        )

    assert rows[0] == "datalake"
    assert rows[1] == "Datalake Platform"
    assert services == ["datalake-api", "datalake-gui"]
    assert environments == ["dev"]

    # The projection carries the plan-bound commit and digest — the values
    # the approval was computed over, not values re-derived at apply time.
    assert projection[0] == plan_bound[0]
    assert projection[1] == plan_bound[1]
    assert uuidlib.UUID(str(projection[2])) == row_id

    # The SLO, from the approved payload rather than from `spec.slos`.
    assert len(slo) == 1
    assert slo[0][0] == "datalake-api-availability"
    assert slo[0][1] == "availability"
    assert float(slo[0][2]) == pytest.approx(0.995)
    assert int(slo[0][3]) == 30 * 24 * 3600

    # Bindings come from observed inventory, and the values land intact.
    for namespace, kind, _name in bindings:
        assert namespace == "datalake-dev"
        assert kind in BINDABLE_WORKLOAD_KINDS


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
async def test_the_apply_receipt_commits_with_the_catalog_change(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`onboarding_applies` is a transactional apply receipt.

    It is NOT an outbox row: nothing reads it, no worker consumes it, and
    ADR-0024's notification flow does not describe it. It commits inside the
    apply transaction alongside the catalog change and the audit, and it is
    what a retry of the same request replays.

    There is deliberately no event table beside it, because no consumer for
    "an onboarding finished" exists — an event nothing acts on is a durable
    surface to keep safe for no reason.
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
        idempotency_key="receipt-0001",
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
        idempotency_key="receipt-0001",
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


async def _second_plan(harness: Any, engine: AsyncEngine, session_id: uuidlib.UUID) -> int:
    """Re-analyse a session, producing a genuinely different plan version."""
    analysis = await service.analyze(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
    )
    return int(analysis["plan_version"])


@pytest.mark.anyio
async def test_the_same_key_under_a_different_plan_is_a_conflict(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """An idempotency key means "this is the same request".

    Uniqueness used to be on `(plan_id, idempotency_key)`, which made the
    same key under a NEWER plan a different request — so a client retrying
    what it believed was one call could apply a second approval it never
    meant to send. The scope is the session; a reuse inside it is a
    conflict, not a replay and not a fresh apply.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)
    first = await _apply(harness, engine, session_id, plan_version, actor, "shared-key")
    assert first.outcome == "applied"

    # A real second plan, from a real second analysis of the same session.
    second_version = await _second_plan(harness, engine, session_id)
    assert second_version != plan_version

    with pytest.raises(service.OnboardingError) as raised:
        await _apply(harness, engine, session_id, second_version, actor, "shared-key")
    assert raised.value.code == "idempotency_key_reused"
    assert raised.value.status == 409

    async with engine.connect() as connection:
        receipts = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM onboarding_applies WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
        projects = int(
            (await connection.execute(text("SELECT count(*) FROM projects"))).scalar_one()
        )
    assert receipts == 1
    assert projects == 1


@pytest.mark.anyio
async def test_two_sessions_may_use_the_same_key(engine: AsyncEngine, tmp_path: Path) -> None:
    """The key is the client's namespace, not a global one.

    Scoping uniqueness to the session is only correct if it does not make
    one client's choice of key collide with another's. Two independent
    onboardings pick the obvious key and neither is refused.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)

    first_session, first_version = await _approved_session(harness, engine, row_id, actor)
    assert (
        await _apply(harness, engine, first_session, first_version, actor, "reused-key-0001")
    ).outcome == ("applied")

    second_session, second_version = await _approved_session(harness, engine, row_id, actor)
    second = await _apply(harness, engine, second_session, second_version, actor, "reused-key-0001")
    assert second.outcome == "applied"

    async with engine.connect() as connection:
        keys = [
            (str(row[0]), str(row[1]))
            for row in (
                await connection.execute(
                    text("SELECT session_id, idempotency_key FROM onboarding_applies")
                )
            ).all()
        ]
    assert sorted(key for _, key in keys) == ["reused-key-0001"] * 2
    assert len({session for session, _ in keys}) == 2


@pytest.mark.anyio
async def test_a_race_on_one_key_and_two_plans_is_settled_by_the_database(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The conflict is decided in PostgreSQL, not by a pre-check.

    Two callers both read "no receipt for this key" before either writes
    one, so an application-level check passes for BOTH and two different
    plans apply under a single key. Only the unique constraint on
    `(session_id, idempotency_key)` can settle that.

    This drives the claim statement `apply` uses, on two independent
    connections released together, rather than driving `apply` itself:
    a session has one approved plan at a time, so the approval gate stops
    two different plans from reaching the claim through the public path.
    That gate is not what is under test here — what happens when two writers
    reach the claim at once is.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, first_version = await _approved_session(harness, engine, row_id, actor)
    await _second_plan(harness, engine, session_id)

    async with engine.connect() as connection:
        plans = [
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT id FROM onboarding_plans WHERE session_id = :s "
                        "ORDER BY plan_version"
                    ),
                    {"s": session_id},
                )
            ).all()
        ]
    assert len(plans) == 2, "two genuinely different plans"

    barrier = asyncio.Barrier(2)
    claim_sql = text(
        """
        INSERT INTO onboarding_applies
            (plan_id, session_id, actor_identity_id, outcome, idempotency_key)
        VALUES (:plan, :session, :actor, 'failed', :key)
        ON CONFLICT (session_id, idempotency_key) DO NOTHING
        RETURNING id
        """
    )

    async def claim(plan_id: Any) -> bool:
        async with engine.begin() as connection:
            await barrier.wait()
            row = (
                await connection.execute(
                    claim_sql,
                    {
                        "plan": plan_id,
                        "session": session_id,
                        "actor": actor,
                        "key": "raced-key-0001",
                    },
                )
            ).first()
            return row is not None

    won = await asyncio.gather(claim(plans[0]), claim(plans[1]))
    assert sorted(won) == [False, True], "exactly one writer may claim the key"

    async with engine.connect() as connection:
        receipts = (
            await connection.execute(
                text("SELECT plan_id FROM onboarding_applies WHERE session_id = :s"),
                {"s": session_id},
            )
        ).all()
    assert len(receipts) == 1

    # And the loser's read of that receipt is what raises the conflict: the
    # plan it holds is not the plan it tried to apply.
    async with engine.connect() as connection:
        recorded = await service._recorded_receipt(connection, session_id, "raced-key-0001")
    assert recorded is not None
    loser_plan = plans[0] if str(recorded["plan_id"]) == str(plans[1]) else plans[1]
    assert str(recorded["plan_id"]) != str(loser_plan)
    assert first_version  # the approved version is untouched by any of this


# ===========================================================================
# the receipt is what a retry replays
# ===========================================================================


async def _apply_http(
    harness: Any, engine: AsyncEngine, session_id: uuidlib.UUID, version: int, key: str
) -> Any:
    """Apply over the real endpoint, so the assertion is on the response."""
    from harness_s1 import grant_platform_owner

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        me = await client.get("/v1/me")
        return await client.post(
            f"/v1/onboarding/sessions/{session_id}/apply",
            json={"plan_version": version, "idempotency_key": key},
            headers={"X-CSRF-Token": me.json()["csrf_token"], "Origin": harness.client_base_url},
        )


@pytest.mark.anyio
async def test_a_retry_returns_the_first_answer_on_every_field(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The one promise an idempotency key makes.

    The receipt stored three counters while the response carried seven, so
    a retried apply reported `metadata_updated: 0` and `bindings_created: 0`
    for work that had really happened. A client reconciling on those numbers
    would conclude the apply did less than it did. Every public field is
    compared, not a chosen few, because the fields nobody thought to check
    are exactly the ones that drifted.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    first = await _apply_http(harness, engine, session_id, plan_version, "retry-0001")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["outcome"] == "applied"
    # A fixture that changed nothing would make the comparison vacuous.
    assert body["created_entities"] > 0
    assert body["slo_definitions_created"] > 0

    retried = await _apply_http(harness, engine, session_id, plan_version, "retry-0001")
    assert retried.status_code == 200, retried.text
    replay = retried.json()

    # Everything except the outcome word, which correctly changes from
    # "applied" to "unchanged" — the work happened once.
    assert set(replay) == set(body)
    for field in set(body) - {"outcome"}:
        assert replay[field] == body[field], field
    assert replay["outcome"] == "unchanged"

    async with engine.connect() as connection:
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
    assert audits == 1


@pytest.mark.anyio
async def test_the_concurrent_loser_receives_the_whole_result(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A lost race and a retry are the same thing to the caller.

    The loser reads the receipt rather than doing the work, so if the
    receipt were partial the loser would receive a partial answer — and it
    has no way to tell that from a genuinely small apply.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    barrier = asyncio.Barrier(2)

    async def worker() -> Any:
        await barrier.wait()
        return await _apply(harness, engine, session_id, plan_version, actor, "loser-0001")

    first, second = await asyncio.gather(worker(), worker())
    for field in (
        "created",
        "linked",
        "unchanged",
        "metadata_updated",
        "slo_definitions_created",
        "slo_definitions_updated",
        "bindings_created",
        "project_id",
    ):
        assert getattr(first, field) == getattr(second, field), field
    assert first.slo_definitions_created > 0


@pytest.mark.anyio
async def test_a_pre_0020_receipt_reports_unrecorded_rather_than_zero(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Receipts written before migration 0020 never held these counters.

    Returning the stored zeros would present "never recorded" as "measured
    zero", which is the same failure the retry bug had, only quieter. They
    are not reconstructed from audit metadata either: audit records what
    happened, not how many rows a counter reached.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)
    assert (
        await _apply_http(harness, engine, session_id, plan_version, "legacy-0001")
    ).status_code == 200

    # Exactly the shape 0020's server default leaves an old row in.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE onboarding_applies SET counters_complete = false, "
                "metadata_updated = 0, slo_definitions_created = 0, "
                "slo_definitions_updated = 0, bindings_created = 0 WHERE session_id = :s"
            ),
            {"s": session_id},
        )

    replay = (await _apply_http(harness, engine, session_id, plan_version, "legacy-0001")).json()
    assert replay["outcome"] == "unchanged"
    # The three counters the old schema DID record still come back.
    assert replay["created_entities"] > 0
    # The four it did not are absent, not zero.
    for field in (
        "metadata_updated",
        "slo_definitions_created",
        "slo_definitions_updated",
        "bindings_created",
    ):
        assert replay[field] is None, field


@pytest.mark.anyio
async def test_a_failed_receipt_write_rolls_back_the_whole_apply(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The receipt commits with the work or neither commits.

    If the receipt write could fail on its own, an apply would change the
    catalog and leave no record that it did — so a retry would apply it a
    second time, which is the exact outcome idempotency exists to prevent.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    actor = await _identity(engine)
    session_id, plan_version = await _approved_session(harness, engine, row_id, actor)

    original = service._promote_receipt
    calls: list[int] = []

    async def explode(*args: Any, **kwargs: Any) -> None:
        calls.append(1)
        raise RuntimeError("receipt write failed")

    service._promote_receipt = explode  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            await _apply(harness, engine, session_id, plan_version, actor, "receipt-fail-01")
    finally:
        service._promote_receipt = original  # type: ignore[assignment]
    assert calls == [1], "the injected failure has to be reached"

    async with engine.connect() as connection:
        projects = int(
            (await connection.execute(text("SELECT count(*) FROM projects"))).scalar_one()
        )
        receipts = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM onboarding_applies WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    assert projects == 0
    assert receipts == 0

    # And the same request still works once the fault is gone.
    retried = await _apply(harness, engine, session_id, plan_version, actor, "receipt-fail-01")
    assert retried.outcome == "applied"


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
