"""The development-activity loop: commits from GitHub, usage from Claude Code.

Two harvests per cycle, under one lease, each independent of the other:

1. **Commits.** For every catalog project whose GitHub repository the App can
   read, page the default branch's commits since the newest one kept (90 days
   on a first run), fetch each new commit's detail for its size and changed
   paths, and keep one row: author login and name (never the email), the
   coding agent the commit names, the services its paths fall under. A
   per-cycle budget of detail calls keeps a large backfill from crowding the
   App's rate limit; what does not fit is picked up next cycle.
2. **Claude Code usage.** Claude Code's events and counters arrive through the
   gateway's external door into the store (content already removed there).
   Whole days are re-read — today and yesterday; the store's week on a first
   run — and replaced in Postgres per day, project and Claude identity. A
   session is kept only when it says which catalog project it worked on: a
   `tracesis.project` resource attribute naming a project key, or repository
   attributes (`vcs.*`) naming a catalog repository. Everything else is left
   out.

Nothing here writes anywhere but Tracesis's own tables.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.dev_activity import classify
from drake_api.observability.scope import clickhouse_time
from drake_api.observability.signals import MAX_GROUPS as SIGNAL_MAX_GROUPS

logger = logging.getLogger(__name__)

LEASE_KEY = "drake:dev-activity:lease"
_RELEASE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
)
RETENTION = timedelta(days=90)
COMMIT_PERMISSIONS = {"metadata": "read", "contents": "read"}
MAX_COMMIT_PAGES = 10
USAGE_BACKFILL = timedelta(days=7)
# The reader profile refuses any max_result_rows above its ceiling (1000,
# deploy/otel clickhouse-config): asking for more fails the whole read. The
# rows are already grouped per day, project, person and tool, so the
# ceiling holds a week of a team's usage. A unit test pins it.
MAX_USAGE_ROWS = SIGNAL_MAX_GROUPS


class CommitSource(Protocol):
    """What the loop needs from GitHub (GitHubClient, or a test double)."""

    async def installation_token(
        self,
        installation_id: int,
        *,
        repository_ids: list[int] | None = None,
        permissions: dict[str, str] | None = None,
    ) -> Any: ...

    async def list_commits_page(
        self, token: Any, owner: str, repo: str, *, branch: str, since: str, page: int
    ) -> list[dict[str, Any]]: ...

    async def get_commit(self, token: Any, owner: str, repo: str, sha: str) -> dict[str, Any]: ...


class UsageStore(Protocol):
    async def query(self, statement: str, parameters: dict[str, str], **kwargs: Any) -> Any: ...


@dataclass
class Report:
    lease_acquired: bool = False
    repositories: int = 0
    commits_added: int = 0
    detail_calls: int = 0
    failed_repositories: int = 0
    usage_rows: int = 0
    usage_failed_reads: int = 0
    budget_exhausted: bool = False
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# commits
# --------------------------------------------------------------------------


@dataclass
class Target:
    project_id: uuid.UUID
    project_key: str
    owner: str
    name: str
    branch: str
    repository_external_id: int | None
    installation_external_id: int
    services: list[str]

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


async def targets(engine: AsyncEngine) -> list[Target]:
    """Catalog projects whose repository the App can read, with their services."""
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT p.id, p.project_key, p.repo_owner, p.repo_name,
                           coalesce(nullif(r.default_branch, ''), p.default_branch),
                           r.external_id, i.external_id
                    FROM projects p
                    JOIN github_repositories r
                      ON lower(r.full_name) = lower(p.repo_owner || '/' || p.repo_name)
                    JOIN github_installations i ON i.id = r.installation_id
                    WHERE p.lifecycle = 'active' AND p.repo_provider = 'github'
                      AND r.access_state = 'accessible' AND NOT r.archived
                      AND i.state = 'active'
                    ORDER BY p.project_key
                    """
                )
            )
        ).all()
        services: dict[uuid.UUID, list[str]] = {}
        for project_id, key in (
            await connection.execute(
                text("SELECT project_id, service_key FROM service_definitions")
            )
        ).all():
            services.setdefault(uuid.UUID(str(project_id)), []).append(str(key))
    return [
        Target(
            project_id=uuid.UUID(str(r[0])),
            project_key=str(r[1]),
            owner=str(r[2]),
            name=str(r[3]),
            branch=str(r[4] or "main"),
            repository_external_id=int(r[5]) if r[5] is not None else None,
            installation_external_id=int(r[6]),
            services=services.get(uuid.UUID(str(r[0])), []),
        )
        for r in rows
    ]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _seq(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _worth_reading(item: dict[str, Any]) -> bool:
    login = _text(_obj(item.get("author")).get("login"))
    return not classify.is_automation(login) and len(_seq(item.get("parents"))) <= 1


def commit_row(
    target: Target, item: dict[str, Any], detail: dict[str, Any]
) -> dict[str, Any] | None:
    """One stored row from a listed commit and its detail, or None to skip."""
    commit = _obj(item.get("commit"))
    author = _obj(commit.get("author"))
    login = _text(_obj(item.get("author")).get("login"))
    name = _text(author.get("name")) or ""
    if not _worth_reading(item):
        # A merge commit repeats its branch's changes (the branch's own
        # commits are counted instead); automation is not a person.
        return None
    authored = _when(author.get("date"))
    if authored is None:
        return None
    message = _text(commit.get("message")) or ""
    agent, evidence = classify.classify(message, login)
    stats = _obj(detail.get("stats"))
    paths = [
        str(f["filename"])
        for f in _seq(detail.get("files"))
        if isinstance(f, dict) and isinstance(f.get("filename"), str)
    ]
    services, unmapped = classify.map_services(paths, target.services, target.project_key)
    # Names and logins only: anything that looks like an address is dropped.
    if "@" in name:
        name = ""
    if login and "@" in login:
        login = None
    return {
        "project_id": target.project_id,
        "sha": str(item.get("sha") or ""),
        "repository": target.full_name,
        "authored_at": authored,
        "author_login": login,
        "author_name": name[:200],
        "agent": agent,
        "agent_evidence": evidence,
        "title": classify.title_of(message),
        "additions": int(stats.get("additions") or 0),
        "deletions": int(stats.get("deletions") or 0),
        "files_changed": len(paths),
        "services": services,
        "unmapped_files": unmapped,
        "pr_number": classify.pr_number(message),
    }


_INSERT_COMMIT = text(
    """
    INSERT INTO dev_commits (project_id, sha, repository, authored_at, author_login,
        author_name, agent, agent_evidence, title, additions, deletions, files_changed,
        services, unmapped_files, pr_number)
    VALUES (:project_id, :sha, :repository, :authored_at, :author_login, :author_name,
        :agent, :agent_evidence, :title, :additions, :deletions, :files_changed,
        :services, :unmapped_files, :pr_number)
    ON CONFLICT (project_id, sha) DO NOTHING
    """
)


async def harvest_commits(
    engine: AsyncEngine,
    source: CommitSource,
    report: Report,
    *,
    now: datetime,
    budget: int,
) -> None:
    for target in await targets(engine):
        if report.detail_calls >= budget:
            report.budget_exhausted = True
            return
        report.repositories += 1
        try:
            await _harvest_one(engine, source, target, report, now=now, budget=budget)
        except Exception as error:  # one repository never stops the others
            report.failed_repositories += 1
            report.errors.append(f"{target.full_name}: {type(error).__name__}")
            logger.warning("dev activity: %s could not be read", target.full_name)


async def _harvest_one(
    engine: AsyncEngine,
    source: CommitSource,
    target: Target,
    report: Report,
    *,
    now: datetime,
    budget: int,
) -> None:
    async with engine.connect() as connection:
        newest = (
            await connection.execute(
                text("SELECT max(authored_at) FROM dev_commits WHERE project_id = :p"),
                {"p": target.project_id},
            )
        ).scalar_one_or_none()
        known = {
            str(r[0])
            for r in (
                await connection.execute(
                    text(
                        "SELECT sha FROM dev_commits WHERE project_id = :p "
                        "AND authored_at >= :since"
                    ),
                    {"p": target.project_id, "since": (newest or now) - timedelta(days=2)},
                )
            ).all()
        }
    # A day of overlap: GitHub's `since` reads the committer date, which a
    # rebase can move behind the author date kept here.
    since = max(now - RETENTION, (newest - timedelta(days=1)) if newest else now - RETENTION)
    token = await source.installation_token(
        target.installation_external_id,
        repository_ids=[target.repository_external_id] if target.repository_external_id else None,
        permissions=dict(COMMIT_PERMISSIONS),
    )
    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_COMMIT_PAGES + 1):
        items = await source.list_commits_page(
            token, target.owner, target.name, branch=target.branch, since=_iso(since), page=page
        )
        # Merges and automation are recognised from the listing itself, so
        # their detail is never fetched (and never re-fetched every cycle).
        fresh = [
            i
            for i in items
            if isinstance(i.get("sha"), str) and i["sha"] not in known and _worth_reading(i)
        ]
        for item in fresh:
            if report.detail_calls >= budget:
                report.budget_exhausted = True
                break
            report.detail_calls += 1
            detail = await source.get_commit(token, target.owner, target.name, item["sha"])
            row = commit_row(target, item, detail)
            if row is not None and row["sha"]:
                rows.append(row)
        if report.budget_exhausted or len(items) < 100:
            break
    if rows:
        async with engine.begin() as connection:
            for row in rows:
                result = await connection.execute(_INSERT_COMMIT, row)
                report.commits_added += result.rowcount or 0


