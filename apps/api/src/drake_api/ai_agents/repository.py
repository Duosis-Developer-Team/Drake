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


MANAGE = "agent.manage"


async def _manage_visibility(
    connection: AsyncConnection, principal: Principal
) -> tuple[set[uuid.UUID], bool]:
    """(projects whose agents this caller may govern, may govern agents
    outside the catalog)."""
    manage = set(await visible_scope_ids(connection, principal, MANAGE))
    if not manage:
        return set(), False
    projects = set(await visible_scope_ids(connection, principal, "project.view"))
    scopes = list(manage & projects) or [uuid.UUID(int=0)]
    ids = {
        uuid.UUID(str(r[0]))
        for r in (
            await connection.execute(
                text("SELECT id FROM projects WHERE scope_id = ANY(:s)"), {"s": scopes}
            )
        ).all()
    }
    root = (
        await connection.execute(
            text(
                "SELECT id FROM scopes WHERE scope_type = 'organization' AND external_ref = 'root'"
            )
        )
    ).scalar_one_or_none()
    return ids, root is not None and root in manage


async def may_manage(
    connection: AsyncConnection, principal: Principal, project_id: uuid.UUID | None
) -> bool:
    projects, unbound = await _manage_visibility(connection, principal)
    if project_id is None:
        return unbound
    return uuid.UUID(str(project_id)) in projects


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
    p.id, p.project_key, p.display_name, e.id, e.environment_key, g.binding_id,
    g.purpose, o.id, o.display_name, g.lifecycle_changed_at, c.display_name
"""

# Who owns the agent and who last changed its lifecycle (phase 5).
_PEOPLE = """
    LEFT JOIN identities o ON o.id = g.owner_identity_id
    LEFT JOIN identities c ON c.id = g.lifecycle_changed_by
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
        "purpose": row[18],
        "owner": {"id": str(row[19]), "name": row[20] or None} if row[19] is not None else None,
        "lifecycle_changed_at": row[21].isoformat() if row[21] is not None else None,
        "lifecycle_changed_by": row[22],
        # Shadow AI: nobody approved it, and it runs outside the catalog or
        # is known only from the wire.
        "shadow": row[4] == "observed" and (row[12] is None or row[2] == "observed"),
        # last_seen_at is the end of the last hour with usage, so only an hour
        # that began after the retirement counts as being seen again.
        "seen_after_retirement": (
            row[4] == "retired" and row[21] is not None and row[11] > row[21] + timedelta(hours=1)
        ),
    }


# Cost (phase 4): each hourly row joined to the price its (provider, model)
# matched, in force in that hour. Input includes cached reads and cache
# writes (OpenTelemetry), output includes reasoning; each subset is costed
# at its own price when the list has one. USD.
_PRICED = """
    LEFT JOIN model_price_matches pm ON pm.provider = r.provider AND pm.model = r.model
    LEFT JOIN model_prices mp ON mp.model_key = pm.model_key AND mp.valid_from <= r.hour
         AND (mp.valid_to IS NULL OR mp.valid_to > r.hour)
"""
_COST = """(
    greatest(r.input_tokens - r.cache_read_tokens - r.cache_write_tokens, 0) * mp.input_usd
    + r.cache_read_tokens * coalesce(mp.cache_read_usd, mp.input_usd)
    + r.cache_write_tokens * coalesce(mp.cache_write_usd, mp.input_usd)
    + greatest(r.output_tokens - r.reasoning_tokens, 0) * mp.output_usd
    + r.reasoning_tokens * coalesce(mp.reasoning_usd, mp.output_usd)
) / 1000000.0"""
_COST_GROUPS = {"agent_id", "hour", "provider", "model"}


