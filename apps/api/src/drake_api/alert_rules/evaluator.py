"""Record a binding's sample and judge the rules that cover it (ADR-0031).

Called by the incident runner right after a binding's fresh health verdict,
in the same cycle. That is the whole data path:

    health verdict -> service_health_samples row -> each covering rule's step
                   -> alert_rule_states / alert_rule_events
                   -> (critical, not muted) an incident with source 'rule'

An incident opened here is an ordinary incident: the notification planner
routes its `opened` and `auto_resolved` events like any other. It is keyed
by rule and binding (`correlation_key`), so two rules firing on one service
are two incidents and one rule firing twice is never two at once.

A verdict served from the last-good cache (the provider failed this cycle)
records no sample and moves no rule. Old evidence is not a new observation.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.alert_rules.model import RuleCondition, RuleState, sample_from_health, step

logger = logging.getLogger("drake_api.alert_rules.evaluator")

# How long samples are kept. The screen reads one day; a week more lets a
# rule be judged against last week's incident before it is written.
SAMPLE_RETENTION = timedelta(days=8)

_COVERING_RULES = """
SELECT r.id, r.name, r.signal, r.operator, r.threshold, r.for_seconds, r.severity,
       r.muted_until, s.state, s.breach_since, s.firing_since, s.incident_id
FROM alert_rules r
LEFT JOIN alert_rule_states s ON s.rule_id = r.id AND s.binding_id = :binding
WHERE r.enabled
  AND r.project_id = :project
  AND (r.environment_id IS NULL OR r.environment_id = :environment)
  AND (r.service_id IS NULL OR r.service_id = :service)
"""


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


async def _record_sample(
    connection: AsyncConnection, binding_id: uuid.UUID, at: datetime, sample: dict[str, Any]
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO service_health_samples
                (binding_id, observed_at, error_ratio, latency_p95_seconds, request_rate,
                 restarts, cpu_utilization, memory_utilization, unavailable_replicas)
            VALUES (:binding, :at, :error_ratio, :latency_p95_seconds, :request_rate,
                    :restarts, :cpu_utilization, :memory_utilization, :unavailable_replicas)
            ON CONFLICT (binding_id, observed_at) DO NOTHING
            """
        ),
        {"binding": binding_id, "at": at, **sample},
    )
    await connection.execute(
        text(
            "DELETE FROM service_health_samples WHERE binding_id = :binding AND observed_at < :cut"
        ),
        {"binding": binding_id, "cut": at - SAMPLE_RETENTION},
    )


async def _incident_event(
    connection: AsyncConnection,
    incident_id: uuid.UUID,
    event_type: str,
    at: datetime,
    detail: dict[str, Any],
) -> None:
    await connection.execute(
        text(
            "INSERT INTO incident_events (incident_id, event_type, occurred_at, detail) "
            "VALUES (:incident, :type, :at, CAST(:detail AS jsonb))"
        ),
        {"incident": incident_id, "type": event_type, "at": at, "detail": json.dumps(detail)},
    )


