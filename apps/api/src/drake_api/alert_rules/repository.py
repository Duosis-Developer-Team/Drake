"""Alert rule reads and writes (ADR-0031).

Visibility is `alert.view` at the rule's project. Writing is
`alert_rule.manage` there. A caller who cannot see a project cannot tell its
rules exist: every refusal of a rule outside the caller's reach is the same
404.

Everything the screen draws comes from Drake's own tables:
- the rules;
- their per-binding state;
- their fired/resolved events;
- the per-cycle samples the runner wrote.
No read here queries the metrics provider.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.alert_rules.model import SIGNALS, RuleCondition, replay, validate
from drake_api.catalog.authz import visible_scope_ids
from drake_api.rbac.service import Principal

VIEW_PERMISSION = "alert.view"
MANAGE_PERMISSION = "alert_rule.manage"
HISTORY = timedelta(hours=24)
MAX_RULES = 200
MAX_BINDINGS_PER_PREVIEW = 50
MAX_MUTE_MINUTES = 24 * 60


class RuleError(ValueError):
    """A request the server will not carry out (404/409/422 at the router)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    return list(ids) or [uuid.UUID(int=0)]


_RULE_COLUMNS = """
    r.id, r.project_id, r.environment_id, r.service_id, r.name, r.signal, r.operator,
    r.threshold, r.for_seconds, r.severity, r.enabled, r.muted_until, r.version,
    r.created_at, r.updated_at, p.project_key, e.environment_key, sd.service_key, p.scope_id
"""
_RULE_FROM = """
FROM alert_rules r
JOIN projects p ON p.id = r.project_id
LEFT JOIN environments e ON e.id = r.environment_id
LEFT JOIN service_definitions sd ON sd.id = r.service_id
"""


def _rule_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "project_id": str(row[1]),
        "environment_id": str(row[2]) if row[2] else None,
        "service_id": str(row[3]) if row[3] else None,
        "name": row[4],
        "signal": row[5],
        "operator": row[6],
        "threshold": row[7],
        "for_seconds": row[8],
        "severity": row[9],
        "enabled": row[10],
        "muted_until": row[11].isoformat() if row[11] else None,
        "version": row[12],
        "created_at": row[13].isoformat(),
        "updated_at": row[14].isoformat(),
        "target": {"project": row[15], "environment": row[16], "service": row[17]},
    }


