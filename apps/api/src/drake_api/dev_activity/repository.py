"""Reading development activity: the map, a slice of commits, aliases.

The map is per repository: a catalogued repository carries its project (and
its services); a repository of a watched GitHub organisation that nobody has
catalogued is a repository node of its own.

Visibility follows the catalog exactly as the rest of Agent Visibility does:
a project's commits and usage are seen by those holding `agent.view` and
`project.view` on that project. A repository outside the catalog belongs to
no project, so only those holding `agent.view` at the organisation root see
it (as with agents outside the catalog). Aliases are organisation-wide, so
changing them takes `agent.manage` at the organisation root.

One person is one node: a GitHub login, a second login, and a Claude Code
identity collapse into one when an alias says so, or when Claude Code's
`tracesis.person` attribute names the login. A Claude identity nobody has
linked stays its own node, labelled as unlinked — never guessed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
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
        """(person key, linked).

        Usage a developer imported from their own history is kept under
        `backfill:<account>`; it is the same Claude account as the live rows,
        so it resolves to the same person.
        """
        account = identity[len("backfill:") :] if identity.startswith("backfill:") else identity
        hit = self.by_alias.get(("claude", account.lower()))
        if hit:
            return hit[0], True
        if hint:
            return self.of_login(hint, ""), True
        return f"claude:{account[:12].lower()}", False

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


def repository_id(external_id: Any, full_name: str) -> str:
    """A repository's node id: GitHub's id when known (a rename keeps it)."""
    if external_id is not None:
        return f"gh:{int(external_id)}"
    return f"repo:{full_name.lower()}"


def _repository_filter(repository: str | None) -> tuple[int | None, str | None]:
    if not repository:
        return None, None
    if repository.startswith("gh:"):
        return int(repository[3:]), None
    if repository.startswith("repo:"):
        return None, repository[5:].lower()
    return None, None


async def dev_map(
    connection: AsyncConnection,
    principal: Principal,
    *,
    window: str,
    now: datetime | None = None,
    reads_commits: bool,
    reads_usage: bool,
    orgs: Iterable[str] = (),
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    days = WINDOWS[window]
    since = moment - timedelta(days=days)
    first_day = (moment - timedelta(days=days - 1)).date()
    project_ids, org_wide = await _visibility(connection, principal)
    people = await _people(connection)
    org_names = sorted({o.strip().lower() for o in orgs if o and o.strip()})
    ids = project_ids or [uuid.UUID(int=0)]

    projects = {
        uuid.UUID(str(r[0])): {"key": str(r[1]), "name": str(r[2])}
        for r in (
            await connection.execute(
                text(
                    "SELECT id, project_key, display_name FROM projects "
                    "WHERE id = ANY(:ids) AND lifecycle = 'active'"
                ),
                {"ids": ids},
            )
        ).all()
    }
    # What the loop reads that this caller may see: the visible projects'
    # repositories, and (organisation-wide only) the watched orgs' others.
    tracked = (
        await connection.execute(
            text(
                """
                SELECT count(DISTINCT r.external_id) FROM github_repositories r
                LEFT JOIN projects p
                  ON p.lifecycle = 'active'
                 AND lower(r.full_name) = lower(p.repo_owner || '/' || p.repo_name)
                WHERE r.access_state = 'accessible' AND NOT r.archived
                  AND r.security_gate IS NULL
                  AND (p.id = ANY(:ids)
                       OR (:org_wide AND p.id IS NULL AND lower(r.owner_login) = ANY(:orgs)))
                """
            ),
            {"ids": ids, "org_wide": org_wide, "orgs": org_names},
        )
    ).scalar_one()
    known_ids = {
        str(r[0]): int(r[1])
        for r in (
            await connection.execute(
                text(
                    "SELECT lower(full_name), external_id FROM github_repositories "
                    "ORDER BY last_reconciled_at NULLS FIRST"
                )
            )
        ).all()
    }

    commits = (
        await connection.execute(
            text(
                """
                SELECT repository_external_id, repository, project_id, author_login,
                       author_name, agent, additions, deletions, services, unmapped_files,
                       authored_at
                FROM dev_commits
                WHERE authored_at >= :since
                  AND (project_id = ANY(:ids) OR (:org_wide AND project_id IS NULL))
                ORDER BY authored_at
                """
            ),
            {"ids": list(projects) or [uuid.UUID(int=0)], "org_wide": org_wide, "since": since},
        )
    ).all()

    person: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    repositories: dict[str, dict[str, Any]] = {}

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
                "repositories": set(),
                "days": set(),
                "last_at": None,
                "series": [0] * days,
                "telemetry": None,
                "bot": False,
            }
        return person[key]

    def repository(rid: str, full_name: str, pid: Any) -> dict[str, Any]:
        project_id = uuid.UUID(str(pid)) if pid is not None else None
        if rid not in repositories:
            project = projects.get(project_id) if project_id else None
            repositories[rid] = {
                "id": rid,
                "full_name": full_name,
                "name": full_name.rpartition("/")[2],
                "url": f"https://github.com/{full_name}",
                "catalogued": project is not None,
                "project_id": str(project_id) if project else None,
                "project_key": project["key"] if project else None,
                "project_name": project["name"] if project else None,
                "commits": 0,
                "agent_commits": 0,
                "people": set(),
                "services": set(),
                "cost_usd": None,
            }
        return repositories[rid]

    def edge_of(key: str, rid: str, agent: str) -> dict[str, Any]:
        return edges.setdefault(
            (key, rid, agent),
            {
                "person": key,
                "repository": rid,
                "agent": agent,
                "commits": 0,
                "additions": 0,
                "deletions": 0,
                "last_at": None,
                "telemetry": None,
            },
        )

    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    service_edges: dict[tuple[str, str | None], int] = defaultdict(int)
    for ext, full, pid, login, name, agent, adds, dels, services, unmapped, at in commits:
        rid = repository_id(ext, str(full))
        repo = repository(rid, str(full), pid)
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
        p["repositories"].add(rid)
        p["days"].add(at.date())
        p["last_at"] = at
        index = (at.date() - first_day).days
        if 0 <= index < days:
            p["series"][index] += 1
        edge = edge_of(key, rid, agent)
        edge["commits"] += 1
        edge["additions"] += int(adds or 0)
        edge["deletions"] += int(dels or 0)
        edge["last_at"] = at.isoformat()
        repo["commits"] += 1
        repo["agent_commits"] += 1 if agent != "none" else 0
        repo["people"].add(key)
        if repo["catalogued"]:
            # Services are the catalog's: a repository outside it has none.
            for service in services or []:
                service_edges[(rid, service)] += 1
                repo["services"].add(service)
            if unmapped:
                service_edges[(rid, None)] += 1

    # Claude Code's own usage, per person and repository.
    usage_where = "day >= :d AND (project_id = ANY(:ids) OR (:org_wide AND project_id IS NULL))"
    usage_params = {"ids": list(projects) or [uuid.UUID(int=0)], "org_wide": org_wide}
    usage_rows = (
        await connection.execute(
            text(
                f"""
                SELECT repository, max(repository_external_id), max(project_id::text),
                       identity, max(person_hint), sum(cost_usd),
                       sum(input_tokens), sum(output_tokens), sum(cache_read_tokens),
                       sum(cache_creation_tokens), sum(api_requests), sum(api_errors),
                       sum(prompts), sum(sessions), sum(active_seconds),
                       sum(lines_added), sum(lines_removed), sum(commits),
                       sum(pull_requests), sum(edits_accepted), sum(edits_rejected),
                       count(active_seconds), count(lines_added)
                FROM dev_agent_days
                WHERE {usage_where}
                GROUP BY repository, identity
                """  # noqa: S608 - fixed fragment; values are bound parameters
            ),
            {**usage_params, "d": first_day},
        )
    ).all()
    breakdown_rows = (
        await connection.execute(
            text(
                f"""
                SELECT identity, kind, name, sum(calls), sum(failures),
                       sum(cost_usd), sum(tokens)
                FROM dev_agent_breakdown
                WHERE {usage_where}
                GROUP BY identity, kind, name
                """  # noqa: S608 - fixed fragment; values are bound parameters
            ),
            {**usage_params, "d": first_day},
        )
    ).all()
    identity_person: dict[str, str] = {}
    for row in usage_rows:
        full, ext, pid = str(row[0]), row[1], row[2]
        rid = repository_id(ext if ext is not None else known_ids.get(full), full)
        repo = repository(rid, full, pid)
        identity, hint = str(row[3]), row[4]
        key, linked = people.of_claude(identity, hint)
        identity_person[identity] = key
        p = node(key)
        p["linked"] = linked
        p["repositories"].add(rid)
        t = _usage(row[2:])
        p["telemetry"] = _merge(p["telemetry"], t)
        edge = edge_of(key, rid, "claude_code")
        edge["telemetry"] = _merge(edge["telemetry"], t)
        repo["people"].add(key)
        repo["cost_usd"] = (repo["cost_usd"] or 0.0) + t["cost_usd"]
    breakdowns: dict[str, dict[str, dict[str, dict[str, float]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for identity, kind, name, calls, failures, cost, tokens in breakdown_rows:
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
                "repositories": sorted(p["repositories"]),
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
    repository_out = [
        {
            **r,
            "people": len(r["people"]),
            "services": len(r["services"]),
            "cost_usd": round(r["cost_usd"], 4) if r["cost_usd"] is not None else None,
        }
        for r in sorted(
            repositories.values(),
            # The catalog's first, then by activity.
            key=lambda r: (
                not r["catalogued"],
                -r["commits"],
                -(r["cost_usd"] or 0),
                (r["project_name"] or r["name"]).lower(),
            ),
        )
    ]
    return {
        "window": window,
        "as_of": moment.isoformat(),
        "days": [(first_day + timedelta(days=i)).isoformat() for i in range(days)],
        "configured": {
            "commits": reads_commits,
            "telemetry": reads_usage,
            "repositories": int(tracked or 0),
            "organisations": org_names if org_wide else [],
        },
        "summary": {
            "commits": total_commits,
            "agent_commits": agent_commits,
            "people": len(person),
            "repositories": len(repository_out),
            "catalogued": sum(1 for r in repository_out if r["catalogued"]),
            "top_agent": top_agent[0] if top_agent else None,
            "agents": dict(agent_totals),
            "cost_usd": round(sum(t["cost_usd"] for t in telemetry_people), 4)
            if telemetry_people
            else None,
            "tokens": sum(t["tokens"] for t in telemetry_people) if telemetry_people else None,
            "sessions": sum(t["sessions"] for t in telemetry_people) if telemetry_people else None,
        },
        "people": people_out,
        "repositories": repository_out,
        "services": [
            {"repository": rid, "key": service, "commits": n}
            for (rid, service), n in sorted(service_edges.items(), key=lambda kv: -kv[1])
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
    """Telemetry totals from a usage row (its columns after the repository ones)."""
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
    repository: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=WINDOWS[window])
    project_ids, org_wide = await _visibility(connection, principal)
    ids = [p for p in project_ids if project is None or p == project] or [uuid.UUID(int=0)]
    external, full_name = _repository_filter(repository)
    people = await _people(connection)
    rows = (
        await connection.execute(
            text(
                """
                SELECT c.sha, c.title, c.authored_at, c.author_login, c.author_name, c.agent,
                       c.agent_evidence, c.additions, c.deletions, c.services, c.pr_number,
                       c.repository, c.project_id, p.project_key, p.display_name,
                       c.repository_external_id
                FROM dev_commits c LEFT JOIN projects p ON p.id = c.project_id
                WHERE c.authored_at >= :since
                  AND (c.project_id = ANY(:ids)
                       OR (:org_wide AND CAST(:project AS uuid) IS NULL
                           AND c.project_id IS NULL))
                  AND (CAST(:external AS bigint) IS NULL
                       OR c.repository_external_id = :external)
                  AND (CAST(:full AS text) IS NULL OR lower(c.repository) = :full)
                  AND (CAST(:agent AS text) IS NULL OR c.agent = :agent)
                  AND (CAST(:service AS text) IS NULL OR :service = ANY(c.services))
                ORDER BY c.authored_at DESC
                LIMIT 2000
                """
            ),
            {
                "ids": ids,
                "org_wide": org_wide,
                "project": project,
                "external": external,
                "full": full_name,
                "since": since,
                "agent": agent,
                "service": service,
            },
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
                "repository_id": repository_id(r[15], repo),
                "project_id": str(r[12]) if r[12] is not None else None,
                "project_key": str(r[13]) if r[13] is not None else None,
                "project_name": str(r[14]) if r[14] is not None else None,
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


async def _person_logins(
    connection: AsyncConnection,
    *,
    project_ids: list[Any] | None,
    org_wide: bool,
    now: datetime | None = None,
) -> dict[str, set[str]]:
    """Every person on the map within the widest window → their GitHub logins.

    `project_ids=None` means everything (an operator's view, no caller).
    People without a login (a commit name, an unlinked Claude identity) are
    present with no logins.
    """
    since = (now or datetime.now(UTC)) - timedelta(days=WINDOWS["90d"])
    people = await _people(connection)
    params: dict[str, Any] = {
        "ids": project_ids or [uuid.UUID(int=0)],
        "org_wide": org_wide,
        "everything": project_ids is None,
        "since": since,
        "day": since.date(),
    }
    visible = "(:everything OR project_id = ANY(:ids) OR (:org_wide AND project_id IS NULL))"
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT DISTINCT 'commit', author_login, author_name FROM dev_commits
                WHERE authored_at >= :since AND {visible}
                UNION
                SELECT DISTINCT 'claude', identity, person_hint FROM dev_agent_days
                WHERE day >= :day AND {visible}
                """  # noqa: S608 - fixed fragment; values are bound parameters
            ),
            params,
        )
    ).all()
    found: dict[str, set[str]] = defaultdict(set)
    for source, first, second in rows:
        if source == "commit":
            login = str(first) if first else None
            if login and login.endswith("[bot]"):
                continue
            key = people.of_login(login, str(second or ""))
            found[key].update([login] if login else [])
        else:
            hint = str(second) if second else None
            key, _ = people.of_claude(str(first), hint)
            linked = hint and not hint.endswith("[bot]") and key == people.of_login(hint, "")
            found[key].update([hint] if linked and hint else [])
    return dict(found)


