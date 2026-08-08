"""Incident events → matching policies → notifications and deliveries.

An outbox, deliberately. Calling a webhook inside the incident transaction
would let a slow receiver hold a database lock and a failing one roll back
the incident that caused it — so the incident commits first, and this
reads the committed events afterwards.

Three properties make it safe to run repeatedly:

**Every event is planned exactly once.** A row in `notification_event_plans`
records the decision, including "nothing matched". Without that, an event
with no policy would be rescanned on every cycle forever.

**Duplicates are impossible, not merely unlikely.** Unique constraints sit
on the CANONICAL target — (event, recipient identity) for in-app and
(event, destination key) for webhooks — so two policies, or two
destination rows naming the same person or the same operator endpoint,
produce one notification. Two planners racing produce one row.

**A policy applies from when it was configured, not backwards.** Matching
compares the event's immutable `created_at` against the policy's and the
binding's `effective_from`, so a policy created — or re-scoped — after an
event never routes it. A backlog that built up while the worker was off is
still delivered, because those events are newer than the policy.

**A broken policy is one broken policy.** Each destination is planned in
its own savepoint, so one failure does not cost the others their delivery.
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

from drake_api.notifications.model import (
    NOTIFIABLE_EVENTS,
    PLANNER_VERSION,
    NotifiableEvent,
    idempotency_key,
    in_app_metadata,
    webhook_payload,
)

logger = logging.getLogger("drake_api.notifications.planner")


@dataclass
class PlanReport:
    events_planned: int = 0
    in_app_created: int = 0
    webhooks_created: int = 0
    destinations_failed: int = 0
    events_failed: int = 0


async def unplanned_events(connection: AsyncConnection, limit: int) -> list[NotifiableEvent]:
    """Committed, notifiable events that have not been planned yet.

    Deliberately unbounded in time: a backlog that built up while the
    worker was off must still be delivered. What keeps an old event from
    being routed by a new policy is the effective-time comparison in
    `matching_destinations`, not a scan window here — a window would drop
    the backlog and the retroactivity together.
    """
    rows = (
        await connection.execute(
            text(
                """
                SELECT ev.id, ev.incident_id, ev.event_type, ev.occurred_at,
                       ev.created_at,
                       i.state, i.severity, i.title, i.primary_reason,
                       i.opened_at, i.acknowledged_at, i.resolved_at,
                       i.project_id, i.environment_id, i.service_id,
                       p.project_key, e.environment_key, sd.service_key,
                       b.id, b.namespace, b.workload_name, c.cluster_ref
                FROM incident_events ev
                JOIN incidents i ON i.id = ev.incident_id
                JOIN projects p ON p.id = i.project_id
                JOIN environments e ON e.id = i.environment_id
                JOIN service_definitions sd ON sd.id = i.service_id
                JOIN service_workload_bindings b ON b.id = i.binding_id
                JOIN clusters c ON c.id = b.cluster_id
                LEFT JOIN notification_event_plans pl ON pl.incident_event_id = ev.id
                WHERE pl.incident_event_id IS NULL
                  AND ev.event_type = ANY(:types)
                ORDER BY ev.occurred_at, ev.id
                LIMIT :limit
                """
            ),
            {"types": list(NOTIFIABLE_EVENTS), "limit": limit},
        )
    ).all()
    return [
        NotifiableEvent(
            incident_event_id=row[0],
            incident_id=row[1],
            event_type=row[2],
            occurred_at=row[3],
            # The immutable moment Drake recorded the event. `occurred_at`
            # is the evaluation's timestamp and can be backdated by a
            # replayed reading, so it is not a safe basis for deciding
            # which policy revision applies.
            recorded_at=row[4],
            incident_state=row[5],
            severity=row[6],
            title=row[7],
            primary_reason=row[8],
            opened_at=row[9],
            acknowledged_at=row[10],
            resolved_at=row[11],
            project_id=row[12],
            environment_id=row[13],
            service_id=row[14],
            project_key=row[15],
            environment_key=row[16],
            service_key=row[17],
            binding_id=row[18],
            namespace=row[19],
            workload_name=row[20],
            cluster_ref=row[21],
        )
        for row in rows
    ]


async def matching_destinations(
    connection: AsyncConnection, event: NotifiableEvent
) -> list[dict[str, Any]]:
    """Canonical destinations for an event, with the policies that matched.

    Every filter is `AND`, and every one of them is an id, a timestamp
    comparison or an allowlist membership — there is no expression to
    evaluate. Grouping is on the canonical target rather than the
    destination row, which is what stops three overlapping policies (or
    three rows naming one person) from sending the same person three
    copies.
    """
    rows = (
        await connection.execute(
            text(
                """
                SELECT d.destination_type,
                       -- Canonical target: WHO ultimately receives this.
                       -- Grouping on the destination ROW would let two rows
                       -- naming one person produce two notifications.
                       d.identity_id, d.destination_key,
                       min(d.id::text) AS destination_id,
                       max(d.payload_schema_version) AS payload_schema_version,
                       min(d.display_name) AS display_name,
                       array_agg(DISTINCT pol.id) AS policy_ids
                FROM notification_policies pol
                JOIN notification_policy_destinations pd ON pd.policy_id = pol.id
                JOIN notification_destinations d ON d.id = pd.destination_id
                WHERE pol.enabled
                  AND pd.enabled
                  AND d.enabled
                  AND pol.project_id = :project
                  AND (pol.environment_id IS NULL OR pol.environment_id = :environment)
                  AND (pol.service_id IS NULL OR pol.service_id = :service)
                  AND pol.event_types ? :event_type
                  AND pol.severities ? :severity
                  -- A policy applies from the moment it was created or
                  -- last re-scoped, and a destination from the moment it
                  -- was attached or re-enabled. An event that predates
                  -- either is not retroactively routed.
                  AND :recorded_at >= pol.effective_from
                  AND :recorded_at >= pd.effective_from
                  -- A destination belongs to the same project as the policy
                  -- that points at it; this re-checks at send time so a
                  -- later catalog change cannot widen an old policy.
                  AND d.project_id = pol.project_id
                GROUP BY d.destination_type, d.identity_id, d.destination_key
                ORDER BY d.destination_type, d.identity_id, d.destination_key
                """
            ),
            {
                "project": event.project_id,
                "environment": event.environment_id,
                "service": event.service_id,
                "event_type": event.event_type,
                "severity": event.severity,
                "recorded_at": event.recorded_at,
            },
        )
    ).all()
    return [
        {
            "type": row[0],
            "identity_id": row[1],
            "destination_key": row[2],
            "id": uuid.UUID(row[3]),
            "payload_schema_version": row[4],
            "display_name": row[5],
            "policy_ids": sorted(str(policy_id) for policy_id in row[6]),
        }
        for row in rows
    ]


async def _plan_destination(
    connection: AsyncConnection,
    event: NotifiableEvent,
    destination: dict[str, Any],
    base_url: str,
) -> str | None:
    """Create one notification or delivery. Returns what it created.

    Wrapped in a savepoint by the caller: a unique violation here means a
    concurrent planner already created the row, which is success.
    """
    if destination["type"] == "in_app_user":
        await connection.execute(
            text(
                """
                INSERT INTO in_app_notifications
                    (recipient_identity_id, incident_id, incident_event_id, destination_id,
                     matched_policy_ids, event_type, title, body, target_path,
                     metadata_snapshot)
                VALUES (:recipient, :incident, :event, :destination,
                        CAST(:policies AS jsonb), :event_type, :title, :body, :target,
                        CAST(:metadata AS jsonb))
                """
            ),
            {
                "recipient": destination["identity_id"],
                "incident": event.incident_id,
                "event": event.incident_event_id,
                "destination": destination["id"],
                "policies": json.dumps(destination["policy_ids"]),
                "event_type": event.event_type,
                "title": event.headline(),
                "body": event.sentence(),
                "target": event.target_path(),
                "metadata": json.dumps(in_app_metadata(event)),
            },
        )
        return "in_app"

    delivery_id = uuid.uuid4()
    key = idempotency_key(
        event.incident_event_id, "webhook", str(destination["destination_key"])
    )
    await connection.execute(
        text(
            """
            INSERT INTO webhook_deliveries
                (id, incident_id, incident_event_id, destination_id, project_id,
                 destination_key, payload_schema_version, matched_policy_ids,
                 payload, idempotency_key, state, next_attempt_at)
            VALUES (:id, :incident, :event, :destination, :project,
                    :key, :schema, CAST(:policies AS jsonb),
                    CAST(:payload AS jsonb), :idem, 'pending', now())
            """
        ),
        {
            "id": delivery_id,
            "incident": event.incident_id,
            "event": event.incident_event_id,
            "destination": destination["id"],
            "project": event.project_id,
            "key": destination["destination_key"],
            "schema": destination["payload_schema_version"],
            "policies": json.dumps(destination["policy_ids"]),
            # Frozen now. Editing a policy later cannot rewrite what an
            # already-scheduled delivery will send.
            "payload": json.dumps(
                webhook_payload(
                    event, delivery_id=delivery_id, idempotency_key=key, base_url=base_url
                )
            ),
            "idem": key,
        },
    )
    return "webhook"


async def plan_event(
    connection: AsyncConnection, event: NotifiableEvent, base_url: str
) -> tuple[int, int, int]:
    """Plan one event. Returns (in_app, webhook, failed) counts.

    The plan row is written by the caller only after this returns, so an
    event is never marked planned while some of its destinations still have
    no result.
    """
    destinations = await matching_destinations(connection, event)
    in_app = webhooks = failed = 0

    for destination in destinations:
        savepoint = await connection.begin_nested()
        try:
            created = await _plan_destination(connection, event, destination, base_url)
        except IntegrityError:
            # Already planned by a concurrent run. The unique constraint is
            # the arbiter, and losing to it is the outcome we wanted.
            await savepoint.rollback()
            continue
        except Exception:
            # One destination's problem must not cost the others theirs.
            await savepoint.rollback()
            failed += 1
            logger.warning("notification planning failed for one destination")
            continue
        await savepoint.commit()
        if created == "in_app":
            in_app += 1
        else:
            webhooks += 1
    return in_app, webhooks, failed


async def plan_pending(
    engine: AsyncEngine, *, limit: int = 50, base_url: str = ""
) -> PlanReport:
    """One bounded planning pass over unplanned incident events."""
    report = PlanReport()
    async with engine.connect() as connection:
        events = await unplanned_events(connection, limit)

    for event in events:
        try:
            async with engine.begin() as connection:
                in_app, webhooks, failed = await plan_event(connection, event, base_url)
                # Marked planned in the SAME transaction that created the
                # rows: a crash between the two would either replay the
                # event or lose it, and both are worse than doing it once.
                await connection.execute(
                    text(
                        """
                        INSERT INTO notification_event_plans
                            (incident_event_id, state, planner_version, matched_destinations,
                             error_code)
                        VALUES (:event, :state, :version, :matched, :error)
                        ON CONFLICT (incident_event_id) DO NOTHING
                        """
                    ),
                    {
                        "event": event.incident_event_id,
                        # `planned` covers "matched nothing": that is a
                        # finished decision, not an open one.
                        "state": "planned" if failed == 0 else "failed",
                        "version": PLANNER_VERSION,
                        "matched": in_app + webhooks,
                        "error": "destination_planning_failed" if failed else None,
                    },
                )
            report.events_planned += 1
            report.in_app_created += in_app
            report.webhooks_created += webhooks
            report.destinations_failed += failed
        except Exception:
            # The event stays unplanned and is retried next cycle. Nothing
            # partial was committed — the transaction covered everything.
            report.events_failed += 1
            logger.warning("notification planning failed for one incident event")
    return report


async def mark_existing_events_planned(engine: AsyncEngine) -> int:
    """Optional housekeeping: mark the existing backlog as already planned.

    Correctness does NOT depend on this. Effective-time matching already
    guarantees that a policy never routes an event older than itself, so
    turning notifications on cannot deliver the incident history. This
    exists only to stop the planner from re-reading a large historical
    backlog on every cycle, and skipping it changes nothing an operator
    would notice.
    """
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                """
                INSERT INTO notification_event_plans
                    (incident_event_id, state, planner_version, matched_destinations,
                     error_code)
                SELECT ev.id, 'planned', :version, 0, NULL
                FROM incident_events ev
                LEFT JOIN notification_event_plans pl ON pl.incident_event_id = ev.id
                WHERE pl.incident_event_id IS NULL
                ON CONFLICT (incident_event_id) DO NOTHING
                """
            ),
            {"version": PLANNER_VERSION},
        )
    return int(result.rowcount or 0)


def utcnow() -> datetime:
    return datetime.now(UTC)