# --------------------------------------------------------------------------
# Claude Code usage
# --------------------------------------------------------------------------

_EVENT = "replaceRegexpOne(LogAttributes['event.name'], '^claude_code\\\\.', '')"
_PROJECT_TAG = (
    "coalesce(nullIf(ResourceAttributes['tracesis.project'], ''), "
    "nullIf(LogAttributes['tracesis.project'], ''), '')"
)
_PERSON_HINT = (
    "coalesce(nullIf(ResourceAttributes['tracesis.person'], ''), "
    "nullIf(LogAttributes['tracesis.person'], ''), '')"
)
_IDENTITY = (
    "coalesce(nullIf(LogAttributes['user.account_uuid'], ''), "
    "nullIf(LogAttributes['user.id'], ''), '')"
)
# Repository attributes are matched by prefix: their exact keys are not
# documented, and whatever names the repository is enough to match it.
_VCS = (
    "arrayStringConcat(arrayConcat("
    "mapValues(mapFilter((k, v) -> startsWith(k, 'vcs.'), ResourceAttributes)), "
    "mapValues(mapFilter((k, v) -> startsWith(k, 'vcs.'), LogAttributes))), ' ')"
)


_EDIT_TOOLS = "('Edit', 'Write', 'MultiEdit', 'NotebookEdit')"


