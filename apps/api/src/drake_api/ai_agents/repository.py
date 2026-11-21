"""Reading the agent inventory (Agent Visibility, phase 2).

Visibility follows the catalog: an agent bound to a project is seen by those
holding `agent.view` and `project.view` on that project; an agent outside the
catalog (no workload binding — another cluster, an external system, a laptop)
only by those holding `agent.view` at the organisation root. Nothing tells a
caller that an agent they cannot see exists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.catalog.authz import visible_scope_ids
from drake_api.rbac.service import Principal

PERMISSION = "agent.view"
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
NEW_FOR = timedelta(days=7)


async def _visibility(
    connection: AsyncConnection, principal: Principal
) -> tuple[list[uuid.UUID], bool]:
    """(visible project ids, may see agents outside the catalog)."""
    agents = set(await visible_scope_ids(connection, principal, PERMISSION))
    projects = set(await visible_scope_ids(connection, principal, "project.view"))
    scopes = list(agents & projects) or [uuid.UUID(int=0)]
    project_ids = [
        uuid.UUID(str(r[0]))
        for r in (
            await connection.execute(
                text("SELECT id FROM projects WHERE scope_id = ANY(:s)"), {"s": scopes}
            )
        ).all()
    ]
    root = (
        await connection.execute(
            text(
                "SELECT id FROM scopes WHERE scope_type = 'organization' AND external_ref = 'root'"
            )
        )
    ).scalar_one_or_none()
    return project_ids, root is not None and root in agents


def _clause(alias: str = "g") -> str:
    return f"(({alias}.project_id = ANY(:projects)) OR ({alias}.project_id IS NULL AND :unbound))"


def _hours(window: timedelta, now: datetime) -> list[datetime]:
    end = now.replace(minute=0, second=0, microsecond=0)
    count = int(window.total_seconds() // 3600)
    return [end - timedelta(hours=count - 1 - i) for i in range(count)]


_TOTALS = """
    coalesce(sum(r.invocations), 0), coalesce(sum(r.inference_calls), 0),
    coalesce(sum(r.tool_calls), 0), coalesce(sum(r.errors), 0),
    coalesce(sum(r.input_tokens), 0), coalesce(sum(r.output_tokens), 0),
    coalesce(sum(r.cache_read_tokens), 0), coalesce(sum(r.cache_write_tokens), 0),
    coalesce(sum(r.reasoning_tokens), 0), coalesce(sum(r.duration_ms), 0)
"""


def _totals(row: Any, offset: int = 0) -> dict[str, int]:
    keys = (
        "invocations",
        "inference_calls",
        "tool_calls",
        "errors",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "duration_ms",
    )
    return {k: int(row[offset + i] or 0) for i, k in enumerate(keys)}


_AGENT_COLUMNS = """
    g.id, g.display_name, g.confidence, g.ingest, g.lifecycle, g.service_name, g.namespace,
    g.workload, g.cluster_ref, g.agent_external_id, g.first_seen_at, g.last_seen_at,
    p.id, p.project_key, p.display_name, e.id, e.environment_key, g.binding_id