async def visible_people(
    connection: AsyncConnection,
    principal: Principal,
    *,
    now: datetime | None = None,
) -> dict[str, set[str]]:
    """The people this caller can see on the map → their GitHub logins.

    Only rows the caller may see count — a commit, or a Claude Code session —
    so neither a picture nor a portrait ever answers "does this person exist
    elsewhere".
    """
    project_ids, org_wide = await _visibility(connection, principal)
    return await _person_logins(
        connection, project_ids=list(project_ids), org_wide=org_wide, now=now
    )


async def person_login(
    connection: AsyncConnection,
    principal: Principal,
    person_key: str,
    *,
    now: datetime | None = None,
) -> str | None:
    """The GitHub login whose picture stands for a person, if the caller sees them.

    The person's own key wins when it is one of their logins; otherwise the
    first, alphabetically.
    """
    logins = sorted(
        (await visible_people(connection, principal, now=now)).get(person_key, set()), key=str.lower
    )
    for login in logins:
        if login.lower() == person_key.lower():
            return login
    return logins[0] if logins else None


async def map_people(
    connection: AsyncConnection, *, now: datetime | None = None
) -> dict[str, set[str]]:
    """Every person on the map, whoever looks (operators' tooling only)."""
    return await _person_logins(connection, project_ids=None, org_wide=True, now=now)


