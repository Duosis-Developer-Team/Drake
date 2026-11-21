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
            "server": r[4] or None,
            "calls": int(r[1]),
            "errors": int(r[2]),
            "avg_ms": round(int(r[3]) / int(r[1])) if int(r[1]) else None,
        }
        for r in (
            await connection.execute(
                text(
                    """
                    SELECT tool_name, sum(calls), sum(errors), sum(duration_ms), server
                    FROM agent_tool_rollups WHERE agent_id = :id AND hour >= :since
                    GROUP BY tool_name, server ORDER BY 2 DESC LIMIT 50
                    """
                ),
                {"id": agent_id, "since": since},
            )
        ).all()
    ]
    agent["window"] = window if window in WINDOWS else "7d"
    return agent


async def agent_for_runs(
    connection: AsyncConnection, principal: Principal, agent_id: uuid.UUID
) -> dict[str, Any] | None:
    """What the run views need to find an agent's spans — if the caller may
    see the agent at all."""
    projects, unbound = await _visibility(connection, principal)
    row = (
        await connection.execute(
            text(
                f"""
                SELECT g.id, g.display_name, g.confidence, g.identity_key, g.agent_external_id,
                       g.agent_name, g.service_name, g.cluster_ref, g.namespace, g.workload
                FROM agents g
                WHERE g.id = :id AND {_clause()}
                """  # noqa: S608 - fixed fragments
            ),
            {"id": agent_id, "projects": projects, "unbound": unbound},
        )
    ).first()
    if row is None:
        return None
    keys = (
        "id",
        "display_name",
        "confidence",
        "identity_key",
        "agent_external_id",
        "agent_name",
        "service_name",
        "cluster_ref",
        "namespace",
        "workload",
    )
    agent = dict(zip(keys, row, strict=True))
    agent["id"] = str(agent["id"])
    return agent


async def visible_agents_by_key(
    connection: AsyncConnection, principal: Principal, keys: list[str]
) -> dict[str, dict[str, Any]]:
    """Identity key → {id, display_name} for the agents among `keys` this
    caller may see; the others stay anonymous in a run."""
    if not keys:
        return {}
    projects, unbound = await _visibility(connection, principal)
    rows = (
        await connection.execute(
            text(
                f"SELECT g.identity_key, g.id, g.display_name FROM agents g "  # noqa: S608
                f"WHERE g.identity_key = ANY(:keys) AND {_clause()}"
            ),
            {"keys": keys, "projects": projects, "unbound": unbound},
        )
    ).all()
    return {str(r[0]): {"id": str(r[1]), "display_name": r[2]} for r in rows}


MAX_TOPOLOGY_AGENTS = 60
MAX_RESOURCES = 40


