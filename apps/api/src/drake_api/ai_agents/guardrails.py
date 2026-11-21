"""Guardrails on AI agents: lines an agent should not cross, watched.

Four kinds, set per agent (at most one of each):

- `failure_ratio`  — share of its calls that failed over the last two hours,
  judged only once it made enough calls to mean something;
- `avg_run_seconds` — average run time over the last two hours;
- `cost_day`       — US dollars over the last 24 hours, at list prices;
- `silence_hours`  — hours since it was last seen.

Usage is hourly (`agent_rollups`), so a two-hour window always holds one
complete hour. Every few minutes the evaluator measures each enabled
guardrail and moves it between `ok`, `firing` and `no_data`; each change is
an event. A critical guardrail that starts firing on an agent bound to a
project environment opens an incident (source `agent`), which the existing
notification policies route; it resolves when the guardrail is `ok` again.
Warnings stay on the agent's page.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.ai_agents import repository as repo
from drake_api.catalog.authz import visible_scope_ids
from drake_api.rbac.service import Principal

logger = logging.getLogger(__name__)

KINDS = ("failure_ratio", "avg_run_seconds", "cost_day", "silence_hours")
SEVERITIES = ("warning", "critical")
MANAGE = "agent.manage"
WINDOW = timedelta(hours=2)
MIN_CALLS = 20
LEASE_KEY = "agents:guardrails:cycle"
_LIMITS = {
    "failure_ratio": (Decimal("0.0001"), Decimal("1")),
    "avg_run_seconds": (Decimal("0.01"), Decimal("86400")),
    "cost_day": (Decimal("0.01"), Decimal("1000000")),
    "silence_hours": (Decimal("0.5"), Decimal("720")),
}
_LABELS = {
    "failure_ratio": "failure rate",
    "avg_run_seconds": "average run",
    "cost_day": "cost per day",
    "silence_hours": "silent",
}


class GuardrailError(ValueError):
    """A request to set a guardrail that is not allowed or not valid."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def check(kind: str, threshold: Decimal, severity: str) -> None:
    if kind not in KINDS:
        raise GuardrailError("unknown_kind", 404)
    if severity not in SEVERITIES:
        raise GuardrailError("invalid_severity")
    low, high = _LIMITS[kind]
    if not low <= threshold <= high:
        raise GuardrailError("threshold_out_of_range")


# --- measuring ---------------------------------------------------------------


def judge(kind: str, value: float | None, threshold: float) -> str:
    if value is None:
        return "no_data"
    return "firing" if value > threshold else "ok"


async def measure(
    connection: AsyncConnection, agent_ids: list[uuid.UUID], now: datetime
) -> dict[uuid.UUID, dict[str, float | None]]:
    """Each agent's value for every kind (None where it cannot be judged)."""
    if not agent_ids:
        return {}
    since = (now - WINDOW).replace(minute=0, second=0, microsecond=0)
    usage = {
        r[0]: r
        for r in (
            await connection.execute(
                text(
                    """
                    SELECT agent_id, sum(invocations + inference_calls + tool_calls),
                           sum(errors), sum(duration_ms), sum(invocations)
                    FROM agent_rollups
                    WHERE agent_id = ANY(:ids) AND hour >= :since
                    GROUP BY agent_id
                    """
                ),
                {"ids": agent_ids, "since": since},
            )
        ).all()
    }
    seen = {
        r[0]: r[1]
        for r in (
            await connection.execute(
                text("SELECT id, last_seen_at FROM agents WHERE id = ANY(:ids)"),
                {"ids": agent_ids},
            )
        ).all()
    }
    costs = await repo.agent_costs(connection, agent_ids, now - timedelta(hours=24))
    out: dict[uuid.UUID, dict[str, float | None]] = {}
    for agent_id in agent_ids:
        row = usage.get(agent_id)
        calls = int(row[1]) if row else 0
        errors = int(row[2]) if row else 0
        duration_ms = int(row[3]) if row else 0
        runs = int(row[4]) if row else 0
        last = seen.get(agent_id)
        out[agent_id] = {
            "failure_ratio": errors / calls if calls >= MIN_CALLS else None,
            "avg_run_seconds": duration_ms / runs / 1000 if runs else None,
            "cost_day": costs.get(agent_id, 0.0),
            "silence_hours": (now - last).total_seconds() / 3600 if last else None,
        }
    return out