def usage_statements(database: str) -> tuple[str, str, str]:
    """(daily usage from events, breakdown from events, counters from metrics)."""
    logs = f"{database}.otel_logs"
    where = (
        "ServiceName = 'claude-code' "
        "AND Timestamp >= {start:DateTime64(9, 'UTC')} "
        "AND Timestamp < {end:DateTime64(9, 'UTC')}"
    )
    days = f"""
        SELECT toString(toDate(Timestamp)) AS day, {_PROJECT_TAG} AS project_tag,
               {_VCS} AS vcs, {_IDENTITY} AS identity, any({_PERSON_HINT}) AS person_hint,
               countIf({_EVENT} = 'api_request') AS api_requests,
               countIf({_EVENT} = 'api_error') AS api_errors,
               countIf({_EVENT} = 'user_prompt') AS prompts,
               uniqExact(nullIf(LogAttributes['session.id'], '')) AS sessions,
               sumIf(toFloat64OrZero(LogAttributes['cost_usd']), {_EVENT} = 'api_request') AS cost,
               sumIf(toUInt64OrZero(LogAttributes['input_tokens']), {_EVENT} = 'api_request')
                 AS input_tokens,
               sumIf(toUInt64OrZero(LogAttributes['output_tokens']), {_EVENT} = 'api_request')
                 AS output_tokens,
               sumIf(toUInt64OrZero(LogAttributes['cache_read_tokens']), {_EVENT} = 'api_request')
                 AS cache_read_tokens,
               sumIf(toUInt64OrZero(LogAttributes['cache_creation_tokens']),
                     {_EVENT} = 'api_request') AS cache_creation_tokens,
               countIf({_EVENT} = 'tool_decision' AND LogAttributes['decision'] = 'accept'
                       AND LogAttributes['tool_name'] IN {_EDIT_TOOLS}) AS edits_accepted,
               countIf({_EVENT} = 'tool_decision' AND LogAttributes['decision'] = 'reject'
                       AND LogAttributes['tool_name'] IN {_EDIT_TOOLS}) AS edits_rejected
        FROM {logs}
        WHERE {where}
        GROUP BY day, project_tag, vcs, identity
        HAVING identity != ''
        LIMIT {{max_rows:UInt32}}
    """  # noqa: S608 - fixed fragments; values are bound parameters
    breakdown = f"""
        SELECT toString(toDate(Timestamp)) AS day, {_PROJECT_TAG} AS project_tag,
               {_VCS} AS vcs, {_IDENTITY} AS identity,
               if({_EVENT} = 'api_request', 'model', 'tool') AS kind,
               if({_EVENT} = 'api_request', LogAttributes['model'], LogAttributes['tool_name'])
                 AS name,
               count() AS calls,
               countIf(LogAttributes['success'] = 'false') AS failures,
               sum(toFloat64OrZero(LogAttributes['cost_usd'])) AS cost,
               sum(toUInt64OrZero(LogAttributes['input_tokens'])
                   + toUInt64OrZero(LogAttributes['output_tokens'])
                   + toUInt64OrZero(LogAttributes['cache_read_tokens'])
                   + toUInt64OrZero(LogAttributes['cache_creation_tokens'])) AS tokens,
               sum(toUInt64OrZero(LogAttributes['duration_ms'])) AS duration_ms
        FROM {logs}
        WHERE {where} AND {_EVENT} IN ('api_request', 'tool_result')
        GROUP BY day, project_tag, vcs, identity, kind, name
        HAVING identity != '' AND name != ''
        LIMIT {{max_rows:UInt32}}
    """  # noqa: S608 - fixed fragments; values are bound parameters
    metrics = f"""
        SELECT toString(toDate(TimeUnix)) AS day,
               coalesce(nullIf(ResourceAttributes['tracesis.project'], ''),
                        nullIf(Attributes['tracesis.project'], ''), '') AS project_tag,
               arrayStringConcat(arrayConcat(
                 mapValues(mapFilter((k, v) -> startsWith(k, 'vcs.'), ResourceAttributes)),
                 mapValues(mapFilter((k, v) -> startsWith(k, 'vcs.'), Attributes))), ' ') AS vcs,
               coalesce(nullIf(Attributes['user.account_uuid'], ''),
                        nullIf(Attributes['user.id'], ''), '') AS identity,
               MetricName AS metric, Attributes['type'] AS type,
               Attributes['decision'] AS decision, Attributes['language'] AS language,
               sum(Value) AS value
        FROM {database}.otel_metrics_sum
        WHERE ServiceName = 'claude-code' AND AggregationTemporality = 1
          AND TimeUnix >= {{start:DateTime64(9, 'UTC')}}
          AND TimeUnix < {{end:DateTime64(9, 'UTC')}}
          AND MetricName IN ('claude_code.active_time.total', 'claude_code.lines_of_code.count',
                             'claude_code.commit.count', 'claude_code.pull_request.count',
                             'claude_code.code_edit_tool.decision')
        GROUP BY day, project_tag, vcs, identity, metric, type, decision, language
        HAVING identity != ''
        LIMIT {{max_rows:UInt32}}
    """  # noqa: S608 - fixed fragments; values are bound parameters
    return days, breakdown, metrics


