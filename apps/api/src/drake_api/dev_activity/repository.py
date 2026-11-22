"""Reading development activity: the map, a slice of commits, aliases.

Visibility follows the catalog exactly as the rest of Agent Visibility does:
a project's commits and usage are seen by those holding `agent.view` and
`project.view` on that project. Aliases are organisation-wide, so changing
them takes `agent.manage` at the organisation root.

One person is one node: a GitHub login, a second login, and a Claude Code
identity collapse into one when an alias says so, or when Claude Code's
`tracesis.person` attribute names the login. A Claude identity nobody has
linked stays its own node, labelled as unlinked — never guessed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.ai_agents.repository import MANAGE, _visibility
from drake_api.catalog.authz import visible_scope_ids
from drake_api.rbac.service import Principal

WINDOWS = {"7d": 7, "30d": 30, "90d": 90}
AGENT_ORDER = ("claude_code", "copilot", "codex", "cursor", "devin", "other_ai", "none")


async def may_manage_aliases(connection: AsyncConnection, principal: Principal) -> bool:
    manage = set(await visible_scope_ids(connection, principal, MANAGE))
    root = (
        await connection.execute(
            text(
                "SELECT id FROM scopes WHERE scope_type = 'organization' AND external_ref = 'root'"
            )
        )
    ).scalar_one_or_none()
    return root is not None and root in manage


class People:
    """Identity → person, from aliases and Claude Code's `tracesis.person`."""

    def __init__(self, rows: list[Any]) -> None:
        self.by_alias: dict[tuple[str, str], tuple[str, str | None]] = {
            (str(r[0]), str(r[1]).lower()): (str(r[2]), r[3]) for r in rows
        }

    def of_login(self, login: str | None, name: str) -> str:
        if login:
            hit = self.by_alias.get(("github", login.lower()))
            return hit[0] if hit else login.lower()
        return f"name:{name.strip().lower()}" if name.strip() else "unknown"

    def of_claude(self, identity: str, hint: str | None) -> tuple[str, bool]:
        """(person key, linked)."""
        hit = self.by_alias.get(("claude", identity.lower()))
        if hit:
            return hit[0], True
        if hint:
            return self.of_login(hint, ""), True
        return f"claude:{identity[:12].lower()}", False

    def display(self, key: str) -> str | None:
        for (_, _), (person, shown) in self.by_alias.items():
            if person == key and shown:
                return str(shown)
        return None


async def _people(connection: AsyncConnection) -> People:
    rows = (
        await connection.execute(
            text("SELECT kind, alias, person_key, display_name FROM dev_person_aliases")
        )
    ).all()
    return People(list(rows))


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