# --------------------------------------------------------------------------
# Portraits
# --------------------------------------------------------------------------


async def portrait(connection: AsyncConnection, person_key: str) -> dict[str, Any] | None:
    row = (
        await connection.execute(
            text(
                "SELECT media_type, content, updated_at FROM dev_person_portraits "
                "WHERE person_key = :k"
            ),
            {"k": person_key},
        )
    ).one_or_none()
    if row is None:
        return None
    return {"media_type": str(row[0]), "content": bytes(row[1]), "updated_at": row[2]}


async def set_portrait(
    connection: AsyncConnection,
    *,
    person_key: str,
    media_type: str,
    content: bytes,
    width: int,
    height: int,
    updated_by: uuid.UUID | None = None,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO dev_person_portraits
              (person_key, media_type, content, width, height, updated_by, updated_at)
            VALUES (:k, :t, :c, :w, :h, :by, now())
            ON CONFLICT (person_key) DO UPDATE SET
              media_type = EXCLUDED.media_type, content = EXCLUDED.content,
              width = EXCLUDED.width, height = EXCLUDED.height,
              updated_by = EXCLUDED.updated_by, updated_at = now()
            """
        ),
        {"k": person_key, "t": media_type, "c": content, "w": width, "h": height, "by": updated_by},
    )


async def delete_portrait(connection: AsyncConnection, person_key: str) -> bool:
    result = await connection.execute(
        text("DELETE FROM dev_person_portraits WHERE person_key = :k"), {"k": person_key}
    )
    return bool(result.rowcount)


async def list_portraits(connection: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await connection.execute(
            text(
                "SELECT person_key, media_type, width, height, octet_length(content), updated_at "
                "FROM dev_person_portraits ORDER BY person_key"
            )
        )
    ).all()
    return [
        {
            "person_key": str(r[0]),
            "media_type": str(r[1]),
            "width": int(r[2]),
            "height": int(r[3]),
            "bytes": int(r[4]),
            "updated_at": r[5],
        }
        for r in rows
    ]


async def avatar_installation(connection: AsyncConnection) -> int | None:
    """An active App installation to look a public profile up through."""
    found = (
        await connection.execute(
            text(
                "SELECT external_id FROM github_installations "
                "WHERE state = 'active' AND suspended_at IS NULL ORDER BY external_id LIMIT 1"
            )
        )
    ).scalar_one_or_none()
    return int(found) if found is not None else None