async def _covered_bindings(
    connection: AsyncConnection,
    project_id: uuid.UUID,
    environment_id: uuid.UUID | None,
    service_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    rows = (
        await connection.execute(
            text(
                """
                SELECT b.id, e.environment_key, sd.service_key
                FROM service_workload_bindings b
                JOIN environments e ON e.id = b.environment_id
                JOIN service_definitions sd ON sd.id = b.service_id
                WHERE b.project_id = :project AND b.lifecycle = 'active'
                  AND (CAST(:environment AS uuid) IS NULL OR b.environment_id = :environment)
                  AND (CAST(:service AS uuid) IS NULL OR b.service_id = :service)
                ORDER BY e.environment_key, sd.service_key, b.id
                LIMIT :limit
                """
            ),
            {
                "project": project_id,
                "environment": environment_id,
                "service": service_id,
                "limit": MAX_BINDINGS_PER_PREVIEW,
            },
        )
    ).all()
    return [{"binding_id": str(r[0]), "environment": r[1], "service": r[2]} for r in rows]


def status_of(rule: dict[str, Any], states: list[dict[str, Any]], now: datetime) -> str:
    """One word for the rule as a whole: what the screen leads with."""
    if not rule["enabled"]:
        return "disabled"
    muted_until = rule["muted_until"]
    if muted_until and datetime.fromisoformat(muted_until) > now:
        return "muted"
    if any(s["state"] == "firing" for s in states):
        return "firing"
    if any(s["state"] == "pending" for s in states):
        return "pending"
    if not states or all(s["last_value"] is None for s in states):
        return "no_data"
    return "ok"


async def list_rules(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None,
    now: datetime,
) -> dict[str, Any]:
    visible = await visible_scope_ids(connection, principal, VIEW_PERMISSION)
    manageable = await visible_scope_ids(connection, principal, MANAGE_PERMISSION)
    project_clause = "AND r.project_id = :project" if project_id else ""
    rows = (
        await connection.execute(
            text(
                f"SELECT {_RULE_COLUMNS} {_RULE_FROM} "
                f"WHERE p.scope_id = ANY(:visible) {project_clause} "
                "ORDER BY p.project_key, r.name, r.id LIMIT :limit"
            ),
            {"visible": _sentinel(visible), "project": project_id, "limit": MAX_RULES},
        )
    ).all()
    rules = [{**_rule_row(r), "can_manage": r[18] in manageable} for r in rows]
    if not rules:
        return {"as_of": now.isoformat(), "rules": [], "summary": _summary([])}
    ids = [uuid.UUID(r["id"]) for r in rules]
    state_rows = (
        await connection.execute(
            text(
                """
                SELECT s.rule_id, s.binding_id, s.state, s.firing_since, s.last_value,
                       s.evaluated_at, s.incident_id, e.environment_key, sd.service_key
                FROM alert_rule_states s
                JOIN service_workload_bindings b ON b.id = s.binding_id
                JOIN environments e ON e.id = b.environment_id
                JOIN service_definitions sd ON sd.id = b.service_id
                WHERE s.rule_id = ANY(:ids)
                ORDER BY e.environment_key, sd.service_key
                """
            ),
            {"ids": ids},
        )
    ).all()
    event_rows = (
        await connection.execute(
            text(
                """
                SELECT ev.rule_id, ev.binding_id, ev.kind, ev.at, ev.value, ev.incident_id,
                       e.environment_key, sd.service_key
                FROM alert_rule_events ev
                JOIN service_workload_bindings b ON b.id = ev.binding_id
                JOIN environments e ON e.id = b.environment_id
                JOIN service_definitions sd ON sd.id = b.service_id
                WHERE ev.rule_id = ANY(:ids) AND ev.at >= :since
                ORDER BY ev.at
                """
            ),
            {"ids": ids, "since": now - HISTORY},
        )
    ).all()
    states: dict[str, list[dict[str, Any]]] = {}
    for s in state_rows:
        states.setdefault(str(s[0]), []).append(
            {
                "binding_id": str(s[1]),
                "state": s[2],
                "firing_since": s[3].isoformat() if s[3] else None,
                "last_value": s[4],
                "evaluated_at": s[5].isoformat(),
                "incident_id": str(s[6]) if s[6] else None,
                "environment": s[7],
                "service": s[8],
            }
        )
    episodes: dict[str, list[dict[str, Any]]] = {}
    open_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in event_rows:
        key = (str(ev[0]), str(ev[1]))
        if ev[2] == "fired":
            episode = {
                "binding_id": str(ev[1]),
                "environment": ev[6],
                "service": ev[7],
                "fired_at": ev[3].isoformat(),
                "resolved_at": None,
                "peak": ev[4],
                "incident_id": str(ev[5]) if ev[5] else None,
            }
            open_by_key[key] = episode
            episodes.setdefault(str(ev[0]), []).append(episode)
        elif key in open_by_key:
            open_by_key.pop(key)["resolved_at"] = ev[3].isoformat()
    for rule in rules:
        rule_states = states.get(rule["id"], [])
        rule["states"] = rule_states
        rule["episodes"] = episodes.get(rule["id"], [])
        rule["status"] = status_of(rule, rule_states, now)
    return {"as_of": now.isoformat(), "rules": rules, "summary": _summary(rules)}


def _summary(rules: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total": len(rules),
        "firing": 0,
        "pending": 0,
        "ok": 0,
        "muted": 0,
        "disabled": 0,
        "no_data": 0,
        "fired_24h": 0,
    }
    for rule in rules:
        counts[rule["status"]] += 1
        counts["fired_24h"] += len(rule.get("episodes", []))
    return counts


async def _project_scope(connection: AsyncConnection, project_id: uuid.UUID) -> uuid.UUID | None:
    value = (
        await connection.execute(
            text("SELECT scope_id FROM projects WHERE id = :id"), {"id": project_id}
        )
    ).scalar_one_or_none()
    return value if isinstance(value, uuid.UUID) else None


async def _require(
    connection: AsyncConnection, principal: Principal, project_id: uuid.UUID, permission: str
) -> None:
    scope = await _project_scope(connection, project_id)
    allowed = await visible_scope_ids(connection, principal, permission)
    if scope is None or scope not in allowed:
        raise RuleError("not_found", "not found")


async def _validate_target(
    connection: AsyncConnection,
    project_id: uuid.UUID,
    environment_id: uuid.UUID | None,
    service_id: uuid.UUID | None,
) -> None:
    if service_id is not None and environment_id is None:
        raise RuleError("service_needs_environment", "a service rule names its environment")
    if environment_id is not None:
        owner = (
            await connection.execute(
                text("SELECT project_id FROM environments WHERE id = :id"), {"id": environment_id}
            )
        ).scalar_one_or_none()
        if owner != project_id:
            raise RuleError("not_found", "not found")
    if service_id is not None:
        owner = (
            await connection.execute(
                text("SELECT project_id FROM service_definitions WHERE id = :id"),
                {"id": service_id},
            )
        ).scalar_one_or_none()
        if owner != project_id:
            raise RuleError("not_found", "not found")


def _check(values: dict[str, Any]) -> None:
    try:
        validate(
            values["signal"],
            values["operator"],
            float(values["threshold"]),
            int(values["for_seconds"]),
            values["severity"],
        )
    except ValueError as error:
        raise RuleError("invalid_rule", str(error)) from error


async def create_rule(
    connection: AsyncConnection, principal: Principal, values: dict[str, Any], actor: uuid.UUID
) -> dict[str, Any]:
    _check(values)
    await _require(connection, principal, values["project_id"], MANAGE_PERMISSION)
    await _validate_target(
        connection, values["project_id"], values["environment_id"], values["service_id"]
    )
    row = (
        await connection.execute(
            text(
                """
                INSERT INTO alert_rules
                    (project_id, environment_id, service_id, name, signal, operator, threshold,
                     for_seconds, severity, created_by, updated_by)
                VALUES (:project_id, :environment_id, :service_id, :name, :signal, :operator,
                        :threshold, :for_seconds, :severity, :actor, :actor)
                RETURNING id, version
                """
            ),
            {**values, "actor": actor},
        )
    ).first()
    assert row is not None
    return {"id": str(row[0]), "version": row[1]}


async def _load(connection: AsyncConnection, rule_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await connection.execute(
            text(f"SELECT {_RULE_COLUMNS} {_RULE_FROM} WHERE r.id = :id"),
            {"id": rule_id},
        )
    ).first()
    return _rule_row(row) if row else None


async def get_rule(
    connection: AsyncConnection, principal: Principal, rule_id: uuid.UUID, permission: str
) -> dict[str, Any]:
    rule = await _load(connection, rule_id)
    if rule is None:
        raise RuleError("not_found", "not found")
    await _require(connection, principal, uuid.UUID(rule["project_id"]), permission)
    return rule


async def update_rule(
    connection: AsyncConnection,
    principal: Principal,
    rule_id: uuid.UUID,
    values: dict[str, Any],
    expected_version: int,
    actor: uuid.UUID,
) -> dict[str, Any]:
    current = await get_rule(connection, principal, rule_id, MANAGE_PERMISSION)
    merged = {**current, **values}
    _check(merged)
    row = (
        await connection.execute(
            text(
                """
                UPDATE alert_rules
                SET name = :name, signal = :signal, operator = :operator,
                    threshold = :threshold, for_seconds = :for_seconds, severity = :severity,
                    enabled = :enabled, version = version + 1, updated_by = :actor,
                    updated_at = now()
                WHERE id = :id AND version = :version
                RETURNING version
                """
            ),
            {
                "id": rule_id,
                "version": expected_version,
                "actor": actor,
                **{
                    k: merged[k]
                    for k in (
                        "name",
                        "signal",
                        "operator",
                        "threshold",
                        "for_seconds",
                        "severity",
                        "enabled",
                    )
                },
            },
        )
    ).first()
    if row is None:
        raise RuleError("version_conflict", "the rule changed since it was read")
    # A changed condition starts from a clean slate: a half-held breach of
    # the old threshold says nothing about the new one.
    if any(merged[k] != current[k] for k in ("signal", "operator", "threshold", "for_seconds")):
        await connection.execute(
            text(
                "UPDATE alert_rule_states SET state = 'ok', breach_since = NULL "
                "WHERE rule_id = :id AND state = 'pending'"
            ),
            {"id": rule_id},
        )
    return {"id": str(rule_id), "version": row[0]}


async def mute_rule(
    connection: AsyncConnection,
    principal: Principal,
    rule_id: uuid.UUID,
    minutes: int,
    now: datetime,
    actor: uuid.UUID,
) -> dict[str, Any]:
    if not 0 <= minutes <= MAX_MUTE_MINUTES:
        raise RuleError("invalid_mute", "mute for 0 to 1440 minutes")
    await get_rule(connection, principal, rule_id, MANAGE_PERMISSION)
    until = now + timedelta(minutes=minutes) if minutes else None
    await connection.execute(
        text(
            "UPDATE alert_rules SET muted_until = :until, updated_by = :actor, updated_at = now(), "
            "version = version + 1 WHERE id = :id"
        ),
        {"id": rule_id, "until": until, "actor": actor},
    )
    return {"id": str(rule_id), "muted_until": until.isoformat() if until else None}


async def delete_rule(
    connection: AsyncConnection, principal: Principal, rule_id: uuid.UUID
) -> None:
    await get_rule(connection, principal, rule_id, MANAGE_PERMISSION)
    await connection.execute(text("DELETE FROM alert_rules WHERE id = :id"), {"id": rule_id})


async def _samples(
    connection: AsyncConnection, binding_ids: list[uuid.UUID], signal: str, since: datetime
) -> dict[str, list[tuple[datetime, float | None]]]:
    if signal not in SIGNALS:
        raise RuleError("invalid_rule", "unknown signal")
    rows = (
        await connection.execute(
            text(
                f"SELECT binding_id, observed_at, {signal} FROM service_health_samples "  # noqa: S608
                "WHERE binding_id = ANY(:ids) AND observed_at >= :since ORDER BY observed_at"
            ),
            {"ids": binding_ids, "since": since},
        )
    ).all()
    out: dict[str, list[tuple[datetime, float | None]]] = {}
    for binding_id, at, value in rows:
        out.setdefault(str(binding_id), []).append((at, value))
    return out


def _series(points: list[tuple[datetime, float | None]]) -> list[dict[str, Any]]:
    return [{"at": at.isoformat(), "value": value} for at, value in points]


async def rule_history(
    connection: AsyncConnection, principal: Principal, rule_id: uuid.UUID, now: datetime
) -> dict[str, Any]:
    rule = await get_rule(connection, principal, rule_id, VIEW_PERMISSION)
    bindings = await _covered_bindings(
        connection,
        uuid.UUID(rule["project_id"]),
        uuid.UUID(rule["environment_id"]) if rule["environment_id"] else None,
        uuid.UUID(rule["service_id"]) if rule["service_id"] else None,
    )
    samples = await _samples(
        connection, [uuid.UUID(b["binding_id"]) for b in bindings], rule["signal"], now - HISTORY
    )
    return {
        "rule_id": rule["id"],
        "signal": rule["signal"],
        "since": (now - HISTORY).isoformat(),
        "bindings": [{**b, "series": _series(samples.get(b["binding_id"], []))} for b in bindings],
    }


async def preview(
    connection: AsyncConnection, principal: Principal, values: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """How often a draft rule would have fired over the last day, judged by
    the same `step` the runner uses, on the same samples."""
    _check(values)
    await _require(connection, principal, values["project_id"], VIEW_PERMISSION)
    await _validate_target(
        connection, values["project_id"], values["environment_id"], values["service_id"]
    )
    bindings = await _covered_bindings(
        connection, values["project_id"], values["environment_id"], values["service_id"]
    )
    samples = await _samples(
        connection, [uuid.UUID(b["binding_id"]) for b in bindings], values["signal"], now - HISTORY
    )

    def episodes_at(threshold: float) -> list[dict[str, Any]]:
        condition = RuleCondition(
            values["signal"], values["operator"], threshold, int(values["for_seconds"])
        )
        found = []
        for binding in bindings:
            for ep in replay(condition, samples.get(binding["binding_id"], [])):
                end = ep.resolved_at or now
                found.append(
                    {
                        "binding_id": binding["binding_id"],
                        "environment": binding["environment"],
                        "service": binding["service"],
                        "fired_at": ep.fired_at.isoformat(),
                        "resolved_at": ep.resolved_at.isoformat() if ep.resolved_at else None,
                        "minutes": round((end - ep.fired_at).total_seconds() / 60),
                        "peak": ep.peak,
                    }
                )
        return sorted(found, key=lambda e: e["fired_at"])

    threshold = float(values["threshold"])
    found = episodes_at(threshold)
    suggestion = None
    for factor in (1.25, 1.5, 0.75):
        candidate = threshold * factor
        if values["signal"] in ("error_ratio", "cpu_utilization", "memory_utilization"):
            candidate = min(candidate, 1.0)
        count = len(episodes_at(candidate))
        if count != len(found):
            suggestion = {"threshold": candidate, "count": count}
            break
    busiest = max(
        bindings, key=lambda b: sum(e["binding_id"] == b["binding_id"] for e in found), default=None
    )
    covered_hours = 0.0
    if samples:
        first = min(points[0][0] for points in samples.values() if points)
        covered_hours = round((now - first).total_seconds() / 3600, 1)
    return {
        "count": len(found),
        "minutes": sum(e["minutes"] for e in found),
        "episodes": found,
        "suggestion": suggestion,
        "bindings": len(bindings),
        "history_hours": min(covered_hours, 24.0),
        "series": (
            {**busiest, "series": _series(samples.get(busiest["binding_id"], []))}
            if busiest
            else None
        ),
    }


async def options(connection: AsyncConnection, principal: Principal) -> dict[str, Any]:
    """Projects, their environments and services that the caller may write
    rules for, and the signals a rule can watch."""
    manageable = await visible_scope_ids(connection, principal, MANAGE_PERMISSION)
    rows = (
        await connection.execute(
            text(
                """
                SELECT p.id, p.project_key, e.id, e.environment_key, sd.id, sd.service_key
                FROM projects p
                JOIN service_workload_bindings b ON b.project_id = p.id AND b.lifecycle = 'active'
                JOIN environments e ON e.id = b.environment_id
                JOIN service_definitions sd ON sd.id = b.service_id
                WHERE p.scope_id = ANY(:scopes)
                ORDER BY p.project_key, e.environment_key, sd.service_key
                """
            ),
            {"scopes": _sentinel(manageable)},
        )
    ).all()
    projects: dict[str, dict[str, Any]] = {}
    for pid, pkey, eid, ekey, sid, skey in rows:
        project = projects.setdefault(str(pid), {"id": str(pid), "key": pkey, "environments": {}})
        env = project["environments"].setdefault(
            str(eid), {"id": str(eid), "key": ekey, "services": []}
        )
        if not any(s["id"] == str(sid) for s in env["services"]):
            env["services"].append({"id": str(sid), "key": skey})
    return {
        "signals": list(SIGNALS),
        "projects": [
            {**p, "environments": list(p["environments"].values())} for p in projects.values()
        ],
    }
