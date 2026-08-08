"""The incident lifecycle against the real database.

Every rule in this suite exists because getting it wrong produces a
specific, expensive failure: paging someone because Prometheus restarted,
closing an incident because a binding was disabled, or opening a second
incident for a service that already has one open.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from drake_api.incidents.model import Evaluation
from drake_api.incidents.processor import acknowledge_incident, process_evaluation
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

BASE = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)


async def make_world(engine: AsyncEngine) -> dict[str, uuid.UUID]:
    """A project → environment → service with one active, resolved binding."""
    async with engine.begin() as connection:
        org = (
            await connection.execute(
                text(
                    "SELECT id FROM scopes WHERE scope_type='organization' AND external_ref='root'"
                )
            )
        ).scalar_one()
        suffix = uuid.uuid4().hex[:8]
        project_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('project', :ref, :parent) RETURNING id"
                ),
                {"ref": f"p-{suffix}", "parent": org},
            )
        ).scalar_one()
        project = (
            await connection.execute(
                text(
                    "INSERT INTO projects (project_key, display_name, repo_provider, "
                    "repo_owner, repo_name, tenant_model, catalog_source_kind, scope_id) "
                    "VALUES (:k, 'Pilot', 'github', 'acme', :k, 'none', 'manifest', :scope) "
                    "RETURNING id"
                ),
                {"k": f"proj{suffix}", "scope": project_scope},
            )
        ).scalar_one()
        env_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('environment', :ref, :parent) RETURNING id"
                ),
                {"ref": f"e-{suffix}", "parent": project_scope},
            )
        ).scalar_one()
        environment = (
            await connection.execute(
                text(
                    "INSERT INTO environments (project_id, environment_key, runtime, "
                    "catalog_source_kind, cluster_id, namespace, scope_id) "
                    "VALUES (:p, 'dev', 'external', 'manifest', NULL, NULL, :scope) RETURNING id"
                ),
                {"p": project, "scope": env_scope},
            )
        ).scalar_one()
        service = (
            await connection.execute(
                text(
                    "INSERT INTO service_definitions (project_id, service_key, display_name, "
                    "component, runtime, metrics_profile, catalog_source_kind) "
                    "VALUES (:p, :k, 'API', 'api', 'kubernetes', 'http', 'manifest') RETURNING id"
                ),
                {"p": project, "k": f"api{suffix}"},
            )
        ).scalar_one()
        svc_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('service', :ref, :parent) RETURNING id"
                ),
                {"ref": f"s-{suffix}", "parent": env_scope},
            )
        ).scalar_one()
        environment_service = (
            await connection.execute(
                text(
                    "INSERT INTO environment_services (environment_id, service_id, project_id, "
                    "scope_id) VALUES (:e, :s, :p, :scope) RETURNING id"
                ),
                {"e": environment, "s": service, "p": project, "scope": svc_scope},
            )
        ).scalar_one()
        cluster_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('cluster', :ref, :parent) RETURNING id"
                ),
                {"ref": f"c-{suffix}", "parent": org},
            )
        ).scalar_one()
        cluster = (
            await connection.execute(
                text(
                    "INSERT INTO clusters (cluster_ref, display_name, catalog_source_kind, "
                    "scope_id) VALUES (:ref, 'Cluster', 'manifest', :scope) RETURNING id"
                ),
                {"ref": f"cl-{suffix}", "scope": cluster_scope},
            )
        ).scalar_one()
        binding = (
            await connection.execute(
                text(
                    """
                    INSERT INTO service_workload_bindings
                        (environment_service_id, project_id, environment_id, service_id,
                         cluster_id, namespace, workload_kind, workload_name,
                         resolved_resource_uid, resolved_at, preset_key, health_policy_key)
                    VALUES (:es, :p, :e, :s, :c, 'pilot-dev', 'Deployment', 'pilot-api',
                            'uid-1', now(), 'kubernetes.baseline.v1', 'default.v1')
                    RETURNING id
                    """
                ),
                {
                    "es": environment_service,
                    "p": project,
                    "e": environment,
                    "s": service,
                    "c": cluster,
                },
            )
        ).scalar_one()
    return {
        "binding_id": binding,
        "cluster_id": cluster,
        "environment_service_id": environment_service,
        "project_id": project,
        "environment_id": environment,
        "service_id": service,
        "service_scope": svc_scope,
    }


def evaluation(world: dict[str, uuid.UUID], **overrides: Any) -> Evaluation:
    base: dict[str, Any] = {
        "binding_id": world["binding_id"],
        "environment_service_id": world["environment_service_id"],
        "project_id": world["project_id"],
        "environment_id": world["environment_id"],
        "service_id": world["service_id"],
        "binding_revision": 1,
        "lifecycle": "active",
        "resolved": True,
        "status": "critical",
        "reasons": ("no_ready_replicas",),
        "computed_at": BASE,
        "partial": False,
        "served_from_last_good": False,
        "project_key": "pilot",
        "environment_key": "dev",
        "service_key": "api",
    }
    base.update(overrides)
    return Evaluation(**base)


async def incidents_for(engine: AsyncEngine, binding_id: uuid.UUID) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT id, state, severity, title, primary_reason, resolved_at, "
                        "resolution_source, version FROM incidents "
                        "WHERE binding_id = :b ORDER BY opened_at"
                    ),
                    {"b": binding_id},
                )
            ).all()
        )


async def events_for(engine: AsyncEngine, incident_id: uuid.UUID) -> list[tuple[str, Any]]:
    async with engine.connect() as connection:
        return [
            (row[0], row[1])
            for row in (
                await connection.execute(
                    text(
                        "SELECT event_type, occurred_at FROM incident_events "
                        "WHERE incident_id = :i ORDER BY occurred_at, id"
                    ),
                    {"i": incident_id},
                )
            ).all()
        ]


async def transitions_for(engine: AsyncEngine, binding_id: uuid.UUID) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT previous_status, new_status, reasons FROM "
                        "service_health_transitions WHERE binding_id = :b "
                        "ORDER BY computed_at, id"
                    ),
                    {"b": binding_id},
                )
            ).all()
        )


# --- opening -------------------------------------------------------------


async def test_one_critical_starts_a_streak_but_opens_nothing(engine: AsyncEngine) -> None:
    """A single bad reading is a spike. Two are a pattern."""
    world = await make_world(engine)
    result = await process_evaluation(engine, evaluation(world))

    assert result.consecutive_critical == 1
    assert result.incident_opened is None
    assert await incidents_for(engine, world["binding_id"]) == []


async def test_the_second_critical_opens_exactly_one_incident(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))
    result = await process_evaluation(
        engine, evaluation(world, computed_at=BASE + timedelta(minutes=1))
    )

    assert result.incident_opened is not None
    rows = await incidents_for(engine, world["binding_id"])
    assert len(rows) == 1
    assert rows[0][1] == "open"
    assert rows[0][2] == "critical"
    # Title comes from the server's reason dictionary, never from a caller.
    assert rows[0][3] == "api (dev): No replicas ready"
    assert rows[0][4] == "no_ready_replicas"

    assert [event for event, _ in await events_for(engine, rows[0][0])] == ["opened"]


async def test_a_third_critical_does_not_open_a_second_incident(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    for minute in range(4):
        await process_evaluation(
            engine, evaluation(world, computed_at=BASE + timedelta(minutes=minute))
        )
    rows = await incidents_for(engine, world["binding_id"])
    assert len(rows) == 1
    # Still critical is not news: no event per polling cycle.
    assert [event for event, _ in await events_for(engine, rows[0][0])] == ["opened"]


async def test_reprocessing_the_same_evaluation_changes_nothing(engine: AsyncEngine) -> None:
    """A cached verdict carries the timestamp it was computed at.

    Counting it twice would let a page refresh advance a streak, which is
    how a service opens an incident because someone was watching it.
    """
    world = await make_world(engine)
    first = await process_evaluation(engine, evaluation(world))
    repeat = await process_evaluation(engine, evaluation(world))

    assert first.duplicate is False
    assert repeat.duplicate is True
    assert repeat.consecutive_critical == 1
    assert await incidents_for(engine, world["binding_id"]) == []
    # And no second transition row for an observation we already recorded.
    assert len(await transitions_for(engine, world["binding_id"])) == 1


async def test_concurrent_processors_open_a_single_incident(engine: AsyncEngine) -> None:
    """The database, not the application, is what makes this true."""
    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))

    # Two workers, two DISTINCT evaluations, arriving at the same moment.
    results = await asyncio.gather(
        process_evaluation(engine, evaluation(world, computed_at=BASE + timedelta(minutes=1))),
        process_evaluation(engine, evaluation(world, computed_at=BASE + timedelta(minutes=2))),
    )
    opened = [r.incident_opened for r in results if r.incident_opened is not None]
    assert len(opened) == 1
    assert len(await incidents_for(engine, world["binding_id"])) == 1


# --- what must never open an incident ------------------------------------


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("degraded", {"status": "degraded", "reasons": ("restart_spike",)}),
        ("unknown", {"status": "unknown", "reasons": ("datasource_unavailable",)}),
        ("stale", {"status": "stale", "reasons": ("telemetry_stale",)}),
        ("not_configured", {"status": "not_configured", "reasons": ("no_binding",)}),
        ("partial", {"partial": True, "reasons": ("no_ready_replicas", "partial_result")}),
        ("last_good", {"served_from_last_good": True}),
        ("query_failed", {"status": "unknown", "reasons": ("query_failed",)}),
        ("disabled", {"lifecycle": "disabled"}),
        ("unresolved", {"resolved": False}),
    ],
)
async def test_untrustworthy_or_non_critical_readings_open_nothing(
    engine: AsyncEngine, label: str, overrides: dict[str, Any]
) -> None:
    world = await make_world(engine)
    for minute in range(4):
        await process_evaluation(
            engine,
            evaluation(world, computed_at=BASE + timedelta(minutes=minute), **overrides),
        )
    assert await incidents_for(engine, world["binding_id"]) == [], label


async def test_an_unreadable_sample_breaks_a_critical_streak(engine: AsyncEngine) -> None:
    """A run of criticals interrupted by an outage is not a run.

    Otherwise a datasource that flaps between critical and unreachable
    accumulates a streak it never actually observed.
    """
    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))
    interrupted = await process_evaluation(
        engine,
        evaluation(
            world,
            computed_at=BASE + timedelta(minutes=1),
            status="unknown",
            reasons=("datasource_unavailable",),
        ),
    )
    assert interrupted.consecutive_critical == 0

    resumed = await process_evaluation(
        engine, evaluation(world, computed_at=BASE + timedelta(minutes=2))
    )
    assert resumed.consecutive_critical == 1
    assert await incidents_for(engine, world["binding_id"]) == []


# --- recovery -------------------------------------------------------------


async def open_incident(engine: AsyncEngine, world: dict[str, uuid.UUID]) -> Any:
    await process_evaluation(engine, evaluation(world))
    await process_evaluation(engine, evaluation(world, computed_at=BASE + timedelta(minutes=1)))
    rows = await incidents_for(engine, world["binding_id"])
    assert len(rows) == 1
    return rows[0]


async def test_one_healthy_reading_starts_recovery_without_resolving(
    engine: AsyncEngine,
) -> None:
    world = await make_world(engine)
    incident = await open_incident(engine, world)

    result = await process_evaluation(
        engine,
        evaluation(
            world, computed_at=BASE + timedelta(minutes=2), status="healthy", reasons=()
        ),
    )
    assert result.incident_resolved is None
    assert result.consecutive_healthy == 1

    rows = await incidents_for(engine, world["binding_id"])
    assert rows[0][1] == "open"
    assert [event for event, _ in await events_for(engine, incident[0])] == [
        "opened",
        "recovery_started",
    ]


async def test_the_second_healthy_reading_auto_resolves(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    incident = await open_incident(engine, world)
    for minute in (2, 3):
        result = await process_evaluation(
            engine,
            evaluation(
                world, computed_at=BASE + timedelta(minutes=minute), status="healthy", reasons=()
            ),
        )

    assert result.incident_resolved == incident[0]
    rows = await incidents_for(engine, world["binding_id"])
    assert rows[0][1] == "resolved"
    assert rows[0][5] is not None
    assert rows[0][6] == "health_recovered"
    assert [event for event, _ in await events_for(engine, incident[0])] == [
        "opened",
        "recovery_started",
        "auto_resolved",
    ]


async def test_an_interrupted_recovery_is_recorded_and_does_not_resolve(
    engine: AsyncEngine,
) -> None:
    world = await make_world(engine)
    incident = await open_incident(engine, world)
    await process_evaluation(
        engine,
        evaluation(world, computed_at=BASE + timedelta(minutes=2), status="healthy", reasons=()),
    )
    # Back to critical before the second healthy reading.
    await process_evaluation(engine, evaluation(world, computed_at=BASE + timedelta(minutes=3)))

    rows = await incidents_for(engine, world["binding_id"])
    assert rows[0][1] == "open"
    assert [event for event, _ in await events_for(engine, incident[0])] == [
        "opened",
        "recovery_started",
        "recovery_interrupted",
    ]


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("degraded", {"status": "degraded", "reasons": ("restart_spike",)}),
        ("unknown", {"status": "unknown", "reasons": ("datasource_unavailable",)}),
        ("stale", {"status": "stale", "reasons": ("telemetry_stale",)}),
        ("partial healthy", {"status": "healthy", "partial": True}),
        ("last-good healthy", {"status": "healthy", "served_from_last_good": True}),
    ],
)
async def test_only_a_real_healthy_reading_counts_as_recovery(
    engine: AsyncEngine, label: str, overrides: dict[str, Any]
) -> None:
    world = await make_world(engine)
    await open_incident(engine, world)
    for minute in (2, 3, 4):
        await process_evaluation(
            engine,
            evaluation(world, computed_at=BASE + timedelta(minutes=minute), **overrides),
        )
    rows = await incidents_for(engine, world["binding_id"])
    assert rows[0][1] == "open", label


async def test_disabling_a_binding_does_not_resolve_its_incident(
    engine: AsyncEngine,
) -> None:
    """Silence is not recovery.

    Closing here would let an outage be hidden by a configuration change,
    and the incident history would say the service got better.
    """
    world = await make_world(engine)
    await open_incident(engine, world)
    for minute in (2, 3, 4):
        await process_evaluation(
            engine,
            evaluation(
                world,
                computed_at=BASE + timedelta(minutes=minute),
                lifecycle="disabled",
                status="not_configured",
                reasons=("binding_disabled",),
            ),
        )
    rows = await incidents_for(engine, world["binding_id"])
    assert rows[0][1] == "open"
    assert rows[0][5] is None


async def test_an_acknowledged_incident_still_auto_resolves(engine: AsyncEngine) -> None:
    """Acknowledging says a human saw it, not that it stopped happening."""
    world = await make_world(engine)
    incident = await open_incident(engine, world)
    actor = await make_identity(engine)

    async with engine.begin() as connection:
        ack = await acknowledge_incident(connection, incident[0], actor, incident[7])
    assert ack["outcome"] == "acknowledged"

    # Recovery is observed AFTER the acknowledgement — an acknowledge is
    # stamped with the wall clock, so the timeline below is the real order
    # of events rather than an artefact of synthetic timestamps.
    recovered_at = datetime.now(UTC)
    for seconds in (1, 61):
        await process_evaluation(
            engine,
            evaluation(
                world,
                computed_at=recovered_at + timedelta(seconds=seconds),
                status="healthy",
                reasons=(),
            ),
        )
    rows = await incidents_for(engine, world["binding_id"])
    assert rows[0][1] == "resolved"
    assert [event for event, _ in await events_for(engine, incident[0])] == [
        "opened",
        "acknowledged",
        "recovery_started",
        "auto_resolved",
    ]


# --- binding changes ------------------------------------------------------


async def test_a_binding_revision_change_resets_both_streaks(engine: AsyncEngine) -> None:
    """A binding read under a new preset is measuring something else."""
    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))
    result = await process_evaluation(
        engine,
        evaluation(world, computed_at=BASE + timedelta(minutes=1), binding_revision=2),
    )
    assert result.consecutive_critical == 1
    assert await incidents_for(engine, world["binding_id"]) == []


async def test_a_resolved_incident_is_never_reopened(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    first = await open_incident(engine, world)
    for minute in (2, 3):
        await process_evaluation(
            engine,
            evaluation(
                world, computed_at=BASE + timedelta(minutes=minute), status="healthy", reasons=()
            ),
        )
    # It fails again later.
    for minute in (4, 5):
        await process_evaluation(
            engine, evaluation(world, computed_at=BASE + timedelta(minutes=minute))
        )

    rows = await incidents_for(engine, world["binding_id"])
    assert len(rows) == 2
    assert rows[0][0] == first[0]
    assert rows[0][1] == "resolved"  # untouched
    assert rows[1][1] == "open"
    assert rows[1][0] != first[0]


# --- transitions ----------------------------------------------------------


async def test_transitions_are_written_only_on_a_real_change(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    for minute in range(4):
        await process_evaluation(
            engine, evaluation(world, computed_at=BASE + timedelta(minutes=minute))
        )
    # Four evaluations, one status: one row. A row per poll would be a
    # metric series, not a history.
    rows = await transitions_for(engine, world["binding_id"])
    assert len(rows) == 1
    assert rows[0][0] is None  # first observation, not a transition out of `unknown`
    assert rows[0][1] == "critical"

    await process_evaluation(
        engine,
        evaluation(world, computed_at=BASE + timedelta(minutes=5), status="healthy", reasons=()),
    )
    rows = await transitions_for(engine, world["binding_id"])
    assert len(rows) == 2
    assert (rows[1][0], rows[1][1]) == ("critical", "healthy")


async def test_a_changed_reason_set_is_a_transition_even_at_the_same_status(
    engine: AsyncEngine,
) -> None:
    """Still critical, but for a new reason, is something a reader needs."""
    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))
    await process_evaluation(
        engine,
        evaluation(
            world,
            computed_at=BASE + timedelta(minutes=1),
            reasons=("no_ready_replicas", "crash_loop"),
        ),
    )
    rows = await transitions_for(engine, world["binding_id"])
    assert len(rows) == 2
    assert set(rows[1][2]) == {"no_ready_replicas", "crash_loop"}


async def test_unknown_reason_codes_are_dropped_rather_than_stored(
    engine: AsyncEngine,
) -> None:
    """The history table is not a place an unreviewed string can arrive."""
    world = await make_world(engine)
    await process_evaluation(
        engine,
        evaluation(world, reasons=("no_ready_replicas", "definitely-not-a-code")),
    )
    rows = await transitions_for(engine, world["binding_id"])
    assert rows[0][2] == ["no_ready_replicas"]


# --- acknowledge ----------------------------------------------------------


async def make_identity(engine: AsyncEngine) -> uuid.UUID:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, display_name) "
                    "VALUES ('https://tests.drake.local/v2.0', :s, 'Responder') RETURNING id"
                ),
                {"s": f"resp-{uuid.uuid4().hex[:8]}"},
            )
        ).scalar_one()


async def test_acknowledge_records_who_and_when(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    incident = await open_incident(engine, world)
    actor = await make_identity(engine)

    async with engine.begin() as connection:
        result = await acknowledge_incident(connection, incident[0], actor, incident[7])

    assert result["outcome"] == "acknowledged"
    assert result["state"] == "acknowledged"
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT state, acknowledged_by, acknowledged_at, resolved_at "
                    "FROM incidents WHERE id = :i"
                ),
                {"i": incident[0]},
            )
        ).first()
    assert row is not None
    assert row[0] == "acknowledged"
    assert row[1] == actor
    assert row[2] is not None
    # Acknowledging does not close anything: the service is still down.
    assert row[3] is None


async def test_a_repeated_acknowledge_is_idempotent(engine: AsyncEngine) -> None:
    """A client that lost the response must be able to retry safely."""
    world = await make_world(engine)
    incident = await open_incident(engine, world)
    actor = await make_identity(engine)

    async with engine.begin() as connection:
        first = await acknowledge_incident(connection, incident[0], actor, incident[7])
    async with engine.begin() as connection:
        retry = await acknowledge_incident(connection, incident[0], actor, incident[7])

    assert first["outcome"] == "acknowledged"
    assert retry["outcome"] == "unchanged"
    assert retry["version"] == first["version"]
    # And exactly one timeline entry, not one per retry.
    assert [event for event, _ in await events_for(engine, incident[0])] == [
        "opened",
        "acknowledged",
    ]


async def test_a_stale_version_is_a_conflict(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    incident = await open_incident(engine, world)
    actor = await make_identity(engine)

    async with engine.begin() as connection:
        result = await acknowledge_incident(connection, incident[0], actor, incident[7] + 5)
    assert result["outcome"] == "conflict"


async def test_a_resolved_incident_cannot_be_acknowledged(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    incident = await open_incident(engine, world)
    for minute in (2, 3):
        await process_evaluation(
            engine,
            evaluation(
                world, computed_at=BASE + timedelta(minutes=minute), status="healthy", reasons=()
            ),
        )
    actor = await make_identity(engine)
    async with engine.begin() as connection:
        result = await acknowledge_incident(connection, incident[0], actor, incident[7])
    assert result["outcome"] == "conflict"


# --- what the migration itself guarantees ---------------------------------


async def test_the_database_refuses_a_second_active_incident(engine: AsyncEngine) -> None:
    """The rule does not depend on application code being correct.

    Written directly against the table: if the partial unique index were
    ever dropped, this fails even though every processor test still passes.
    """
    world = await make_world(engine)
    await open_incident(engine, world)

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO incidents
                        (binding_id, environment_service_id, project_id, environment_id,
                         service_id, state, severity, title, primary_reason,
                         binding_revision, opened_at, last_critical_at)
                    VALUES (:b, :es, :p, :e, :s, 'open', 'critical', 'duplicate',
                            'no_ready_replicas', 1, now(), now())
                    """
                ),
                {
                    "b": world["binding_id"],
                    "es": world["environment_service_id"],
                    "p": world["project_id"],
                    "e": world["environment_id"],
                    "s": world["service_id"],
                },
            )