# --- incidents -----------------------------------------------------------------


def _title(agent: str, kind: str, value: float, threshold: float) -> str:
    if kind == "failure_ratio":
        shown = f"{value:.1%} failed (limit {threshold:.1%})"
    elif kind == "avg_run_seconds":
        shown = f"runs take {value:.1f}s on average (limit {threshold:g}s)"
    elif kind == "cost_day":
        shown = f"${value:,.2f} in 24h (budget ${threshold:,.2f})"
    else:
        shown = f"silent for {value:.1f}h (limit {threshold:g}h)"
    return f"Agent {agent}: {shown}"[:200]


async def _event(
    connection: AsyncConnection,
    incident_id: uuid.UUID,
    kind: str,
    at: datetime,
    detail: dict[str, Any],
) -> None:
    await connection.execute(
        text(
            "INSERT INTO incident_events (incident_id, event_type, occurred_at, detail) "
            "VALUES (:incident, :type, :at, CAST(:detail AS jsonb))"
        ),
        {"incident": incident_id, "type": kind, "at": at, "detail": json.dumps(detail)},
    )


async def _open_incident(
    connection: AsyncConnection, guard: dict[str, Any], value: float, at: datetime
) -> uuid.UUID | None:
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
                    VALUES ('agent', :key, NULL, NULL, :project, :environment, NULL,
                            'open', 'critical', 'P1', :title, 'agent_guardrail',
                            CAST(:reasons AS jsonb), 1, :at, :at)
                    RETURNING id
                    """
                ),
                {
                    "key": f"agent-guardrail:{guard['id']}",
                    "project": guard["project_id"],
                    "environment": guard["environment_id"],
                    "title": _title(guard["agent"], guard["kind"], value, guard["threshold"]),
                    "reasons": json.dumps([f"agent_guardrail:{guard['kind']}"]),
                    "at": at,
                },
            )
        ).first()
    except IntegrityError:
        # Already open for this guardrail (partial unique correlation key).
        await savepoint.rollback()
        return None
    await savepoint.commit()
    if row is None:
        return None
    incident_id = uuid.UUID(str(row[0]))
    await _event(
        connection,
        incident_id,
        "opened",
        at,
        {
            "source": "agent",
            "agent_id": str(guard["agent_id"]),
            "guardrail": guard["kind"],
            "value": value,
        },
    )
    return incident_id


async def _resolve_incident(
    connection: AsyncConnection, incident_id: uuid.UUID, at: datetime
) -> None:
    done = (
        await connection.execute(
            text(
                """
                UPDATE incidents
                SET state = 'resolved', resolved_at = :at, resolution_source = 'agent_recovered',
                    version = version + 1, updated_at = now()
                WHERE id = :id AND state IN ('open', 'acknowledged')
                RETURNING id
                """
            ),
            {"id": incident_id, "at": at},
        )
    ).first()
    if done is not None:
        await _event(connection, incident_id, "auto_resolved", at, {"source": "agent"})


# --- the cycle -------------------------------------------------------------------


@dataclass
class GuardReport:
    lease_acquired: bool = False
    evaluated: int = 0
    fired: int = 0
    resolved: int = 0


async def evaluate(engine: AsyncEngine, *, now: datetime) -> GuardReport:
    report = GuardReport(lease_acquired=True)
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT g.id, g.agent_id, g.kind, g.threshold, g.severity, a.display_name,
                           a.project_id, a.environment_id, s.state, s.incident_id
                    FROM agent_guardrails g
                    JOIN agents a ON a.id = g.agent_id
                    LEFT JOIN agent_guardrail_states s ON s.guardrail_id = g.id
                    WHERE g.enabled AND a.lifecycle <> 'retired'
                    ORDER BY g.id
                    LIMIT 5000
                    """
                )
            )
        ).all()
        values = await measure(connection, sorted({r[1] for r in rows}), now)
        for r in rows:
            guard = {
                "id": r[0],
                "agent_id": r[1],
                "kind": r[2],
                "threshold": float(r[3]),
                "severity": r[4],
                "agent": r[5],
                "project_id": r[6],
                "environment_id": r[7],
            }
            previous, incident_id = r[8], r[9]
            value = values.get(r[1], {}).get(r[2])
            state = judge(r[2], value, guard["threshold"])
            report.evaluated += 1
            if state != previous:
                if state == "firing":
                    report.fired += 1
                    await connection.execute(
                        text(
                            "INSERT INTO agent_guardrail_events (guardrail_id, event, value, at) "
                            "VALUES (:g, 'fired', :v, :at)"
                        ),
                        {"g": r[0], "v": value, "at": now},
                    )
                    if (
                        guard["severity"] == "critical"
                        and guard["project_id"] is not None
                        and guard["environment_id"] is not None
                        and value is not None
                    ):
                        incident_id = await _open_incident(connection, guard, value, now)
                elif previous == "firing":
                    report.resolved += 1
                    await connection.execute(
                        text(
                            "INSERT INTO agent_guardrail_events (guardrail_id, event, value, at) "
                            "VALUES (:g, 'resolved', :v, :at)"
                        ),
                        {"g": r[0], "v": value, "at": now},
                    )
                    if state == "ok" and incident_id is not None:
                        await _resolve_incident(connection, incident_id, now)
                        incident_id = None
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_guardrail_states
                        (guardrail_id, state, value, since, evaluated_at, incident_id)
                    VALUES (:g, :state, :value, :now, :now, :incident)
                    ON CONFLICT (guardrail_id) DO UPDATE SET
                        state = EXCLUDED.state, value = EXCLUDED.value,
                        since = CASE WHEN agent_guardrail_states.state = EXCLUDED.state
                                     THEN agent_guardrail_states.since ELSE EXCLUDED.since END,
                        evaluated_at = EXCLUDED.evaluated_at,
                        incident_id = EXCLUDED.incident_id
                    """
                ),
                {"g": r[0], "state": state, "value": value, "now": now, "incident": incident_id},
            )
    return report


_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


async def run_once(
    engine: AsyncEngine, redis: aioredis.Redis, *, now: datetime | None = None
) -> GuardReport:
    token = uuid.uuid4().hex
    if not await redis.set(LEASE_KEY, token, nx=True, ex=240):
        return GuardReport()
    try:
        return await evaluate(engine, now=now or datetime.now(UTC))
    finally:
        await redis.eval(_RELEASE, 1, LEASE_KEY, token)


class GuardrailEvaluator:
    def __init__(
        self, engine: AsyncEngine, redis: aioredis.Redis, *, interval_seconds: float = 300.0
    ) -> None:
        self._engine = engine
        self._redis = redis
        self._interval = max(60.0, interval_seconds)
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            try:
                await run_once(self._engine, self._redis)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("agent guardrails: a cycle failed")
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="agent-guardrails")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


# --- reading and setting -----------------------------------------------------------


async def _may_manage(
    connection: AsyncConnection, principal: Principal, project_id: uuid.UUID | None
) -> bool:
    scopes = set(await visible_scope_ids(connection, principal, MANAGE))
    if not scopes:
        return False
    if project_id is None:
        root = (
            await connection.execute(
                text(
                    "SELECT id FROM scopes "
                    "WHERE scope_type = 'organization' AND external_ref = 'root'"
                )
            )
        ).scalar_one_or_none()
        return root is not None and root in scopes
    projects = set(await visible_scope_ids(connection, principal, "project.view"))
    scope = (
        await connection.execute(
            text("SELECT scope_id FROM projects WHERE id = :p"), {"p": project_id}
        )
    ).scalar_one_or_none()
    return scope is not None and scope in scopes and scope in projects


async def read(
    connection: AsyncConnection, principal: Principal, agent_id: uuid.UUID
) -> dict[str, Any] | None:
    """Every kind for one agent: what is set and what it last found. None
    when the agent is not visible."""
    agent = await repo.agent_for_runs(connection, principal, agent_id)
    if agent is None:
        return None
    project = (
        await connection.execute(
            text("SELECT project_id, environment_id FROM agents WHERE id = :id"), {"id": agent_id}
        )
    ).one()
    rows = {
        r[0]: r
        for r in (
            await connection.execute(
                text(
                    """
                    SELECT g.kind, g.threshold, g.severity, g.enabled, g.version, g.updated_at,
                           s.state, s.value, s.since, s.evaluated_at, s.incident_id
                    FROM agent_guardrails g
                    LEFT JOIN agent_guardrail_states s ON s.guardrail_id = g.id
                    WHERE g.agent_id = :id
                    """
                ),
                {"id": agent_id},
            )
        ).all()
    }
    items = []
    for kind in KINDS:
        r = rows.get(kind)
        items.append(
            {
                "kind": kind,
                "set": r is not None,
                "threshold": float(r[1]) if r else None,
                "severity": r[2] if r else None,
                "enabled": bool(r[3]) if r else False,
                "version": int(r[4]) if r else None,
                "state": (r[6] or "pending") if r else None,
                "value": float(r[7]) if r and r[7] is not None else None,
                "since": r[8].isoformat() if r and r[8] else None,
                "evaluated_at": r[9].isoformat() if r and r[9] else None,
                "incident_id": str(r[10]) if r and r[10] else None,
            }
        )
    return {
        "agent_id": str(agent_id),
        "can_manage": await _may_manage(connection, principal, project[0]),
        "pages": project[0] is not None and project[1] is not None,
        "min_calls": MIN_CALLS,
        "items": items,
    }


async def put(
    connection: AsyncConnection,
    principal: Principal,
    agent_id: uuid.UUID,
    kind: str,
    *,
    threshold: Decimal,
    severity: str,
    enabled: bool,
    actor: uuid.UUID,
) -> None:
    agent = await repo.agent_for_runs(connection, principal, agent_id)
    if agent is None:
        raise GuardrailError("not_found", 404)
    project_id = (
        await connection.execute(
            text("SELECT project_id FROM agents WHERE id = :id"), {"id": agent_id}
        )
    ).scalar_one_or_none()
    if not await _may_manage(connection, principal, project_id):
        raise GuardrailError("not_found", 404)
    check(kind, threshold, severity)
    await connection.execute(
        text(
            """
            INSERT INTO agent_guardrails (agent_id, kind, threshold, severity, enabled, created_by)
            VALUES (:agent, :kind, :threshold, :severity, :enabled, :actor)
            ON CONFLICT (agent_id, kind) DO UPDATE SET
                threshold = EXCLUDED.threshold, severity = EXCLUDED.severity,
                enabled = EXCLUDED.enabled, updated_at = now(),
                version = agent_guardrails.version + 1
            """
        ),
        {
            "agent": agent_id,
            "kind": kind,
            "threshold": threshold,
            "severity": severity,
            "enabled": enabled,
            "actor": actor,
        },
    )


async def remove(
    connection: AsyncConnection, principal: Principal, agent_id: uuid.UUID, kind: str, now: datetime
) -> bool:
    agent = await repo.agent_for_runs(connection, principal, agent_id)
    if agent is None or kind not in KINDS:
        raise GuardrailError("not_found", 404)
    project_id = (
        await connection.execute(
            text("SELECT project_id FROM agents WHERE id = :id"), {"id": agent_id}
        )
    ).scalar_one_or_none()
    if not await _may_manage(connection, principal, project_id):
        raise GuardrailError("not_found", 404)
    row = (
        await connection.execute(
            text(
                """
                SELECT s.incident_id FROM agent_guardrails g
                LEFT JOIN agent_guardrail_states s ON s.guardrail_id = g.id
                WHERE g.agent_id = :agent AND g.kind = :kind
                """
            ),
            {"agent": agent_id, "kind": kind},
        )
    ).first()
    if row is None:
        return False
    # Removing the guardrail ends what it opened.
    if row[0] is not None:
        await _resolve_incident(connection, row[0], now)
    await connection.execute(
        text("DELETE FROM agent_guardrails WHERE agent_id = :agent AND kind = :kind"),
        {"agent": agent_id, "kind": kind},
    )
    return True


def label(kind: str) -> str:
    return _LABELS[kind]