async def _costs(
    connection: AsyncConnection, ids: list[uuid.UUID], since: datetime, group: tuple[str, ...]
) -> dict[tuple[Any, ...], tuple[float, int]]:
    """(cost in USD, tokens with no price) per group of agent usage."""
    if not ids:
        return {}
    if not set(group) <= _COST_GROUPS:
        raise ValueError("unknown cost grouping")
    cols = ", ".join(f"r.{c}" for c in group)
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT {cols}, coalesce(sum({_COST}), 0),
                       coalesce(sum(CASE WHEN mp.id IS NULL AND r.model <> ''
                                         THEN r.input_tokens + r.output_tokens ELSE 0 END), 0)
                FROM agent_rollups r {_PRICED}
                WHERE r.agent_id = ANY(:ids) AND r.hour >= :since
                GROUP BY {cols}
                """  # noqa: S608 - fixed fragments; group columns checked above
            ),
            {"ids": ids, "since": since},
        )
    ).all()
    n = len(group)
    return {tuple(row[:n]): (float(row[n]), int(row[n + 1])) for row in rows}


async def agent_costs(
    connection: AsyncConnection, ids: list[uuid.UUID], since: datetime
) -> dict[uuid.UUID, float]:
    """USD per agent since a time, at the prices in force each hour."""
    return {
        uuid.UUID(str(key[0])): cost
        for key, (cost, _) in (await _costs(connection, ids, since, ("agent_id",))).items()
    }


def _usd(value: float) -> float:
    return round(value, 6)


async def list_agents(
    connection: AsyncConnection,
    principal: Principal,
    *,
    window: str = "24h",
    include_retired: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    span = WINDOWS.get(window, WINDOWS["24h"])
    since = moment - span
    projects, unbound = await _visibility(connection, principal)
    governed, governed_unbound = await _manage_visibility(connection, principal)
    params = {"projects": projects, "unbound": unbound, "since": since, "retired": include_retired}
    agents = (
        await connection.execute(
            text(
                f"""
                SELECT {_AGENT_COLUMNS}
                FROM agents g
                LEFT JOIN projects p ON p.id = g.project_id
                LEFT JOIN environments e ON e.id = g.environment_id
                {_PEOPLE}
                WHERE {_clause()} AND (:retired OR g.lifecycle != 'retired')
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
            "cost_usd": 0.0,
            "unpriced_tokens": 0,
            "guardrails_firing": 0,
            "can_manage": (
                uuid.UUID(str(r[12])) in governed if r[12] is not None else governed_unbound
            ),
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
        {"t": h.isoformat(), "calls": 0, "errors": 0, "tokens": 0, "cost_usd": 0.0} for h in hours
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
        for agent_id, firing in (
            await connection.execute(
                text(
                    """
                    SELECT g.agent_id, count(*) FROM agent_guardrails g
                    JOIN agent_guardrail_states s ON s.guardrail_id = g.id
                    WHERE g.agent_id = ANY(:ids) AND g.enabled AND s.state = 'firing'
                    GROUP BY g.agent_id
                    """
                ),
                {"ids": ids},
            )
        ).all():
            items[str(agent_id)]["guardrails_firing"] = int(firing)
        for (agent_id,), (cost, unpriced) in (
            await _costs(connection, ids, since, ("agent_id",))
        ).items():
            items[str(agent_id)]["cost_usd"] = _usd(cost)
            items[str(agent_id)]["unpriced_tokens"] = unpriced
        model_costs = await _costs(connection, ids, since, ("agent_id", "provider", "model"))
        for (agent_id, provider, model), (cost, _) in model_costs.items():
            for m in items[str(agent_id)]["models"]:
                if m["model"] == model and (m["provider"] or "") == (provider or ""):
                    m["cost_usd"] = _usd(cost)
        for (hour,), (cost, _) in (await _costs(connection, ids, since, ("hour",))).items():
            i = index.get(hour)
            if i is not None:
                total_series[i]["cost_usd"] = _usd(cost)
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
        "cost_usd": _usd(sum(i["cost_usd"] for i in rows)),
        "unpriced_tokens": sum(i["unpriced_tokens"] for i in rows),
        "guardrails_firing": sum(1 for i in rows if i["guardrails_firing"]),
        "governance": {
            "approved": sum(1 for i in rows if i["lifecycle"] == "approved"),
            "review": sum(1 for i in rows if i["lifecycle"] == "observed"),
            "retired": sum(1 for i in rows if i["lifecycle"] == "retired"),
            "shadow": sum(1 for i in rows if i["shadow"]),
            "no_owner": sum(1 for i in rows if i["owner"] is None and i["lifecycle"] != "retired"),
            "seen_after_retirement": sum(1 for i in rows if i["seen_after_retirement"]),
        },
        "can_manage": bool(governed) or governed_unbound,
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
                {_PEOPLE}
                WHERE g.id = :id AND {_clause()}
                """  # noqa: S608 - fixed fragments
            ),
            {"id": agent_id, "projects": projects, "unbound": unbound},
        )
    ).first()
    if row is None:
        return None
    agent = _agent(row, moment)
    agent["can_manage"] = await may_manage(connection, principal, row[12])
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
            "cost_usd": 0.0,
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
    for (hour,), (cost, _) in (await _costs(connection, [agent_id], since, ("hour",))).items():
        i = index.get(hour)
        if i is not None:
            series[i]["cost_usd"] = _usd(cost)
    whole = await _costs(connection, [agent_id], since, ("agent_id",))
    cost, unpriced = next(iter(whole.values()), (0.0, 0))
    agent["cost_usd"] = _usd(cost)
    agent["unpriced_tokens"] = unpriced
    by_model = await _costs(connection, [agent_id], since, ("provider", "model"))
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
            "cost_usd": _usd(by_model[(r[0], r[1])][0]) if (r[0], r[1]) in by_model else None,
            "priced": (r[0], r[1]) in by_model and by_model[(r[0], r[1])][1] == 0,
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


async def list_mcp_servers(
    connection: AsyncConnection,
    principal: Principal,
    *,
    window: str = "24h",
    now: datetime | None = None,
) -> dict[str, Any]:
    """The MCP servers agents called in the window, for the servers this
    caller may see (the same project rule as agents): what each serves,
    who calls it, how it went, hour by hour."""
    moment = now or datetime.now(UTC)
    span = WINDOWS.get(window, WINDOWS["24h"])
    since = moment - span
    hours = _hours(span, moment)
    index = {h: i for i, h in enumerate(hours)}
    projects, unbound = await _visibility(connection, principal)
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT s.id, s.service_name, s.cluster_ref, s.namespace, s.workload,
                       s.first_seen_at, s.last_seen_at, p.id, p.project_key, p.display_name,
                       e.environment_key,
                       coalesce(sum(r.calls), 0), coalesce(sum(r.errors), 0),
                       coalesce(sum(r.duration_ms), 0)
                FROM mcp_servers s
                LEFT JOIN projects p ON p.id = s.project_id
                LEFT JOIN environments e ON e.id = s.environment_id
                LEFT JOIN mcp_server_rollups r ON r.server_id = s.id AND r.hour >= :since
                WHERE {_clause("s")} AND s.last_seen_at >= :since
                GROUP BY s.id, p.id, p.project_key, p.display_name, e.environment_key
                ORDER BY 12 DESC, s.service_name
                LIMIT 50
                """  # noqa: S608 - fixed fragments
            ),
            {"projects": projects, "unbound": unbound, "since": since},
        )
    ).all()
    servers: dict[str, dict[str, Any]] = {}
    for r in rows:
        calls = int(r[11])
        servers[str(r[0])] = {
            "id": str(r[0]),
            "service_name": r[1] or None,
            "cluster_ref": r[2] or None,
            "namespace": r[3] or None,
            "workload": r[4] or None,
            "first_seen_at": r[5].isoformat(),
            "last_seen_at": r[6].isoformat(),
            "project": {"id": str(r[7]), "key": r[8], "name": r[9]} if r[7] else None,
            "environment": r[10],
            "calls": calls,
            "errors": int(r[12]),
            "avg_ms": round(int(r[13]) / calls) if calls else None,
            "tools": [],
            "clients": [],
            "methods": [],
            "series": [{"t": h.isoformat(), "calls": 0, "errors": 0} for h in hours],
        }
    if not servers:
        return {"window": window, "servers": []}
    ids = [uuid.UUID(k) for k in servers]
    params = {"ids": ids, "since": since}
    for column, key in (("tool_name", "tools"), ("client", "clients"), ("method", "methods")):
        for sid, name, calls, errors, duration in (
            await connection.execute(
                text(
                    f"""
                    SELECT server_id, {column}, sum(calls), sum(errors), sum(duration_ms)
                    FROM mcp_server_rollups
                    WHERE server_id = ANY(:ids) AND hour >= :since
                    GROUP BY server_id, {column}
                    ORDER BY 3 DESC
                    """  # noqa: S608 - column from a fixed tuple
                ),
                params,
            )
        ).all():
            servers[str(sid)][key].append(
                {
                    "name": name or None,
                    "calls": int(calls),
                    "errors": int(errors),
                    "avg_ms": round(int(duration) / int(calls)) if int(calls) else None,
                }
            )
    for sid, hour, calls, errors in (
        await connection.execute(
            text(
                """
                SELECT server_id, hour, sum(calls), sum(errors)
                FROM mcp_server_rollups
                WHERE server_id = ANY(:ids) AND hour >= :since
                GROUP BY server_id, hour
                """
            ),
            params,
        )
    ).all():
        i = index.get(hour.astimezone(UTC).replace(minute=0, second=0, microsecond=0))
        if i is not None:
            servers[str(sid)]["series"][i]["calls"] += int(calls)
            servers[str(sid)]["series"][i]["errors"] += int(errors)
    await _mcp_users(connection, servers, params)
    for server in servers.values():
        server["tools"] = server["tools"][:20]
        server["clients"] = server["clients"][:10]
    return {"window": window, "servers": list(servers.values())}