@dataclass
class Project:
    id: uuid.UUID
    key: str
    repo: str


async def _projects(engine: AsyncEngine) -> list[Project]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, project_key, repo_owner || '/' || repo_name FROM projects "
                    "WHERE lifecycle = 'active'"
                )
            )
        ).all()
    return [Project(uuid.UUID(str(r[0])), str(r[1]), str(r[2])) for r in rows]


def resolve_project(projects: list[Project], tag: str, vcs: str) -> Project | None:
    """The catalog project a session worked on, or None to leave it out."""
    tag = (tag or "").strip().lower()
    if tag:
        for project in projects:
            if project.key.lower() == tag:
                return project
    haystack = (vcs or "").lower()
    if haystack:
        for project in projects:
            repo = project.repo.lower()
            if repo and (repo in haystack or f"{repo}.git" in haystack):
                return project
    return None


def _safe_identity(value: Any) -> str | None:
    text_value = str(value or "").strip()
    if not text_value or "@" in text_value:
        return None
    return text_value[:200]


async def harvest_usage(
    engine: AsyncEngine,
    store: UsageStore,
    report: Report,
    *,
    database: str,
    now: datetime,
) -> None:
    async with engine.connect() as connection:
        known = (
            await connection.execute(text("SELECT max(day) FROM dev_agent_days"))
        ).scalar_one_or_none()
    first_day: date = (now - timedelta(days=1)).date() if known else (now - USAGE_BACKFILL).date()
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=UTC)
    params = {
        "start": clickhouse_time(start),
        "end": clickhouse_time(now),
        "max_rows": str(MAX_USAGE_ROWS),
    }
    days_sql, breakdown_sql, metrics_sql = usage_statements(database)
    results: dict[str, list[dict[str, Any]] | None] = {}
    for name, sql in (("days", days_sql), ("breakdown", breakdown_sql), ("metrics", metrics_sql)):
        try:
            result = await store.query(
                sql,
                params,
                max_rows=MAX_USAGE_ROWS,
                timeout_seconds=20.0,
                correlation_id="dev-activity",
            )
            results[name] = result.rows if isinstance(result.rows, list) else []
        except Exception as error:
            # The metrics table exists only once a counter has arrived; a
            # failed read keeps what was kept rather than wiping it. It is
            # said out loud: a silent failure here once hid every cost.
            report.usage_failed_reads += 1
            logger.warning(
                "dev activity: usage read %s failed: %s %s",
                name,
                type(error).__name__,
                getattr(error, "code", "") or str(error)[:200],
            )
            results[name] = None
    if results["days"] is None:
        return
    projects = await _projects(engine)
    key = tuple[str, uuid.UUID, str]
    days: dict[key, dict[str, Any]] = {}
    for row in results["days"] or []:
        project = resolve_project(
            projects, str(row.get("project_tag") or ""), str(row.get("vcs") or "")
        )
        identity = _safe_identity(row.get("identity"))
        if project is None or identity is None:
            continue
        k = (str(row["day"]), project.id, identity)
        cur = days.setdefault(k, _empty_day())
        hint = _safe_identity(row.get("person_hint"))
        if hint:
            cur["person_hint"] = hint
        for field_name in (
            "api_requests",
            "api_errors",
            "prompts",
            "sessions",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "edits_accepted",
            "edits_rejected",
        ):
            cur[field_name] += int(row.get(field_name) or 0)
        cur["cost_usd"] += Decimal(str(row.get("cost") or 0))
    for row in results["metrics"] or []:
        project = resolve_project(
            projects, str(row.get("project_tag") or ""), str(row.get("vcs") or "")
        )
        identity = _safe_identity(row.get("identity"))
        if project is None or identity is None:
            continue
        cur = days.setdefault((str(row["day"]), project.id, identity), _empty_day())
        metric, value = str(row.get("metric")), float(row.get("value") or 0)
        if metric == "claude_code.active_time.total":
            cur["active_seconds"] = (cur["active_seconds"] or 0) + value
        elif metric == "claude_code.lines_of_code.count":
            field_name = "lines_added" if row.get("type") == "added" else "lines_removed"
            cur[field_name] = (cur[field_name] or 0) + int(value)
        elif metric == "claude_code.commit.count":
            cur["commits"] = (cur["commits"] or 0) + int(value)
        elif metric == "claude_code.pull_request.count":
            cur["pull_requests"] = (cur["pull_requests"] or 0) + int(value)
        elif metric == "claude_code.code_edit_tool.decision" and row.get("language"):
            lang = str(row["language"])[:200]
            cur["languages"][lang] = cur["languages"].get(lang, 0) + int(value)
    breakdown: dict[tuple[str, uuid.UUID, str, str, str], dict[str, Any]] = {}
    for row in results["breakdown"] or []:
        project = resolve_project(
            projects, str(row.get("project_tag") or ""), str(row.get("vcs") or "")
        )
        identity = _safe_identity(row.get("identity"))
        name = str(row.get("name") or "")[:200]
        if project is None or identity is None or not name:
            continue
        kind = str(row.get("kind"))
        if kind == "tool" and name.startswith("mcp__"):
            kind = "mcp"
        b = breakdown.setdefault(
            (str(row["day"]), project.id, identity, kind, name),
            {"calls": 0, "failures": 0, "cost_usd": Decimal(0), "tokens": 0, "duration_ms": 0},
        )
        b["calls"] += int(row.get("calls") or 0)
        b["failures"] += int(row.get("failures") or 0)
        b["cost_usd"] += Decimal(str(row.get("cost") or 0))
        b["tokens"] += int(row.get("tokens") or 0)
        b["duration_ms"] += int(row.get("duration_ms") or 0)
    for (day_s, project_id, identity), cur in days.items():
        for lang, count in cur["languages"].items():
            breakdown[(day_s, project_id, identity, "language", lang)] = {
                "calls": count,
                "failures": 0,
                "cost_usd": Decimal(0),
                "tokens": 0,
                "duration_ms": 0,
            }
    async with engine.begin() as connection:
        # Whole days are replaced, so a re-read never double counts.
        await connection.execute(
            text("DELETE FROM dev_agent_days WHERE day >= :d AND tool = 'claude_code'"),
            {"d": first_day},
        )
        if results["breakdown"] is not None:
            await connection.execute(
                text("DELETE FROM dev_agent_breakdown WHERE day >= :d AND tool = 'claude_code'"),
                {"d": first_day},
            )
        for (day_s, project_id, identity), cur in days.items():
            await connection.execute(
                text(
                    """
                    INSERT INTO dev_agent_days (day, project_id, identity, tool, person_hint,
                        cost_usd, input_tokens, output_tokens, cache_read_tokens,
                        cache_creation_tokens, api_requests, api_errors, prompts, sessions,
                        active_seconds, lines_added, lines_removed, commits, pull_requests,
                        edits_accepted, edits_rejected, updated_at)
                    VALUES (:day, :p, :i, 'claude_code', :person_hint, :cost_usd, :input_tokens,
                        :output_tokens, :cache_read_tokens, :cache_creation_tokens,
                        :api_requests, :api_errors, :prompts, :sessions, :active_seconds,
                        :lines_added, :lines_removed, :commits, :pull_requests,
                        :edits_accepted, :edits_rejected, now())
                    """
                ),
                {
                    "day": date.fromisoformat(day_s),
                    "p": project_id,
                    "i": identity,
                    **{k: v for k, v in cur.items() if k != "languages"},
                },
            )
            report.usage_rows += 1
        if results["breakdown"] is not None:
            for (day_s, project_id, identity, kind, name), b in breakdown.items():
                await connection.execute(
                    text(
                        """
                        INSERT INTO dev_agent_breakdown (day, project_id, identity, tool, kind,
                            name, calls, failures, cost_usd, tokens, duration_ms)
                        VALUES (:day, :p, :i, 'claude_code', :kind, :name, :calls, :failures,
                            :cost_usd, :tokens, :duration_ms)
                        """
                    ),
                    {
                        "day": date.fromisoformat(day_s),
                        "p": project_id,
                        "i": identity,
                        "kind": kind,
                        "name": name,
                        **b,
                    },
                )


