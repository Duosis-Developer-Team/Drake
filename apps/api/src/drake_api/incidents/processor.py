"""Turn a stream of health verdicts into a lifecycle.

One transaction per evaluation, and the state row is locked inside it. That
lock is the whole concurrency design: two workers evaluating the same
binding at the same moment serialize on the row, so exactly one of them
sees `consecutive_critical` reach the threshold. The partial unique index
on active incidents is the second line — if application logic were ever
wrong, the database still refuses the duplicate.

What the processor will not do:

- Advance anything on a verdict it has already seen. A cached read returns
  the timestamp it was originally computed at, so re-processing it is a
  no-op rather than a second observation.
- Move a streak on a verdict Drake could not trust. A datasource outage
  neither opens an incident nor resolves one.
- Resolve an incident because a binding was disabled. Silence is not
  recovery, and closing on it would hide an outage behind a config change.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.incidents.model import (
    OPEN_THRESHOLD,
    RESOLVE_THRESHOLD,
    Evaluation,
)

logger = logging.getLogger("drake_api.incidents")

ACTIVE_STATES = ("open", "acknowledged")


@dataclass(frozen=True)
class ProcessResult:
    """What the evaluation actually changed. Empty is a normal outcome."""

    duplicate: bool = False
    transition_recorded: bool = False
    incident_opened: uuid.UUID | None = None
    incident_resolved: uuid.UUID | None = None
    events: tuple[str, ...] = ()
    consecutive_critical: int = 0
    consecutive_healthy: int = 0
    status: str = ""


def _reason_set(reasons: tuple[str, ...]) -> frozenset[str]:
    return frozenset(reasons)


async def _lock_state(
    connection: AsyncConnection, evaluation: Evaluation
) -> tuple[dict[str, Any], bool]:
    """Get the state row under a row lock, creating it if this is the first sight.

    The insert is `ON CONFLICT DO NOTHING` and the lock is taken afterwards,
    so two workers meeting a brand-new binding produce one row and one of
    them simply finds it already there.
    """
    created = False
    inserted = (
        await connection.execute(
            text(
                """
                INSERT INTO service_health_state
                    (binding_id, environment_service_id, project_id, environment_id,
                     service_id, binding_revision, current_status, current_reasons,
                     status_since, last_observed_at)
                VALUES
                    (:binding, :es, :project, :environment, :service, :revision,
                     :status, CAST(:reasons AS jsonb), :now, :now)
                ON CONFLICT (binding_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "binding": evaluation.binding_id,
                "es": evaluation.environment_service_id,
                "project": evaluation.project_id,
                "environment": evaluation.environment_id,
                "service": evaluation.service_id,
                "revision": evaluation.binding_revision,
                "status": evaluation.status,
                "reasons": json.dumps(list(evaluation.safe_reasons)),
                "now": evaluation.computed_at,
            },
        )
    ).first()
    created = inserted is not None

    row = (
        await connection.execute(
            text(
                """
                SELECT id, binding_revision, current_status, current_reasons,
                       status_since, last_evaluation_id, consecutive_critical,
                       consecutive_healthy, version
                FROM service_health_state
                WHERE binding_id = :binding
                FOR UPDATE
                """
            ),
            {"binding": evaluation.binding_id},
        )
    ).first()
    assert row is not None
    return (
        {
            "id": row[0],
            "binding_revision": row[1],
            "current_status": row[2],
            "current_reasons": tuple(row[3] or ()),
            "status_since": row[4],
            "last_evaluation_id": row[5],
            "consecutive_critical": row[6],
            "consecutive_healthy": row[7],
            "version": row[8],
        },
        created,
    )


async def _active_incident(
    connection: AsyncConnection, binding_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await connection.execute(
            text(
                """
                SELECT id, state, version, opened_at
                FROM incidents
                WHERE binding_id = :binding AND state IN ('open', 'acknowledged')
                FOR UPDATE
                """
            ),
            {"binding": binding_id},
        )
    ).first()
    if row is None:
        return None
    return {"id": row[0], "state": row[1], "version": row[2], "opened_at": row[3]}


async def _add_event(
    connection: AsyncConnection,
    incident_id: uuid.UUID,
    event_type: str,
    occurred_at: datetime,
    detail: dict[str, Any] | None = None,
    actor_identity_id: uuid.UUID | None = None,
) -> None:
    """Append one timeline entry. Nothing here is ever updated afterwards."""
    await connection.execute(
        text(
            """
            INSERT INTO incident_events
                (incident_id, event_type, occurred_at, actor_identity_id, detail)
            VALUES (:incident, :type, :at, :actor, CAST(:detail AS jsonb))
            """
        ),
        {
            "incident": incident_id,
            "type": event_type,
            "at": occurred_at,
            "actor": actor_identity_id,
            "detail": json.dumps(detail or {}),
        },
    )