async def dev_map(
    connection: AsyncConnection,
    principal: Principal,
    *,
    window: str,
    now: datetime | None = None,
    reads_commits: bool,
    reads_usage: bool,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    days = WINDOWS[window]
    since = moment - timedelta(days=days)
    first_day = (moment - timedelta(days=days - 1)).date()
    project_ids, _ = await _visibility(connection, principal)
    people = await _people(connection)

    projects = {
        uuid.UUID(str(r[0])): {
            "id": str(r[0]),
            "key": str(r[1]),
            "name": str(r[2]),
            "repository": f"{r[3]}/{r[4]}",
            "url": f"https://github.com/{r[3]}/{r[4]}",
        }
        for r in (
            await connection.execute(
                text(
                    "SELECT id, project_key, display_name, repo_owner, repo_name FROM projects "
                    "WHERE id = ANY(:ids) AND lifecycle = 'active'"
                ),
                {"ids": project_ids or [uuid.UUID(int=0)]},
            )
        ).all()
    }
    tracked = (
        await connection.execute(
            text(
                """
                SELECT count(*) FROM projects p
                JOIN github_repositories r
                  ON lower(r.full_name) = lower(p.repo_owner || '/' || p.repo_name)
                WHERE p.id = ANY(:ids) AND r.access_state = 'accessible'
                """
            ),
            {"ids": project_ids or [uuid.UUID(int=0)]},
        )
    ).scalar_one()

    commits = (
        await connection.execute(
            text(
                """
                SELECT project_id, author_login, author_name, agent, additions, deletions,
                       services, unmapped_files, authored_at
                FROM dev_commits
                WHERE project_id = ANY(:ids) AND authored_at >= :since
                ORDER BY authored_at
                """
            ),
            {"ids": list(projects) or [uuid.UUID(int=0)], "since": since},
        )
    ).all()

    person: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}

    def node(key: str) -> dict[str, Any]:
        if key not in person:
            person[key] = {
                "key": key,
                "name": "",
                "logins": set(),
                "linked": True,
                "commits": 0,
                "additions": 0,
                "deletions": 0,
                "agents": defaultdict(int),
                "projects": set(),
                "days": set(),
                "last_at": None,
                "series": [0] * days,
                "telemetry": None,
                "bot": False,
            }
        return person[key]

    edges: dict[tuple[str, uuid.UUID, str], dict[str, Any]] = {}
    service_edges: dict[tuple[uuid.UUID, str | None], int] = defaultdict(int)
    project_stats: dict[uuid.UUID, dict[str, Any]] = {
        pid: {"commits": 0, "agent_commits": 0, "people": set(), "services": set()}
        for pid in projects
    }
    for pid, login, name, agent, adds, dels, services, unmapped, at in commits:
        pid = uuid.UUID(str(pid))
        key = people.of_login(login, name or "")
        p = node(key)
        if login:
            p["logins"].add(login)
            p["bot"] = p["bot"] or login.endswith("[bot]")
        if name:
            names[key] = name
        p["commits"] += 1
        p["additions"] += int(adds or 0)
        p["deletions"] += int(dels or 0)
        p["agents"][agent] += 1
        p["projects"].add(str(pid))
        p["days"].add(at.date())
        p["last_at"] = at
        index = (at.date() - first_day).days
        if 0 <= index < days:
            p["series"][index] += 1
        edge = edges.setdefault(
            (key, pid, agent),
            {
                "person": key,
                "project_id": str(pid),
                "agent": agent,
                "commits": 0,
                "additions": 0,
                "deletions": 0,
                "last_at": None,
                "telemetry": None,
            },
        )
        edge["commits"] += 1
        edge["additions"] += int(adds or 0)
        edge["deletions"] += int(dels or 0)
        edge["last_at"] = at.isoformat()
        stats = project_stats[pid]
        stats["commits"] += 1
        stats["agent_commits"] += 1 if agent != "none" else 0
        stats["people"].add(key)
        for service in services or []:
            service_edges[(pid, service)] += 1
            stats["services"].add(service)
        if unmapped:
            service_edges[(pid, None)] += 1

    # Claude Code's own usage, per person and project.
    usage_rows = (
        await connection.execute(
            text(
                """
                SELECT project_id, identity, max(person_hint), sum(cost_usd),
                       sum(input_tokens), sum(output_tokens), sum(cache_read_tokens),
                       sum(cache_creation_tokens), sum(api_requests), sum(api_errors),
                       sum(prompts), sum(sessions), sum(active_seconds),
                       sum(lines_added), sum(lines_removed), sum(commits),
                       sum(pull_requests), sum(edits_accepted), sum(edits_rejected),
                       count(active_seconds), count(lines_added)
                FROM dev_agent_days
                WHERE project_id = ANY(:ids) AND day >= :d
                GROUP BY project_id, identity
                """
            ),
            {"ids": list(projects) or [uuid.UUID(int=0)], "d": first_day},
        )
    ).all()
    breakdown_rows = (
        await connection.execute(
            text(
                """
                SELECT project_id, identity, kind, name, sum(calls), sum(failures),
                       sum(cost_usd), sum(tokens)
                FROM dev_agent_breakdown
                WHERE project_id = ANY(:ids) AND day >= :d
                GROUP BY project_id, identity, kind, name
                """
            ),
            {"ids": list(projects) or [uuid.UUID(int=0)], "d": first_day},
        )
    ).all()
    identity_person: dict[str, str] = {}
    for row in usage_rows:
        pid, identity, hint = uuid.UUID(str(row[0])), str(row[1]), row[2]
        key, linked = people.of_claude(identity, hint)
        identity_person[identity] = key
        p = node(key)
        p["linked"] = linked
        p["projects"].add(str(pid))
        t = _usage(row)
        p["telemetry"] = _merge(p["telemetry"], t)
        edge = edges.setdefault(
            (key, pid, "claude_code"),
            {
                "person": key,
                "project_id": str(pid),
                "agent": "claude_code",
                "commits": 0,
                "additions": 0,
                "deletions": 0,
                "last_at": None,
                "telemetry": None,
            },
        )
        edge["telemetry"] = _merge(edge["telemetry"], t)
        project_stats[pid]["people"].add(key)
    breakdowns: dict[str, dict[str, dict[str, dict[str, float]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for _pid, identity, kind, name, calls, failures, cost, tokens in breakdown_rows:
        owner = identity_person.get(str(identity))
        if owner is None:
            continue
        slot = breakdowns[owner][str(kind)].setdefault(
            str(name), {"calls": 0.0, "failures": 0.0, "cost_usd": 0.0, "tokens": 0.0}
        )
        slot["calls"] += _num(calls)
        slot["failures"] += _num(failures)
        slot["cost_usd"] += _num(cost)
        slot["tokens"] += _num(tokens)

    people_out = []
    for key, p in person.items():
        agents = {a: n for a, n in p["agents"].items() if n}
        tools = [a for a in AGENT_ORDER if a != "none" and agents.get(a)]
        if p["telemetry"] and "claude_code" not in tools:
            tools.insert(0, "claude_code")
        telemetry = p["telemetry"]
        if telemetry is not None:
            b = breakdowns.get(key, {})
            telemetry = {
                **telemetry,
                "tools": _top(b.get("tool", {}), 8),
                "mcp_tools": _top(b.get("mcp", {}), 6),
                "models": _top(b.get("model", {}), 4),
                "languages": _top(b.get("language", {}), 6),
            }
        people_out.append(
            {
                "key": key,
                "name": people.display(key) or names.get(key) or _fallback_name(key),
                "logins": sorted(p["logins"]),
                "linked": bool(p["linked"]),
                "bot": p["bot"],
                "commits": p["commits"],
                "additions": p["additions"],
                "deletions": p["deletions"],
                "agents": agents,
                "tools": tools,
                "projects": sorted(p["projects"]),
                "active_days": len(p["days"]),
                "last_at": p["last_at"].isoformat() if p["last_at"] else None,
                "series": p["series"],
                "telemetry": telemetry,
            }
        )
    people_out.sort(
        key=lambda x: (-x["commits"], -(x["telemetry"] or {}).get("cost_usd", 0), x["name"])
    )

    edge_list = sorted(
        edges.values(), key=lambda e: (-e["commits"], -((e["telemetry"] or {}).get("cost_usd", 0)))
    )
    agent_totals: dict[str, int] = defaultdict(int)
    for e in edge_list:
        agent_totals[e["agent"]] += e["commits"]
    total_commits = len(commits)
    agent_commits = sum(n for a, n in agent_totals.items() if a != "none")
    top_agent = max(
        ((a, n) for a, n in agent_totals.items() if a != "none" and n),
        key=lambda x: x[1],
        default=None,
    )
    telemetry_people = [p["telemetry"] for p in person.values() if p["telemetry"]]
    return {
        "window": window,
        "as_of": moment.isoformat(),
        "days": [(first_day + timedelta(days=i)).isoformat() for i in range(days)],
        "configured": {
            "commits": reads_commits,
            "telemetry": reads_usage,
            "repositories": int(tracked or 0),
        },
        "summary": {
            "commits": total_commits,
            "agent_commits": agent_commits,
            "people": len(person),
            "repositories": sum(1 for s in project_stats.values() if s["commits"] or s["people"]),
            "top_agent": top_agent[0] if top_agent else None,
            "agents": dict(agent_totals),
            "cost_usd": round(sum(t["cost_usd"] for t in telemetry_people), 4)
            if telemetry_people
            else None,
            "tokens": sum(t["tokens"] for t in telemetry_people) if telemetry_people else None,
            "sessions": sum(t["sessions"] for t in telemetry_people) if telemetry_people else None,
        },
        "people": people_out,
        "projects": [
            {
                **projects[pid],
                "commits": s["commits"],
                "agent_commits": s["agent_commits"],
                "people": len(s["people"]),
                "services": len(s["services"]),
            }
            for pid, s in sorted(
                project_stats.items(), key=lambda kv: (-kv[1]["commits"], projects[kv[0]]["name"])
            )
            if s["commits"] or s["people"]
        ],
        "services": [
            {"project_id": str(pid), "key": service, "commits": n}
            for (pid, service), n in sorted(service_edges.items(), key=lambda kv: -kv[1])
        ],
        "edges": edge_list,
    }


def _fallback_name(key: str) -> str:
    if key.startswith("name:"):
        return key[5:]
    if key.startswith("claude:"):
        return f"Claude Code · {key[7:13]}"
    return key


def _usage(row: Any) -> dict[str, Any]:
    tokens_in, tokens_out, cache_read, cache_create = (int(row[i] or 0) for i in (4, 5, 6, 7))
    return {
        "cost_usd": _num(row[3]),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_create,
        "tokens": tokens_in + tokens_out + cache_read + cache_create,
        "api_requests": int(row[8] or 0),
        "api_errors": int(row[9] or 0),
        "prompts": int(row[10] or 0),
        "sessions": int(row[11] or 0),
        # Measured only when the counter arrived at least once.
        "active_seconds": _num(row[12]) if row[19] else None,
        "lines_added": int(row[13] or 0) if row[20] else None,
        "lines_removed": int(row[14] or 0) if row[20] else None,
        "commits": int(row[15]) if row[15] is not None else None,
        "pull_requests": int(row[16]) if row[16] is not None else None,
        "edits_accepted": int(row[17] or 0),
        "edits_rejected": int(row[18] or 0),
    }


def _merge(a: dict[str, Any] | None, b: dict[str, Any]) -> dict[str, Any]:
    if a is None:
        return dict(b)
    out = dict(a)
    for key, value in b.items():
        if value is None:
            continue
        out[key] = value if out.get(key) is None else out[key] + value
    return out


def _top(items: dict[str, dict[str, float]], n: int) -> list[dict[str, Any]]:
    ranked = sorted(items.items(), key=lambda kv: (-kv[1]["calls"], kv[0]))[:n]
    return [
        {
            "name": name,
            "calls": int(v["calls"]),
            "failures": int(v["failures"]),
            "cost_usd": round(v["cost_usd"], 4),
            "tokens": int(v["tokens"]),
        }
        for name, v in ranked
    ]


async def dev_commits(
    connection: AsyncConnection,
    principal: Principal,
    *,
    window: str,
    person: str | None,
    project: uuid.UUID | None,
    service: str | None,
    agent: str | None,
    limit: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=WINDOWS[window])
    project_ids, _ = await _visibility(connection, principal)
    ids = [p for p in project_ids if project is None or p == project] or [uuid.UUID(int=0)]
    people = await _people(connection)
    rows = (
        await connection.execute(
            text(
                """
                SELECT c.sha, c.title, c.authored_at, c.author_login, c.author_name, c.agent,
                       c.agent_evidence, c.additions, c.deletions, c.services, c.pr_number,
                       c.repository, c.project_id, p.project_key, p.display_name
                FROM dev_commits c JOIN projects p ON p.id = c.project_id
                WHERE c.project_id = ANY(:ids) AND c.authored_at >= :since
                  AND (CAST(:agent AS text) IS NULL OR c.agent = :agent)
                  AND (CAST(:service AS text) IS NULL OR :service = ANY(c.services))
                ORDER BY c.authored_at DESC
                LIMIT 2000
                """
            ),
            {"ids": ids, "since": since, "agent": agent, "service": service},
        )
    ).all()
    items = []
    for r in rows:
        key = people.of_login(r[3], r[4] or "")
        if person and key != person:
            continue
        repo = str(r[11])
        items.append(
            {
                "sha": str(r[0]),
                "short": str(r[0])[:7],
                "title": str(r[1]),
                "authored_at": r[2].isoformat(),
                "author_login": r[3],
                "author_name": r[4],
                "person": key,
                "agent": r[5],
                "evidence": r[6],
                "additions": int(r[7]),
                "deletions": int(r[8]),
                "services": list(r[9] or []),
                "pr_number": r[10],
                "repository": repo,
                "project_id": str(r[12]),
                "project_key": str(r[13]),
                "project_name": str(r[14]),
                "url": f"https://github.com/{repo}/commit/{r[0]}",
                "pr_url": f"https://github.com/{repo}/pull/{r[10]}" if r[10] else None,
            }
        )
    return {"items": items[:limit], "total": len(items), "window": window}


async def list_aliases(connection: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await connection.execute(
            text(
                "SELECT kind, alias, person_key, display_name, created_at "
                "FROM dev_person_aliases ORDER BY person_key, kind, alias"
            )
        )
    ).all()
    return [
        {
            "kind": r[0],
            "alias": r[1],
            "person_key": r[2],
            "display_name": r[3],
            "created_at": r[4].isoformat(),
        }
        for r in rows
    ]


async def set_alias(
    connection: AsyncConnection,
    *,
    kind: str,
    alias: str,
    person_key: str,
    display_name: str | None,
    actor: uuid.UUID,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO dev_person_aliases (kind, alias, person_key, display_name, created_by)
            VALUES (:kind, :alias, :person, :name, :actor)
            ON CONFLICT (kind, alias) DO UPDATE
              SET person_key = EXCLUDED.person_key, display_name = EXCLUDED.display_name
            """
        ),
        {
            "kind": kind,
            "alias": alias.lower() if kind == "github" else alias,
            "person": person_key,
            "name": display_name,
            "actor": actor,
        },
    )
    # A person's name is one name: every alias of the person shows it.
    if display_name:
        await connection.execute(
            text("UPDATE dev_person_aliases SET display_name = :n WHERE person_key = :p"),
            {"n": display_name, "p": person_key},
        )


async def delete_alias(connection: AsyncConnection, *, kind: str, alias: str) -> bool:
    result = await connection.execute(
        text("DELETE FROM dev_person_aliases WHERE kind = :k AND lower(alias) = lower(:a)"),
        {"k": kind, "a": alias},
    )
    return bool(result.rowcount)