async def test_a_resolved_incident_does_not_block_a_new_one(engine: AsyncEngine) -> None:
    """The index covers active states only — otherwise a service could
    never have a second incident in its lifetime."""
    world = await make_world(engine)
    async with engine.begin() as connection:
        for _ in range(2):
            await connection.execute(
                text(
                    """
                    INSERT INTO incidents
                        (binding_id, environment_service_id, project_id, environment_id,
                         service_id, state, severity, title, primary_reason,
                         binding_revision, opened_at, last_critical_at, resolved_at,
                         resolution_source)
                    VALUES (:b, :es, :p, :e, :s, 'resolved', 'critical', 'past',
                            'no_ready_replicas', 1, now(), now(), now(), 'health_recovered')
                    """
                ),
                {
                    "b": world["binding_id"],
                    "es": world["environment_service_id"],
                    "p": world["project_id"],
                    "e": world["environment_id"],
                    "s": world["service_id"],
                },
            )
    assert len(await incidents_for(engine, world["binding_id"])) == 2


# --- atomicity ------------------------------------------------------------


async def test_a_failed_transaction_leaves_no_partial_incident(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incident without its opening event would be a timeline that lies."""
    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))

    import drake_api.incidents.processor as processor_module

    original = processor_module._add_event

    async def exploding(*args: Any, **kwargs: Any) -> None:
        if args[2] == "opened":
            raise RuntimeError("write failed after the incident row")
        await original(*args, **kwargs)

    monkeypatch.setattr(processor_module, "_add_event", exploding)
    with pytest.raises(RuntimeError):
        await process_evaluation(
            engine, evaluation(world, computed_at=BASE + timedelta(minutes=1))
        )

    assert await incidents_for(engine, world["binding_id"]) == []
    # The state write rolled back too, so the streak did not silently move.
    async with engine.connect() as connection:
        streak = (
            await connection.execute(
                text("SELECT consecutive_critical FROM service_health_state WHERE binding_id = :b"),
                {"b": world["binding_id"]},
            )
        ).scalar_one()
    assert streak == 1