async def topology(
    connection: AsyncConnection,
    principal: Principal,
    *,
    window: str = "24h",
    focus: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Agents and what they reach — models, tools (by the server they run
    on) and other agents — over a window, for the agents this caller may
    see. With `focus`, one agent and its direct neighbours only; None when
    that agent is not visible.

    An agent → agent link is shown only when both ends are visible: a
    caller never learns of an agent outside their scope through a link.
    """
    moment = now or datetime.now(UTC)
    since = moment - WINDOWS.get(window, WINDOWS["24h"])
    projects, unbound = await _visibility(connection, principal)
    params: dict[str, Any] = {"projects": projects, "unbound": unbound, "since": since}

    edge_rows = (
        await connection.execute(
            text(
                f"""
                SELECT e.caller_id, e.callee_id, sum(e.calls), sum(e.errors)
                FROM agent_edges e
                JOIN agents g ON g.id = e.caller_id
                JOIN agents c ON c.id = e.callee_id
                WHERE e.hour >= :since AND {_clause("g")} AND {_clause("c")}
                GROUP BY e.caller_id, e.callee_id
                """  # noqa: S608 - fixed fragments
            ),
            params,
        )
    ).all()
    agent_ids: set[uuid.UUID] | None = None
    if focus is not None:
        visible = (
            await connection.execute(
                text(f"SELECT 1 FROM agents g WHERE g.id = :id AND {_clause()}"),  # noqa: S608
                {**params, "id": focus},
            )
        ).first()
        if visible is None:
            return None
        agent_ids = {focus}
        for caller, callee, *_ in edge_rows:
            if focus in (caller, callee):
                agent_ids |= {caller, callee}

    usage = (
        await connection.execute(
            text(
                f"""
                SELECT g.id, g.display_name, g.confidence, g.ingest, p.project_key,
                       coalesce(sum(r.invocations), 0), coalesce(sum(r.inference_calls), 0),
                       coalesce(sum(r.tool_calls), 0), coalesce(sum(r.errors), 0),
                       max(g.last_seen_at)
                FROM agents g
                LEFT JOIN projects p ON p.id = g.project_id
                LEFT JOIN agent_rollups r ON r.agent_id = g.id AND r.hour >= :since
                WHERE {_clause()}
                  AND (CAST(:ids AS uuid[]) IS NULL AND g.last_seen_at >= :since
                       OR g.id = ANY(CAST(:ids AS uuid[])))
                GROUP BY g.id, g.display_name, g.confidence, g.ingest, p.project_key
                ORDER BY coalesce(sum(r.invocations), 0) + coalesce(sum(r.inference_calls), 0)
                         + coalesce(sum(r.tool_calls), 0) DESC, g.display_name
                LIMIT {MAX_TOPOLOGY_AGENTS}
                """  # noqa: S608 - fixed fragments
            ),
            {**params, "ids": list(agent_ids) if agent_ids is not None else None},
        )
    ).all()
    shown = {r[0] for r in usage}
    nodes: list[dict[str, Any]] = [
        {
            "id": f"agent:{r[0]}",
            "kind": "agent",
            "agent_id": str(r[0]),
            "label": r[1],
            "confidence": r[2],
            "ingest": r[3],
            "project": r[4],
            "calls": int(r[5]) + int(r[6]) + int(r[7]),
            "errors": int(r[8]),
        }
        for r in usage
    ]
    edges: list[dict[str, Any]] = [
        {
            "source": f"agent:{caller}",
            "target": f"agent:{callee}",
            "kind": "agent",
            "calls": int(calls),
            "errors": int(errors),
        }
        for caller, callee, calls, errors in edge_rows
        if caller in shown and callee in shown
    ]
    if not shown:
        return {"window": window, "nodes": [], "edges": []}

    ids = list(shown)
    models = (
        await connection.execute(
            text(
                """
                SELECT agent_id, provider, model, sum(inference_calls), sum(errors),
                       sum(input_tokens), sum(output_tokens)
                FROM agent_rollups
                WHERE agent_id = ANY(:ids) AND hour >= :since AND inference_calls > 0
                GROUP BY agent_id, provider, model
                """
            ),
            {"ids": ids, "since": since},
        )
    ).all()
    tools = (
        await connection.execute(
            text(
                """
                SELECT agent_id, tool_name, server, sum(calls), sum(errors), sum(duration_ms)
                FROM agent_tool_rollups
                WHERE agent_id = ANY(:ids) AND hour >= :since
                GROUP BY agent_id, tool_name, server
                """
            ),
            {"ids": ids, "since": since},
        )
    ).all()

    model_nodes: dict[str, dict[str, Any]] = {}
    for agent, provider, model, calls, errors, tin, tout in models:
        key = f"model:{provider}/{model}"
        node = model_nodes.setdefault(
            key,
            {
                "id": key,
                "kind": "model",
                "label": model or provider or "unknown",
                "provider": provider or None,
                "calls": 0,
                "errors": 0,
                "tokens": 0,
            },
        )
        node["calls"] += int(calls)
        node["errors"] += int(errors)
        node["tokens"] += int(tin) + int(tout)
        edges.append(
            {
                "source": f"agent:{agent}",
                "target": key,
                "kind": "model",
                "calls": int(calls),
                "errors": int(errors),
                "tokens": int(tin) + int(tout),
            }
        )
    tool_nodes: dict[str, dict[str, Any]] = {}
    for agent, tool, server, calls, errors, duration in tools:
        key = f"tool:{server}/{tool}"
        node = tool_nodes.setdefault(
            key,
            {
                "id": key,
                "kind": "tool",
                "label": tool,
                "server": server or None,
                "calls": 0,
                "errors": 0,
                "duration_ms": 0,
            },
        )
        node["calls"] += int(calls)
        node["errors"] += int(errors)
        node["duration_ms"] += int(duration)
        edges.append(
            {
                "source": f"agent:{agent}",
                "target": key,
                "kind": "tool",
                "calls": int(calls),
                "errors": int(errors),
                "avg_ms": round(int(duration) / int(calls)) if int(calls) else None,
            }
        )
    resources = sorted(model_nodes.values(), key=lambda n: -n["calls"])[:MAX_RESOURCES]
    resources += sorted(tool_nodes.values(), key=lambda n: -n["calls"])[:MAX_RESOURCES]
    kept = {n["id"] for n in nodes} | {n["id"] for n in resources}
    for node in tool_nodes.values():
        duration = node.pop("duration_ms")
        node["avg_ms"] = round(duration / node["calls"]) if node["calls"] else None
    return {
        "window": window,
        "focus": str(focus) if focus else None,
        "nodes": nodes + resources,
        "edges": [e for e in edges if e["source"] in kept and e["target"] in kept],
    }
