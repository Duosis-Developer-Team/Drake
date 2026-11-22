"""Development agents against the real database.

GitHub and the trace store are doubles answering with chosen commits and
Claude Code rows; what is under test is what is kept (and what never is),
who a person becomes, and who may see it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis.asyncio as aioredis
from drake_api.dev_activity import collector
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, login_all, make_role
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

NOW = datetime.now(UTC).replace(microsecond=0)
CLAUDE = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


class FakeGitHub:
    def __init__(self, commits: list[dict[str, Any]], only: str | None = None) -> None:
        self.commits = commits
        self.only = only
        self.detail_calls = 0
        self.scopes: list[dict[str, Any]] = []

    async def installation_token(self, installation_id: int, **scope: Any) -> str:
        self.scopes.append({"installation": installation_id, **scope})
        return "token"

    async def list_commits_page(self, token: str, owner: str, repo: str, **kw: Any) -> list[dict]:
        if self.only is not None and repo != self.only:
            return []
        return self.commits if kw["page"] == 1 else []

    async def get_commit(self, token: str, owner: str, repo: str, sha: str) -> dict[str, Any]:
        self.detail_calls += 1
        files = next(c["_files"] for c in self.commits if c["sha"] == sha)
        return {
            "stats": {"additions": 10, "deletions": 3},
            "files": [{"filename": f} for f in files],
        }


def commit(sha: str, login: str, name: str, message: str, files: list[str], hours: int) -> dict:
    return {
        "sha": sha,
        "author": {"login": login},
        "parents": [{"sha": "p"}],
        "commit": {
            "author": {
                "name": name,
                "email": f"{login}@example.test",
                "date": (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "message": message,
        },
        "_files": files,
    }


class Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows


class FakeStore:
    def __init__(self, project_key: str) -> None:
        self.key = project_key

    async def query(self, statement: str, parameters: dict[str, str], **_: Any) -> Result:
        day = NOW.date().isoformat()
        if "otel_metrics_sum" in statement:
            return Result(
                [
                    {
                        "day": day,
                        "project_tag": self.key,
                        "vcs": "",
                        "identity": "acct-gencay",
                        "metric": "claude_code.active_time.total",
                        "type": "user",
                        "decision": "",
                        "language": "",
                        "value": 5400,
                    },
                    {
                        "day": day,
                        "project_tag": self.key,
                        "vcs": "",
                        "identity": "acct-gencay",
                        "metric": "claude_code.code_edit_tool.decision",
                        "type": "",
                        "decision": "accept",
                        "language": "TypeScript",
                        "value": 12,
                    },
                ]
            )
        if "AS kind" in statement:
            return Result(
                [
                    {
                        "day": day,
                        "project_tag": self.key,
                        "vcs": "",
                        "identity": "acct-gencay",
                        "kind": "model",
                        "name": "claude-opus-5-5",
                        "calls": 40,
                        "failures": 0,
                        "cost": 12.5,
                        "tokens": 900000,
                        "duration_ms": 1,
                    },
                    {
                        "day": day,
                        "project_tag": self.key,
                        "vcs": "",
                        "identity": "acct-gencay",
                        "kind": "tool",
                        "name": "Edit",
                        "calls": 30,
                        "failures": 1,
                        "cost": 0,
                        "tokens": 0,
                        "duration_ms": 1,
                    },
                    {
                        "day": day,
                        "project_tag": self.key,
                        "vcs": "",
                        "identity": "acct-gencay",
                        "kind": "tool",
                        "name": "mcp__hermes__hermes_list_projects",
                        "calls": 2,
                        "failures": 0,
                        "cost": 0,
                        "tokens": 0,
                        "duration_ms": 1,
                    },
                ]
            )
        return Result(
            [
                {
                    "day": day,
                    "project_tag": self.key,
                    "vcs": "",
                    "identity": "acct-gencay",
                    "person_hint": "gencaycoskuntr",
                    "api_requests": 40,
                    "api_errors": 1,
                    "prompts": 9,
                    "sessions": 2,
                    "cost": 12.5,
                    "input_tokens": 1000,
                    "output_tokens": 2000,
                    "cache_read_tokens": 800000,
                    "cache_creation_tokens": 97000,
                    "edits_accepted": 12,
                    "edits_rejected": 1,
                },
                # Says no project: never kept.
                {
                    "day": day,
                    "project_tag": "",
                    "vcs": "",
                    "identity": "acct-elsewhere",
                    "person_hint": "",
                    "api_requests": 5,
                    "api_errors": 0,
                    "prompts": 1,
                    "sessions": 1,
                    "cost": 1.0,
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "edits_accepted": 0,
                    "edits_rejected": 0,
                },
            ]
        )


@pytest.fixture
async def redis() -> Any:
    client = aioredis.from_url(require_it_settings().redis_url)
    await client.delete(collector.LEASE_KEY)
    yield client
    await client.delete(collector.LEASE_KEY)
    await client.aclose()


async def seed_repository(engine: AsyncEngine, world: dict[str, Any]) -> tuple[str, str, str]:
    async with engine.begin() as connection:
        key, scope, owner, name = (
            await connection.execute(
                text(
                    "SELECT project_key, scope_id, repo_owner, repo_name FROM projects "
                    "WHERE id = :p"
                ),
                {"p": world["project_id"]},
            )
        ).one()
        external = int(uuid.uuid4().int % 10**9)
        installation = (
            await connection.execute(
                text(
                    "INSERT INTO github_installations (external_id, scope_id, account_login, "
                    "account_type, state) VALUES (:e, :s, :o, 'Organization', 'active') "
                    "RETURNING id"
                ),
                {"e": external, "s": scope, "o": owner},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO github_repositories (installation_id, scope_id, external_id, "
                "full_name, owner_login, name, default_branch) "
                "VALUES (:i, :s, :e, :f, :o, :n, 'dev')"
            ),
            {
                "i": installation,
                "s": scope,
                "e": external + 1,
                "f": f"{owner}/{name}",
                "o": owner,
                "n": name,
            },
        )
        service = (
            await connection.execute(
                text("SELECT service_key FROM service_definitions WHERE project_id = :p"),
                {"p": world["project_id"]},
            )
        ).scalar_one()
    return str(key), str(service), str(name)


async def test_commits_and_claude_code_usage_become_one_map(
    engine: AsyncEngine, redis: Any
) -> None:
    async with engine.begin() as connection:
        # Aliases are organisation-wide; start from none.
        await connection.execute(text("DELETE FROM dev_person_aliases"))
        await connection.execute(text("DELETE FROM dev_agent_days"))
        await connection.execute(text("DELETE FROM dev_agent_breakdown"))
    world = await make_world(engine)
    key, service, _ = await seed_repository(engine, world)
    github = FakeGitHub(
        [
            commit(
                "a" * 40,
                "gencaycoskuntr",
                "Gencay",
                f"feat: one (#12){CLAUDE}",
                [f"backend/{service}/x.py"],
                2,
            ),
            commit(
                "b" * 40, "coskungencay", "Gencay Coşkun", f"fix: two{CLAUDE}", ["README.md"], 5
            ),
            commit("c" * 40, "eneserman", "Enes", "chore: by hand", [f"{service}/y.py"], 30),
            commit("d" * 40, "dependabot[bot]", "dependabot", "bump", ["package.json"], 1),
        ]
    )
    report = await collector.run_once(
        engine, redis, source=github, store=FakeStore(key), now=NOW, budget=50
    )
    assert report.lease_acquired and report.commits_added == 3, report
    # Scoped token: this repository only, read-only contents.
    assert github.scopes[0]["permissions"] == {"metadata": "read", "contents": "read"}
    assert len(github.scopes[0]["repository_ids"]) == 1

    # A second cycle reads nothing it already has.
    await collector.run_once(engine, redis, source=github, store=FakeStore(key), now=NOW, budget=50)
    # Dependabot is recognised from the listing: its detail is never read.
    assert github.detail_calls == 3

    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT sha, author_login, agent, services, pr_number FROM dev_commits "
                    "WHERE project_id = :p ORDER BY sha"
                ),
                {"p": world["project_id"]},
            )
        ).all()
        dump = repr(
            (
                await connection.execute(
                    text("SELECT * FROM dev_commits WHERE project_id = :p"),
                    {"p": world["project_id"]},
                )
            ).all()
        )
        days = (
            await connection.execute(
                text("SELECT identity, cost_usd, active_seconds FROM dev_agent_days")
            )
        ).all()
    assert [(r[0][0], r[1], r[2], list(r[3]), r[4]) for r in stored] == [
        ("a", "gencaycoskuntr", "claude_code", [service], 12),
        ("b", "coskungencay", "claude_code", [], None),
        ("c", "eneserman", "none", [service], None),
    ]
    assert "example.test" not in dump  # no email anywhere
    # Only the session that named a catalog project was kept.
    assert [(d[0], float(d[1]), float(d[2])) for d in days] == [("acct-gencay", 12.5, 5400.0)]

    harness = build_harness(require_it_settings())
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        body = (await client.get("/v1/agents/dev/map?window=30d")).json()
        people = {p["key"]: p for p in body["people"]}
        # Before an alias: two logins are two people; the Claude identity is
        # linked through tracesis.person to the login it names.
        assert {"gencaycoskuntr", "coskungencay", "eneserman"} <= set(people)
        gencay = people["gencaycoskuntr"]
        assert gencay["tools"][0] == "claude_code"
        assert gencay["telemetry"]["cost_usd"] == 12.5
        assert gencay["telemetry"]["active_seconds"] == 5400
        assert gencay["telemetry"]["tools"][0]["name"] == "Edit"
        assert gencay["telemetry"]["mcp_tools"][0]["name"] == "mcp__hermes__hermes_list_projects"
        assert gencay["telemetry"]["languages"][0] == {
            "name": "TypeScript",
            "calls": 12,
            "failures": 0,
            "cost_usd": 0.0,
            "tokens": 0,
        }
        # No telemetry is not zero.
        assert people["eneserman"]["telemetry"] is None
        assert body["summary"]["commits"] == 3 and body["summary"]["agent_commits"] == 2
        edge = next(
            e
            for e in body["edges"]
            if e["person"] == "gencaycoskuntr" and e["agent"] == "claude_code"
        )
        assert edge["commits"] == 1 and edge["telemetry"]["sessions"] == 2

        # One person out of two logins.
        me = (await client.get("/v1/me")).json()
        csrf = {"X-CSRF-Token": me["csrf_token"]}
        for login in ("gencaycoskuntr", "coskungencay"):
            put = await client.put(
                "/v1/agents/dev/aliases",
                json={
                    "kind": "github",
                    "alias": login,
                    "person_key": "gencay",
                    "display_name": "Gencay Coşkun",
                },
                headers=csrf,
            )
            assert put.status_code == 200, put.text
        refused = await client.put(
            "/v1/agents/dev/aliases",
            json={"kind": "github", "alias": "x@y.z", "person_key": "p"},
            headers=csrf,
        )
        assert refused.status_code == 422
        body = (await client.get("/v1/agents/dev/map?window=30d")).json()
        merged = next(p for p in body["people"] if p["key"] == "gencay")
        assert merged["name"] == "Gencay Coşkun" and merged["commits"] == 2
        assert sorted(merged["logins"]) == ["coskungencay", "gencaycoskuntr"]
        assert merged["telemetry"]["cost_usd"] == 12.5
        commits = (await client.get("/v1/agents/dev/commits?window=30d&person=gencay")).json()
        assert [c["short"] for c in commits["items"]] == ["aaaaaaa", "bbbbbbb"]
        assert commits["items"][0]["pr_url"].endswith("/pull/12")

    async with engine.connect() as connection:
        actions = (
            await connection.execute(
                text("SELECT count(*) FROM audit_events WHERE action = 'dev.alias.set'")
            )
        ).scalar_one()
    assert actions >= 2


async def test_the_map_is_scoped_like_the_catalog(engine: AsyncEngine, redis: Any) -> None:
    world = await make_world(engine)
    other = await make_world(engine)
    _, service, repo = await seed_repository(engine, world)
    await seed_repository(engine, other)
    github = FakeGitHub(
        [commit("e" * 40, "someone", "Someone", f"feat: x{CLAUDE}", [f"{service}/a.py"], 3)],
        only=repo,
    )
    await collector.run_once(engine, redis, source=github, store=None, now=NOW, budget=50)

    harness = build_harness(require_it_settings())
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-devviewer", user_type("user-devviewer", "Viewer", "user-devviewer@example.test")
    )
    await login_all(harness, ["user-devviewer"])
    await make_role(harness, engine, "Dev viewer", ["agent.view", "project.view"])
    async with engine.connect() as connection:
        scope_ref = (
            await connection.execute(
                text(
                    "SELECT s.external_ref FROM projects p JOIN scopes s ON s.id = p.scope_id "
                    "WHERE p.id = :p"
                ),
                {"p": other["project_id"]},
            )
        ).scalar_one()
    await grant(engine, harness, "user-devviewer", "Dev viewer", "project", scope_ref)
    async with harness.api_client() as client:
        await harness.login(client, "user-devviewer")
        body = (await client.get("/v1/agents/dev/map?window=7d")).json()
        assert all(p["id"] != str(world["project_id"]) for p in body["projects"])
        assert not any(p["key"] == "someone" for p in body["people"])
        commits = (await client.get("/v1/agents/dev/commits?window=7d")).json()
        assert all(c["project_id"] != str(world["project_id"]) for c in commits["items"])
        # Aliases are organisation-wide: not this viewer's to read or change.
        assert (await client.get("/v1/agents/dev/aliases")).status_code == 404
