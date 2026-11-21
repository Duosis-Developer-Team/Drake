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

from drake_api.alert_rules.model import (
    ANOMALY_OPERATORS,
    STORED_SIGNALS,
    STORED_TELEMETRY,
    RuleCondition,
    RuleState,
    anomaly_threshold,
    baseline_windows,
    effective_operator,
    normal_band,
    sample_from_health,
    step,
)

logger = logging.getLogger("drake_api.alert_rules.evaluator")

# How long samples are kept. The screen reads one day; a week more lets a
# rule be judged against last week's incident before it is written.
SAMPLE_RETENTION = timedelta(days=8)

_COVERING_RULES = """
SELECT r.id, r.name, r.signal, r.operator, r.threshold, r.for_seconds, r.severity,
       r.muted_until, s.state, s.breach_since, s.firing_since, s.incident_id,
       r.log_contains, r.route
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
                 restarts, cpu_utilization, memory_utilization, unavailable_replicas,
                 error_logs, fatal_logs, slow_requests, slowest_route_p95_seconds)
            VALUES (:binding, :at, :error_ratio, :latency_p95_seconds, :request_rate,
                    :restarts, :cpu_utilization, :memory_utilization, :unavailable_replicas,
                    :error_logs, :fatal_logs, :slow_requests, :slowest_route_p95_seconds)
            ON CONFLICT (binding_id, observed_at) DO NOTHING
            """
        ),
        {"binding": binding_id, "at": at, **dict.fromkeys(STORED_SIGNALS), **sample},
    )
    await connection.execute(
        text(
            "DELETE FROM service_health_samples WHERE binding_id = :binding AND observed_at < :cut"
        ),
        {"binding": binding_id, "cut": at - SAMPLE_RETENTION},
    )


async def _baseline(
    connection: AsyncConnection, binding_id: uuid.UUID, signal: str, at: datetime
) -> list[float]:
    """This binding's stored values of *signal* at this time of day over
    the past week (alert_rules.model.baseline_windows)."""
    if signal not in STORED_SIGNALS:
        return []
    windows = baseline_windows(at)
    clauses = " OR ".join(f"observed_at BETWEEN :s{i} AND :e{i}" for i in range(len(windows)))
    params: dict[str, Any] = {"binding": binding_id}
    for i, (start, end) in enumerate(windows):
        params[f"s{i}"] = start
        params[f"e{i}"] = end
    rows = (
        await connection.execute(
            text(
                f"SELECT {signal} FROM service_health_samples "  # noqa: S608 - whitelisted column
                f"WHERE binding_id = :binding AND ({clauses}) AND {signal} IS NOT NULL"
            ),
            params,
        )
    ).all()
    return [float(r[0]) for r in rows]


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
    connection: AsyncConnection,
    incident_id: uuid.UUID,
    at: datetime,
    reason: str | None = None,
) -> bool:
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
    if updated is None:
        return False
    detail: dict[str, Any] = {"source": "rule"}
    if reason is not None:
        detail["reason"] = reason
    await _incident_event(connection, incident_id, "auto_resolved", at, detail)
    return True


async def close_rule_incidents(
    connection: AsyncConnection, rule_id: uuid.UUID, at: datetime, reason: str
) -> int:
    """Resolve every incident this rule still holds open.

    A rule that is disabled or deleted is never evaluated again, so nothing
    else would ever close what it opened: the incident stayed open (and
    paged) for a condition nobody watches any more. The resolution says
    why — `reason` is `rule_disabled` or `rule_deleted` — and goes out as
    the ordinary `auto_resolved` event, so whoever was paged hears it end.
    """
    rows = (
        await connection.execute(
            text(
                "SELECT id FROM incidents WHERE source = 'rule' "
                "AND correlation_key LIKE :prefix AND state IN ('open', 'acknowledged')"
            ),
            {"prefix": f"rule:{rule_id}:%"},
        )
    ).all()
    closed = 0
    for row in rows:
        if await _resolve_incident(connection, uuid.UUID(str(row[0])), at, reason):
            closed += 1
    return closed


async def record_and_evaluate(
    engine: AsyncEngine,
    *,
    binding_id: uuid.UUID,
    catalog: dict[str, Any],
    service_key: str,
    payload: dict[str, Any],
    telemetry: dict[str, float | None] | None = None,
    now: datetime | None = None,
    rule_values: dict[str, float | None] | None = None,
) -> int:
    """Store this cycle's sample and step every covering rule. Returns how
    many rules changed state, for the cycle report.

    *telemetry* holds the log and trace signals (alert_rules.telemetry),
    read this cycle. When the health verdict is old evidence, the metric
    signals are left unknown but the telemetry still counts, observed at
    *now*: the store answered this minute even if the provider did not.
    """
    fresh_telemetry = {k: v for k, v in (telemetry or {}).items() if k in STORED_TELEMETRY}
    filtered = rule_values or {}
    if payload.get("served_from_last_good") or payload.get("cached"):
        fresh = [*fresh_telemetry.values(), *filtered.values()]
        if now is None or not any(v is not None for v in fresh):
            return 0
        at = now
        sample: dict[str, float | None] = dict(fresh_telemetry)
    else:
        parsed = _parse(payload.get("computed_at"))
        if parsed is None:
            return 0
        at = parsed
        sample = {**sample_from_health(payload), **fresh_telemetry}
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
            muted_until, state, breach_since, firing_since, incident_id = row[7:12]
            log_contains, route = row[12:14]
            previous = RuleState(state or "ok", breach_since, firing_since)
            # A rule that filters by text or endpoint has its own value this
            # cycle (alert_rules.telemetry); every other rule reads the sample.
            if log_contains is not None or route is not None:
                value = filtered.get(str(rule_id))
            else:
                value = sample.get(signal)
            if operator in ANOMALY_OPERATORS:
                # Judged against this signal's normal at this time of day;
                # with too little history there is nothing to judge.
                band = normal_band(
                    await _baseline(connection, binding_id, signal, at), signal, float(threshold)
                )
                bound = anomaly_threshold(operator, band)
                if bound is None:
                    value = None
                threshold = bound if bound is not None else threshold
            condition = RuleCondition(
                signal, effective_operator(operator), float(threshold), int(for_seconds)
            )
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