"""


def _agent(row: Any, now: datetime) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "display_name": row[1],
        "confidence": row[2],
        "ingest": row[3],
        "lifecycle": row[4],
        "service_name": row[5] or None,
        "namespace": row[6] or None,
        "workload": row[7] or None,
        "cluster_ref": row[8] or None,
        "agent_external_id": row[9],
        "first_seen_at": row[10].isoformat(),
        "last_seen_at": row[11].isoformat(),
        "new": (now - row[10]) <= NEW_FOR,
        "project": (
            {"id": str(row[12]), "key": row[13], "name": row[14]} if row[12] is not None else None
        ),
        "environment": ({"id": str(row[15]), "key": row[16]} if row[15] is not None else None),
        "binding_id": str(row[17]) if row[17] is not None else None,
    }


async def list_agents(
    connection: AsyncConnection,
    principal: Principal,
    *,
    window: str = "24h",
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    span = WINDOWS.get(window, WINDOWS["24h"])
    since = moment - span
    projects, unbound = await _visibility(connection, principal)
    params = {"projects": projects, "unbound": unbound, "since": since}
    agents = (
        await connection.execute(
            text(
                f"""
                SELECT {_AGENT_COLUMNS}
                FROM agents g
                LEFT JOIN projects p ON p.id = g.project_id
                LEFT JOIN environments e ON e.id = g.environment_id
                WHERE {_clause()} AND g.lifecycle != 'retired'
                ORDER BY g.last_seen_at DESC
                LIMIT 500
                """  # noqa: S608 - fixed fragments
            ),
            params,
        )
    ).all()
    items = {
        str(r[0]): {
            **_agent(r, moment),
            "totals": _totals([0] * 10),
            "providers": [],
            "models": [],
            "series": [],
        }
        for r in agents
    }
    ids = [uuid.UUID(k) for k in items]
    hours = _hours(span, moment)
    index = {h: i for i, h in enumerate(hours)}
    total_series: list[dict[str, Any]] = [
        {"t": h.isoformat(), "calls": 0, "errors": 0, "tokens": 0} for h in hours
    ]
    if ids:
        for row in (
            await connection.execute(
                text(
                    f"SELECT r.agent_id, {_TOTALS} FROM agent_rollups r "  # noqa: S608
                    "WHERE r.agent_id = ANY(:ids) AND r.hour >= :since GROUP BY r.agent_id"
                ),
                {"ids": ids, "since": since},
            )
        ).all():
            items[str(row[0])]["totals"] = _totals(row, 1)
        for row in (
            await connection.execute(
                text(
                    """
                    SELECT r.agent_id, r.provider, r.model,
                           sum(r.inference_calls + r.invocations),
                           sum(r.input_tokens + r.output_tokens)
                    FROM agent_rollups r
                    WHERE r.agent_id = ANY(:ids) AND r.hour >= :since
                    GROUP BY r.agent_id, r.provider, r.model
                    """
                ),
                {"ids": ids, "since": since},
            )
        ).all():
            item = items[str(row[0])]
            if row[2]:
                item["models"].append(
                    {
                        "model": row[2],
                        "provider": row[1] or None,
                        "calls": int(row[3]),
                        "tokens": int(row[4]),
                    }
                )
            if row[1] and row[1] not in {p["provider"] for p in item["providers"]}:
                item["providers"].append({"provider": row[1], "calls": 0})
            for p in item["providers"]:
                if p["provider"] == row[1]:
                    p["calls"] += int(row[3])
        series: dict[str, list[dict[str, Any]]] = {
            k: [{"t": h.isoformat(), "calls": 0, "errors": 0} for h in hours] for k in items
        }
        for row in (
            await connection.execute(
                text(
                    """
                    SELECT r.agent_id, r.hour,
                           sum(r.inference_calls + r.invocations + r.tool_calls),
                           sum(r.errors), sum(r.input_tokens + r.output_tokens)
                    FROM agent_rollups r
                    WHERE r.agent_id = ANY(:ids) AND r.hour >= :since
                    GROUP BY r.agent_id, r.hour
                    """
                ),
                {"ids": ids, "since": since},
            )
        ).all():
            i = index.get(row[1])
            if i is None:
                continue
            series[str(row[0])][i]["calls"] += int(row[2])
            series[str(row[0])][i]["errors"] += int(row[3])
            total_series[i]["calls"] += int(row[2])
            total_series[i]["errors"] += int(row[3])
            total_series[i]["tokens"] += int(row[4])
        for k, s in series.items():
            items[k]["series"] = s
            items[k]["models"].sort(key=lambda m: -m["calls"])
            items[k]["providers"].sort(key=lambda p: -p["calls"])
    rows = list(items.values())
    providers: dict[str, int] = {}
    for item in rows:
        for p in item["providers"]:
            providers[p["provider"]] = providers.get(p["provider"], 0) + p["calls"]
    summary = {
        "agents": len(rows),
        "active": sum(1 for i in rows if any(s["calls"] for s in i["series"])),
        "new": sum(1 for i in rows if i["new"]),
        "unowned": sum(1 for i in rows if i["lifecycle"] == "observed"),
        "outside_catalog": sum(1 for i in rows if i["project"] is None),
        "by_confidence": {
            c: sum(1 for i in rows if i["confidence"] == c)
            for c in ("stable", "named", "service", "observed")
        },
        "providers": [
            {"provider": k, "calls": v} for k, v in sorted(providers.items(), key=lambda x: -x[1])
        ],
        "totals": {
            k: sum(i["totals"][k] for i in rows)
            for k in (
                "invocations",
                "inference_calls",
                "tool_calls",
                "errors",
                "input_tokens",
                "output_tokens",
            )
        },
        "series": total_series,
    }
    return {"window": window if window in WINDOWS else "24h", "items": rows, "summary": summary}


async def get_agent(
    connection: AsyncConnection,
    principal: Principal,
    agent_id: uuid.UUID,
    *,
    window: str = "7d",
    now: datetime | None = None,
) -> dict[str, Any] | None:
    moment = now or datetime.now(UTC)
    span = WINDOWS.get(window, WINDOWS["7d"])
    since = moment - span
    projects, unbound = await _visibility(connection, principal)
    row = (
        await connection.execute(
            text(
                f"""
                SELECT {_AGENT_COLUMNS}
                FROM agents g
                LEFT JOIN projects p ON p.id = g.project_id
                LEFT JOIN environments e ON e.id = g.environment_id
                WHERE g.id = :id AND {_clause()}
                """  # noqa: S608 - fixed fragments
            ),
            {"id": agent_id, "projects": projects, "unbound": unbound},
        )
    ).first()
    if row is None:
        return None
    agent = _agent(row, moment)
    totals = (
        await connection.execute(
            text(
                f"SELECT {_TOTALS} FROM agent_rollups r "  # noqa: S608 - fixed fragment
                "WHERE r.agent_id = :id AND r.hour >= :since"
            ),
            {"id": agent_id, "since": since},
        )
    ).one()
    agent["totals"] = _totals(totals)
    hours = _hours(span, moment)
    index = {h: i for i, h in enumerate(hours)}
    series = [
        {
            "t": h.isoformat(),
            "invocations": 0,
            "inference_calls": 0,
            "tool_calls": 0,
            "errors": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        for h in hours
    ]
    for r in (
        await connection.execute(
            text(
                """
                SELECT hour, sum(invocations), sum(inference_calls), sum(tool_calls), sum(errors),
                       sum(input_tokens), sum(output_tokens)
                FROM agent_rollups WHERE agent_id = :id AND hour >= :since GROUP BY hour
                """
            ),
            {"id": agent_id, "since": since},
        )
    ).all():
        i = index.get(r[0])
        if i is not None:
            series[i].update(
                invocations=int(r[1]),
                inference_calls=int(r[2]),
                tool_calls=int(r[3]),
                errors=int(r[4]),
                input_tokens=int(r[5]),
                output_tokens=int(r[6]),
            )
    agent["series"] = series
    agent["models"] = [
        {
            "provider": r[0] or None,
            "model": r[1] or None,
            "calls": int(r[2]),
            "input_tokens": int(r[3]),
            "output_tokens": int(r[4]),
            "cache_read_tokens": int(r[5]),
            "reasoning_tokens": int(r[6]),
            "errors": int(r[7]),
        }
        for r in (
            await connection.execute(
                text(
                    """
                    SELECT provider, model, sum(inference_calls + invocations), sum(input_tokens),
                           sum(output_tokens), sum(cache_read_tokens), sum(reasoning_tokens),
                           sum(errors)
                    FROM agent_rollups WHERE agent_id = :id AND hour >= :since
                    GROUP BY provider, model ORDER BY 3 DESC
                    """
                ),
                {"id": agent_id, "since": since},
            )
        ).all()
    ]
    agent["tools"] = [
        {
            "tool": r[0],
            "calls": int(r[1]),
            "errors": int(r[2]),
            "avg_ms": round(int(r[3]) / int(r[1])) if int(r[1]) else None,
        }
        for r in (
            await connection.execute(
                text(
                    """
                    SELECT tool_name, sum(calls), sum(errors), sum(duration_ms)
                    FROM agent_tool_rollups WHERE agent_id = :id AND hour >= :since
                    GROUP BY tool_name ORDER BY 2 DESC LIMIT 50
                    """
                ),
                {"id": agent_id, "since": since},
            )
        ).all()
    ]
    agent["window"] = window if window in WINDOWS else "7d"
    return agent