# The name a person was last seen with, across every row kept for them.
_LATEST_NAME = (
    "(array_agg(user_name ORDER BY last_seen_at DESC) FILTER (WHERE user_name IS NOT NULL))[1]"
)


def _person(row: Any) -> dict[str, Any]:
    calls = int(row.calls)
    return {
        "key": row.caller_key,
        "id": row.user_id,
        "name": row.name,
        "calls": calls,
        "errors": int(row.errors),
        "last_seen_at": row.last_seen.isoformat(),
    }


async def _mcp_users(
    connection: AsyncConnection, servers: dict[str, dict[str, Any]], params: dict[str, Any]
) -> None:
    """Who used each server, and each of its tools and clients: the people
    the calls name (`user.id` / `user.name`), with calls and when last
    seen. `attributed_calls` says how many calls named someone at all, so
    the rest reads as unknown — never as nobody."""
    for server in servers.values():
        server["users"] = []
        server["attributed_calls"] = 0
        for item in server["tools"] + server["clients"]:
            item["users"] = []
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT server_id, caller_key, max(user_id) AS user_id,
                       {_LATEST_NAME} AS name,
                       sum(calls) AS calls, sum(errors) AS errors,
                       max(last_seen_at) AS last_seen,
                       (array_agg(DISTINCT client) FILTER (WHERE client <> '')) AS clients
                FROM mcp_server_callers
                WHERE server_id = ANY(:ids) AND hour >= :since
                GROUP BY server_id, caller_key
                ORDER BY calls DESC, last_seen DESC
                """  # noqa: S608 - fixed fragments
            ),
            params,
        )
    ).all()
    for row in rows:
        server = servers[str(row.server_id)]
        server["attributed_calls"] += int(row.calls)
        if len(server["users"]) < 20:
            server["users"].append({**_person(row), "clients": sorted(row.clients or [])[:5]})
    for column, key in (("tool_name", "tools"), ("client", "clients")):
        by_item: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in (
            await connection.execute(
                text(
                    f"""
                    SELECT server_id, {column} AS item, caller_key, max(user_id) AS user_id,
                           {_LATEST_NAME} AS name,
                           sum(calls) AS calls, sum(errors) AS errors,
                           max(last_seen_at) AS last_seen
                    FROM mcp_server_callers
                    WHERE server_id = ANY(:ids) AND hour >= :since
                    GROUP BY server_id, {column}, caller_key
                    ORDER BY calls DESC, last_seen DESC
                    """  # noqa: S608 - column from a fixed tuple
                ),
                params,
            )
        ).all():
            people = by_item.setdefault((str(row.server_id), row.item or ""), [])
            if len(people) < 5:
                people.append(_person(row))
        for sid, server in servers.items():
            for item in server[key]:
                item["users"] = by_item.get((sid, item["name"] or ""), [])


async def price_status(connection: AsyncConnection, principal: Principal) -> dict[str, Any]:
    """Where costs come from: the last price fetch, the last good one, how
    many models are priced, and the models this caller's agents use that
    have no price."""

    def fetch(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "fetched_at": row[0].isoformat(),
            "status": row[1],
            "source": row[2],
            "models": int(row[3]),
            "changed": int(row[4]),
        }

    columns = "SELECT fetched_at, status, source, models, changed FROM model_price_fetches"
    last = (await connection.execute(text(f"{columns} ORDER BY fetched_at DESC LIMIT 1"))).first()
    good = (
        await connection.execute(
            text(f"{columns} WHERE status IN ('live', 'fallback') ORDER BY fetched_at DESC LIMIT 1")
        )
    ).first()
    priced = (
        await connection.execute(text("SELECT count(*) FROM model_prices WHERE valid_to IS NULL"))
    ).scalar_one()
    projects, unbound = await _visibility(connection, principal)
    unmatched = (
        await connection.execute(
            text(
                f"""
                SELECT DISTINCT r.provider, r.model
                FROM agent_rollups r
                JOIN agents g ON g.id = r.agent_id
                JOIN model_price_matches pm ON pm.provider = r.provider AND pm.model = r.model
                WHERE pm.model_key IS NULL AND {_clause()}
                ORDER BY r.model LIMIT 50
                """  # noqa: S608 - fixed fragments
            ),
            {"projects": projects, "unbound": unbound},
        )
    ).all()
    return {
        "currency": "USD",
        "basis": "list prices per token; no discounts, batch or priority tiers",
        "last_fetch": fetch(last),
        "last_good_fetch": fetch(good),
        "priced_models": int(priced),
        "unpriced": [{"provider": r[0] or None, "model": r[1]} for r in unmatched],
    }


async def prices_for(
    connection: AsyncConnection, pairs: set[tuple[str, str]], at: datetime
) -> dict[tuple[str, str], dict[str, float | None]]:
    """The price in force at `at` for each (provider, model), USD per
    million tokens — for the pairs already matched."""
    if not pairs:
        return {}
    providers = [p for p, _ in pairs]
    models = [m for _, m in pairs]
    rows = (
        await connection.execute(
            text(
                """
                SELECT pm.provider, pm.model, mp.input_usd, mp.output_usd, mp.cache_read_usd,
                       mp.cache_write_usd, mp.reasoning_usd
                FROM unnest(CAST(:providers AS text[]), CAST(:models AS text[])) AS q(p, m)
                JOIN model_price_matches pm ON pm.provider = q.p AND pm.model = q.m
                JOIN model_prices mp ON mp.model_key = pm.model_key AND mp.valid_from <= :at
                     AND (mp.valid_to IS NULL OR mp.valid_to > :at)
                """
            ),
            {"providers": providers, "models": models, "at": at},
        )
    ).all()
    return {
        (str(r[0]), str(r[1])): {
            "input": float(r[2]),
            "output": float(r[3]),
            "cache_read": float(r[4]) if r[4] is not None else None,
            "cache_write": float(r[5]) if r[5] is not None else None,
            "reasoning": float(r[6]) if r[6] is not None else None,
        }
        for r in rows
    }


def token_cost(
    price: dict[str, float | None], *, inp: int, out: int, cached: int, reasoning: int
) -> float:
    """USD for one call's tokens at a price (per million tokens)."""
    base_in = float(price["input"] or 0)
    base_out = float(price["output"] or 0)
    cache = price["cache_read"] if price["cache_read"] is not None else base_in
    think = price["reasoning"] if price["reasoning"] is not None else base_out
    return (
        max(inp - cached, 0) * base_in
        + cached * float(cache)
        + max(out - reasoning, 0) * base_out
        + reasoning * float(think)
    ) / 1_000_000