async def _open_incident(
    connection: AsyncConnection,
    *,
    rule_id: uuid.UUID,
    rule_name: str,
    binding_id: uuid.UUID,
    catalog: dict[str, Any],
    service_key: str,
    value: float,
    at: datetime,
) -> uuid.UUID | None:
    key = f"rule:{rule_id}:{binding_id}"
    savepoint = await connection.begin_nested()
    try:
        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO incidents
                        (source, correlation_key, binding_id, environment_service_id,
                         project_id, environment_id, service_id, state, severity, priority,
                         title, primary_reason, opening_reasons, binding_revision,
                         opened_at, last_critical_at)
                    VALUES ('rule', :key, NULL, :es, :project, :environment, :service,
                            'open', 'critical', 'P1', :title, 'rule_firing',
                            CAST(:reasons AS jsonb), 1, :at, :at)
                    RETURNING id
                    """
                ),
                {
                    "key": key,
                    "es": catalog["environment_service_id"],
                    "project": catalog["project_id"],
                    "environment": catalog["environment_id"],
                    "service": catalog["service_id"],
                    "title": f"{rule_name} · {service_key}"[:200],
                    "reasons": json.dumps(["rule_firing"]),
                    "at": at,
                },
            )
        ).first()
    except IntegrityError:
        # The partial unique index on correlation_key: an incident for this
        # rule and binding is already open, which is the outcome we wanted.
        await savepoint.rollback()
        return None
    await savepoint.commit()
    if row is None:
        return None
    incident_id = uuid.UUID(str(row[0]))
    await _incident_event(
        connection,
        incident_id,
        "opened",
        at,
        {"source": "rule", "rule_id": str(rule_id), "value": value},
    )
    return incident_id


async def _resolve_incident(
    connection: AsyncConnection, incident_id: uuid.UUID, at: datetime
) -> None:
    updated = (
        await connection.execute(
            text(
                """
                UPDATE incidents
                SET state = 'resolved', resolved_at = :at, resolution_source = 'rule_resolved',
                    version = version + 1, updated_at = now()
                WHERE id = :id AND state IN ('open', 'acknowledged')
                RETURNING id
                """
            ),
            {"id": incident_id, "at": at},
        )
    ).first()
    if updated is not None:
        await _incident_event(connection, incident_id, "auto_resolved", at, {"source": "rule"})


async def record_and_evaluate(
    engine: AsyncEngine,
    *,
    binding_id: uuid.UUID,
    catalog: dict[str, Any],
    service_key: str,
    payload: dict[str, Any],
) -> int:
    """Store this cycle's sample and step every covering rule. Returns how
    many rules changed state, for the cycle report."""
    if payload.get("served_from_last_good") or payload.get("cached"):
        return 0
    at = _parse(payload.get("computed_at"))
    if at is None:
        return 0
    sample = sample_from_health(payload)
    changed = 0
    async with engine.begin() as connection:
        await _record_sample(connection, binding_id, at, sample)
        rules = (
            await connection.execute(
                text(_COVERING_RULES),
                {
                    "binding": binding_id,
                    "project": catalog["project_id"],
                    "environment": catalog["environment_id"],
                    "service": catalog["service_id"],
                },
            )
        ).all()
        for row in rules:
            rule_id, name, signal, operator, threshold, for_seconds, severity = row[:7]
            muted_until, state, breach_since, firing_since, incident_id = row[7:]
            condition = RuleCondition(signal, operator, float(threshold), int(for_seconds))
            previous = RuleState(state or "ok", breach_since, firing_since)
            value = sample.get(signal)
            result = step(condition, value, previous, at)
            new_incident = incident_id
            if result.event == "fired":
                muted = muted_until is not None and muted_until > at
                if severity == "critical" and not muted:
                    new_incident = await _open_incident(
                        connection,
                        rule_id=rule_id,
                        rule_name=name,
                        binding_id=binding_id,
                        catalog=catalog,
                        service_key=service_key,
                        value=float(value) if value is not None else float(threshold),
                        at=at,
                    )
                await connection.execute(
                    text(
                        "INSERT INTO alert_rule_events "
                        "(rule_id, binding_id, kind, at, value, threshold, incident_id) "
                        "VALUES (:rule, :binding, 'fired', :at, :value, :thr, :incident)"
                    ),
                    {
                        "rule": rule_id,
                        "binding": binding_id,
                        "at": at,
                        "value": value,
                        "thr": threshold,
                        "incident": new_incident,
                    },
                )
            elif result.event == "resolved":
                if incident_id is not None:
                    await _resolve_incident(connection, incident_id, at)
                new_incident = None
                await connection.execute(
                    text(
                        "INSERT INTO alert_rule_events "
                        "(rule_id, binding_id, kind, at, value, threshold, incident_id) "
                        "VALUES (:rule, :binding, 'resolved', :at, :value, :thr, :incident)"
                    ),
                    {
                        "rule": rule_id,
                        "binding": binding_id,
                        "at": at,
                        "value": value,
                        "thr": threshold,
                        "incident": incident_id,
                    },
                )
            if result.state.state != previous.state:
                changed += 1
            await connection.execute(
                text(
                    """
                    INSERT INTO alert_rule_states
                        (rule_id, binding_id, state, breach_since, firing_since, last_value,
                         evaluated_at, incident_id)
                    VALUES (:rule, :binding, :state, :breach, :firing, :value, :at, :incident)
                    ON CONFLICT (rule_id, binding_id) DO UPDATE SET
                        state = EXCLUDED.state, breach_since = EXCLUDED.breach_since,
                        firing_since = EXCLUDED.firing_since,
                        last_value = COALESCE(EXCLUDED.last_value, alert_rule_states.last_value),
                        evaluated_at = EXCLUDED.evaluated_at, incident_id = EXCLUDED.incident_id
                    """
                ),
                {
                    "rule": rule_id,
                    "binding": binding_id,
                    "state": result.state.state,
                    "breach": result.state.breach_since,
                    "firing": result.state.firing_since,
                    "value": value,
                    "at": at,
                    "incident": new_incident,
                },
            )
    return changed