async def process_evaluation(engine: AsyncEngine, evaluation: Evaluation) -> ProcessResult:
    """Apply one health verdict to persisted state, atomically.

    Either every write below lands or none of them does: a cancellation or
    a failure mid-way leaves no incident without its opening event, and no
    event pointing at an incident that was never created.
    """
    async with engine.begin() as connection:
        state, created = await _lock_state(connection, evaluation)

        # --- already seen ------------------------------------------------
        if state["last_evaluation_id"] == evaluation.evaluation_id:
            return ProcessResult(
                duplicate=True,
                consecutive_critical=state["consecutive_critical"],
                consecutive_healthy=state["consecutive_healthy"],
                status=state["current_status"],
            )

        # --- streaks -----------------------------------------------------
        # A binding read under a different preset or policy is measuring
        # something else, so whatever it accumulated before does not carry.
        revision_changed = state["binding_revision"] != evaluation.binding_revision
        previous_critical = 0 if revision_changed else state["consecutive_critical"]
        previous_healthy = 0 if revision_changed else state["consecutive_healthy"]

        if evaluation.opens_incidents:
            critical_streak, healthy_streak = previous_critical + 1, 0
        elif evaluation.proves_recovery:
            critical_streak, healthy_streak = 0, previous_healthy + 1
        else:
            # Degraded, or anything Drake could not trust. Both streaks
            # break: a run of criticals interrupted by an unreadable sample
            # is not a run.
            critical_streak, healthy_streak = 0, 0

        # --- transition --------------------------------------------------
        previous_status = None if created else state["current_status"]
        previous_reasons = () if created else state["current_reasons"]
        status_changed = created or previous_status != evaluation.status
        reasons_changed = _reason_set(previous_reasons) != _reason_set(evaluation.safe_reasons)
        record_transition = status_changed or reasons_changed

        if record_transition:
            await connection.execute(
                text(
                    """
                    INSERT INTO service_health_transitions
                        (binding_id, environment_service_id, previous_status, new_status,
                         reasons, computed_at, binding_revision, evaluation_id)
                    VALUES (:binding, :es, :previous, :new, CAST(:reasons AS jsonb),
                            :computed_at, :revision, :evaluation)
                    """
                ),
                {
                    "binding": evaluation.binding_id,
                    "es": evaluation.environment_service_id,
                    "previous": previous_status,
                    "new": evaluation.status,
                    "reasons": json.dumps(list(evaluation.safe_reasons)),
                    "computed_at": evaluation.computed_at,
                    "revision": evaluation.binding_revision,
                    "evaluation": evaluation.evaluation_id,
                },
            )

        await connection.execute(
            text(
                """
                UPDATE service_health_state
                SET binding_revision = :revision,
                    current_status = :status,
                    current_reasons = CAST(:reasons AS jsonb),
                    status_since = CASE WHEN :status_changed THEN :computed_at
                                        ELSE status_since END,
                    last_observed_at = :computed_at,
                    last_reliable_at = CASE WHEN :reliable THEN :computed_at
                                            ELSE last_reliable_at END,
                    last_evaluation_id = :evaluation,
                    consecutive_critical = :critical,
                    consecutive_healthy = :healthy,
                    version = version + 1,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": state["id"],
                "revision": evaluation.binding_revision,
                "status": evaluation.status,
                "reasons": json.dumps(list(evaluation.safe_reasons)),
                "status_changed": status_changed,
                "computed_at": evaluation.computed_at,
                "reliable": evaluation.reliable,
                "evaluation": evaluation.evaluation_id,
                "critical": critical_streak,
                "healthy": healthy_streak,
            },
        )

        # --- incident lifecycle ------------------------------------------
        incident = await _active_incident(connection, evaluation.binding_id)
        opened: uuid.UUID | None = None
        resolved: uuid.UUID | None = None
        events: list[str] = []

        if incident is None:
            if evaluation.opens_incidents and critical_streak >= OPEN_THRESHOLD:
                opened = await _open_incident(connection, evaluation)
                if opened is not None:
                    events.append("opened")
        else:
            incident_id = incident["id"]
            if evaluation.opens_incidents:
                await connection.execute(
                    text(
                        """
                        UPDATE incidents
                        SET last_critical_at = :at, updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": incident_id, "at": evaluation.computed_at},
                )
                if previous_healthy > 0:
                    # A recovery that had started and did not hold. Recorded
                    # once, on the reading that broke it.
                    await _add_event(
                        connection,
                        incident_id,
                        "recovery_interrupted",
                        evaluation.computed_at,
                        {"status": evaluation.status},
                    )
                    events.append("recovery_interrupted")
            elif evaluation.proves_recovery:
                if healthy_streak >= RESOLVE_THRESHOLD:
                    await connection.execute(
                        text(
                            """
                            UPDATE incidents
                            SET state = 'resolved',
                                resolved_at = :at,
                                resolution_source = 'health_recovered',
                                version = version + 1,
                                updated_at = now()
                            WHERE id = :id
                            """
                        ),
                        {"id": incident_id, "at": evaluation.computed_at},
                    )
                    await _add_event(
                        connection,
                        incident_id,
                        "auto_resolved",
                        evaluation.computed_at,
                        {"healthy_evaluations": healthy_streak},
                    )
                    resolved = incident_id
                    events.append("auto_resolved")
                elif previous_healthy == 0:
                    await _add_event(
                        connection,
                        incident_id,
                        "recovery_started",
                        evaluation.computed_at,
                        {"healthy_evaluations": healthy_streak},
                    )
                    events.append("recovery_started")
            elif previous_healthy > 0:
                # Degraded, unknown, stale or unreadable while a recovery
                # was in progress. The streak is already broken above; this
                # is the note that says why.
                await _add_event(
                    connection,
                    incident_id,
                    "recovery_interrupted",
                    evaluation.computed_at,
                    {"status": evaluation.status},
                )
                events.append("recovery_interrupted")

        return ProcessResult(
            duplicate=False,
            transition_recorded=record_transition,
            incident_opened=opened,
            incident_resolved=resolved,
            events=tuple(events),
            consecutive_critical=critical_streak,
            consecutive_healthy=healthy_streak,
            status=evaluation.status,
        )


async def _open_incident(
    connection: AsyncConnection, evaluation: Evaluation
) -> uuid.UUID | None:
    """Open one, or discover that a concurrent worker already did.

    The `IntegrityError` path is not a failure: it is the partial unique
    index doing exactly what it exists for. Losing that race means an
    incident is already open, which is the outcome we wanted anyway.
    """
    savepoint = await connection.begin_nested()
    try:
        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO incidents
                        (binding_id, environment_service_id, project_id, environment_id,
                         service_id, state, severity, title, primary_reason,
                         opening_reasons, binding_revision, opened_at, last_critical_at)
                    VALUES
                        (:binding, :es, :project, :environment, :service, 'open', 'critical',
                         :title, :primary, CAST(:reasons AS jsonb), :revision, :at, :at)
                    RETURNING id
                    """
                ),
                {
                    "binding": evaluation.binding_id,
                    "es": evaluation.environment_service_id,
                    "project": evaluation.project_id,
                    "environment": evaluation.environment_id,
                    "service": evaluation.service_id,
                    "title": evaluation.title(),
                    "primary": evaluation.primary_reason(),
                    "reasons": json.dumps(list(evaluation.safe_reasons)),
                    "revision": evaluation.binding_revision,
                    "at": evaluation.computed_at,
                },
            )
        ).first()
    except IntegrityError:
        await savepoint.rollback()
        return None
    await savepoint.commit()
    assert row is not None
    incident_id: uuid.UUID = row[0]
    await _add_event(
        connection,
        incident_id,
        "opened",
        evaluation.computed_at,
        {
            "primary_reason": evaluation.primary_reason(),
            "reasons": list(evaluation.safe_reasons),
            "critical_evaluations": OPEN_THRESHOLD,
        },
    )
    return incident_id


async def acknowledge_incident(
    connection: AsyncConnection,
    incident_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
    expected_version: int,
) -> dict[str, Any]:
    """Acknowledge an open incident.

    Idempotent by design: retrying with the version that was already
    acknowledged returns the same answer rather than a conflict, so a
    client that lost the response can safely repeat the call. A genuinely
    stale version — someone else changed the incident in between — is a
    conflict, because silently proceeding would discard their change.
    """
    row = (
        await connection.execute(
            text(
                """
                SELECT id, state, version, acknowledged_at, acknowledged_by
                FROM incidents
                WHERE id = :id
                FOR UPDATE
                """
            ),
            {"id": incident_id},
        )
    ).first()
    if row is None:
        return {"outcome": "not_found"}

    state, version = row[1], row[2]

    if state == "acknowledged":
        # Already done. A safe retry — same actor, and the version the
        # caller holds is either the pre- or post-ack one — is not an error.
        if expected_version in (version, version - 1) and row[4] == actor_identity_id:
            return {
                "outcome": "unchanged",
                "id": str(incident_id),
                "state": state,
                "version": version,
                "acknowledged_at": row[3].isoformat() if row[3] else None,
            }
        return {"outcome": "conflict", "version": version}
    if state == "resolved":
        # Resolved incidents are immutable. Nothing is acknowledged after
        # the fact, because the timeline would then describe an order of
        # events that never happened.
        return {"outcome": "conflict", "version": version}
    if expected_version != version:
        return {"outcome": "conflict", "version": version}

    now = datetime.now(UTC)
    updated = (
        await connection.execute(
            text(
                """
                UPDATE incidents
                SET state = 'acknowledged',
                    acknowledged_at = :at,
                    acknowledged_by = :actor,
                    version = version + 1,
                    updated_at = now()
                WHERE id = :id AND version = :expected
                RETURNING version, acknowledged_at
                """
            ),
            {
                "id": incident_id,
                "at": now,
                "actor": actor_identity_id,
                "expected": expected_version,
            },
        )
    ).first()
    if updated is None:
        return {"outcome": "conflict", "version": version}

    await _add_event(
        connection, incident_id, "acknowledged", now, {}, actor_identity_id=actor_identity_id
    )
    return {
        "outcome": "acknowledged",
        "id": str(incident_id),
        "state": "acknowledged",
        "version": updated[0],
        "acknowledged_at": updated[1].isoformat(),
    }


async def assign_incident(
    connection: AsyncConnection,
    incident_id: uuid.UUID,
    *,
    assignee_id: uuid.UUID | None,
    actor_identity_id: uuid.UUID,
    expected_version: int,
) -> dict[str, Any]:
    """Set or clear an incident's owner.

    Assignment is not acknowledgement and not resolution: it says who is
    looking, not that anyone has looked or that the problem stopped. The
    three are separate columns and separate timeline events for exactly
    that reason.

    Idempotent the same way acknowledge is: repeating the call with the
    version that already produced this owner returns the same answer rather
    than a conflict, so a client that lost the response can safely retry. A
    genuinely stale version is a conflict, because proceeding would discard
    whatever the other writer did.
    """
    row = (
        await connection.execute(
            text(
                """
                SELECT id, state, version, assigned_identity_id, assigned_at
                FROM incidents WHERE id = :id FOR UPDATE
                """
            ),
            {"id": incident_id},
        )
    ).first()
    if row is None:
        return {"outcome": "not_found"}

    state, version, current = row[1], row[2], row[3]

    if state == "resolved":
        # Resolved incidents are immutable. Reassigning one would describe
        # an ownership that never existed while the problem did.
        return {"outcome": "conflict", "version": version}
    if current == assignee_id:
        # Already there. A safe retry — the caller holds either the pre- or
        # post-assignment version — is not a second assignment.
        if expected_version in (version, version - 1):
            return {
                "outcome": "unchanged",
                "id": str(incident_id),
                "state": state,
                "version": version,
                "assigned_at": row[4].isoformat() if row[4] else None,
            }
        return {"outcome": "conflict", "version": version}
    if expected_version != version:
        return {"outcome": "conflict", "version": version}

    now = datetime.now(UTC)
    updated = (
        await connection.execute(
            text(
                """
                UPDATE incidents
                SET assigned_identity_id = :assignee,
                    assigned_at = CASE
                        WHEN CAST(:assignee AS uuid) IS NULL THEN NULL ELSE :at END,
                    assigned_by = CASE
                        WHEN CAST(:assignee AS uuid) IS NULL THEN NULL ELSE :actor END,
                    version = version + 1,
                    updated_at = now()
                WHERE id = :id AND version = :expected
                RETURNING version, assigned_at
                """
            ),
            {
                "id": incident_id,
                "assignee": assignee_id,
                "actor": actor_identity_id,
                "at": now,
                "expected": expected_version,
            },
        )
    ).first()
    if updated is None:
        return {"outcome": "conflict", "version": version}

    await _add_event(
        connection,
        incident_id,
        "assigned" if assignee_id is not None else "unassigned",
        now,
        # The identity id only. A timeline entry naming an email would
        # publish it to everyone who can read the incident.
        {"assignee_id": str(assignee_id)} if assignee_id is not None else {},
        actor_identity_id=actor_identity_id,
    )
    return {
        "outcome": "assigned" if assignee_id is not None else "unassigned",
        "id": str(incident_id),
        "state": state,
        "version": updated[0],
        "assigned_at": updated[1].isoformat() if updated[1] else None,
    }