LIFECYCLES = ("observed", "approved", "retired")


async def set_governance(
    connection: AsyncConnection,
    principal: Principal,
    agent_id: uuid.UUID,
    *,
    actor: uuid.UUID,
    lifecycle: str | None = None,
    owner: str | None = None,
    purpose: str | None = None,
    clear_purpose: bool = False,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Change what the organisation decided about an agent. `owner` is
    "me" or "none". None when the caller may not see or govern it."""
    project = (
        await connection.execute(
            text("SELECT project_id, lifecycle FROM agents WHERE id = :id"), {"id": agent_id}
        )
    ).first()
    if project is None or await agent_for_runs(connection, principal, agent_id) is None:
        return None
    if not await may_manage(connection, principal, project[0]):
        return None
    changes: dict[str, Any] = {}
    if lifecycle is not None:
        if lifecycle not in LIFECYCLES:
            raise ValueError("lifecycle")
        if lifecycle != project[1]:
            changes.update(
                lifecycle=lifecycle,
                lifecycle_changed_at=now or datetime.now(UTC),
                lifecycle_changed_by=actor,
            )
    if owner is not None:
        if owner not in ("me", "none"):
            raise ValueError("owner")
        changes["owner_identity_id"] = actor if owner == "me" else None
    if clear_purpose:
        changes["purpose"] = None
    elif purpose is not None:
        cleaned = " ".join(purpose.split())
        if len(cleaned) > 500:
            raise ValueError("purpose")
        changes["purpose"] = cleaned or None
    if changes:
        assignments = ", ".join(f"{k} = :{k}" for k in changes)
        await connection.execute(
            text(f"UPDATE agents SET {assignments}, updated_at = now() WHERE id = :id"),  # noqa: S608
            {**changes, "id": agent_id},
        )
    return {"changed": sorted(changes)}