def _empty_day() -> dict[str, Any]:
    return {
        "person_hint": None,
        "cost_usd": Decimal(0),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "api_requests": 0,
        "api_errors": 0,
        "prompts": 0,
        "sessions": 0,
        "active_seconds": None,
        "lines_added": None,
        "lines_removed": None,
        "commits": None,
        "pull_requests": None,
        "edits_accepted": 0,
        "edits_rejected": 0,
        "languages": {},
    }


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------


async def run_once(
    engine: AsyncEngine,
    redis: aioredis.Redis,
    *,
    source: CommitSource | None,
    store: UsageStore | None,
    database: str = "otel",
    budget: int = 300,
    now: datetime | None = None,
    lease_seconds: int = 900,
) -> Report:
    report = Report()
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(LEASE_KEY, token, nx=True, ex=lease_seconds)
    except Exception:
        logger.warning("dev activity: lease unavailable, skipping")
        return report
    if not acquired:
        return report
    report.lease_acquired = True
    moment = now or datetime.now(UTC)
    try:
        if source is not None:
            await harvest_commits(engine, source, report, now=moment, budget=budget)
        if store is not None:
            try:
                await harvest_usage(engine, store, report, database=database, now=moment)
            except Exception:
                logger.warning("dev activity: usage harvest failed")
                report.errors.append("usage")
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM dev_commits WHERE authored_at < :t"), {"t": moment - RETENTION}
            )
            await connection.execute(
                text("DELETE FROM dev_agent_days WHERE day < :d"),
                {"d": (moment - RETENTION).date()},
            )
            await connection.execute(
                text("DELETE FROM dev_agent_breakdown WHERE day < :d"),
                {"d": (moment - RETENTION).date()},
            )
    finally:
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, LEASE_KEY, token)
    return report


class DevActivity:
    """Lifespan-owned loop; exists only when development activity is enabled."""

    def __init__(
        self,
        engine: AsyncEngine,
        redis: aioredis.Redis,
        *,
        source: CommitSource | None,
        store: UsageStore | None,
        database: str,
        interval_seconds: float = 900.0,
        budget: int = 300,
    ) -> None:
        self._engine = engine
        self._redis = redis
        self._source = source
        self._store = store
        self._database = database
        self._interval = max(120.0, interval_seconds)
        self._budget = budget
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def reads_commits(self) -> bool:
        return self._source is not None

    @property
    def reads_usage(self) -> bool:
        return self._store is not None

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="dev-activity")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await run_once(
                    self._engine,
                    self._redis,
                    source=self._source,
                    store=self._store,
                    database=self._database,
                    budget=self._budget,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("dev activity: cycle failed")
            await asyncio.sleep(self._interval)
