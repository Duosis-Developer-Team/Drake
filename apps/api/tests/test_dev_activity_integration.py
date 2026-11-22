"""Development agents against the real database.

GitHub and the trace store are doubles answering with chosen commits and
Claude Code rows; what is under test is what is kept (and what never is),
who a person becomes, and who may see it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from drake_api.dev_activity import collector
from drake_api.dev_activity.avatars import Avatars
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
        # Rows of repositories outside the catalog outlive the catalog reset.
        await connection.execute(text("DELETE FROM dev_commits WHERE project_id IS NULL"))
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
        # The catalogued repository is one node that carries its project.
        [repository] = body["repositories"]
        assert repository["catalogued"] and repository["project_id"] == str(world["project_id"])
        assert edge["repository"] == repository["id"] and repository["id"].startswith("gh:")
        assert repository["cost_usd"] == 12.5
        assert {s["repository"] for s in body["services"]} == {repository["id"]}

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
        assert all(r["project_id"] != str(world["project_id"]) for r in body["repositories"])
        assert not any(p["key"] == "someone" for p in body["people"])
        commits = (await client.get("/v1/agents/dev/commits?window=7d")).json()
        assert all(c["project_id"] != str(world["project_id"]) for c in commits["items"])
        # Aliases are organisation-wide: not this viewer's to read or change.
        assert (await client.get("/v1/agents/dev/aliases")).status_code == 404


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _png(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "avatars.githubusercontent.com"
    return httpx.Response(200, content=PNG)


class FakeUsers:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def installation_token(self, installation_id: int, **scope: Any) -> str:
        assert scope == {"permissions": {"metadata": "read"}}
        return "token"

    async def get_user(self, token: Any, login: str) -> dict[str, Any]:
        self.calls.append(login)
        return {"id": 1000 + len(self.calls), "type": "User"}


class PerRepoGitHub(FakeGitHub):
    """Commits per repository name; every read is recorded."""

    def __init__(self, by_repo: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__([c for commits in by_repo.values() for c in commits])
        self.by_repo = by_repo
        self.listed: list[str] = []

    async def list_commits_page(self, token: str, owner: str, repo: str, **kw: Any) -> list[dict]:
        self.listed.append(f"{owner}/{repo}")
        return self.by_repo.get(repo, []) if kw["page"] == 1 else []


async def seed_org_repository(
    engine: AsyncEngine,
    org: str,
    name: str,
    *,
    archived: bool = False,
    gate: str | None = None,
) -> int:
    """A repository of `org` the App can read, under its own installation."""
    async with engine.begin() as connection:
        scope = (
            await connection.execute(
                text(
                    "SELECT id FROM scopes WHERE scope_type = 'organization' "
                    "AND external_ref = 'root'"
                )
            )
        ).scalar_one()
        external = int(uuid.uuid4().int % 10**9)
        installation = (
            await connection.execute(
                text(
                    "INSERT INTO github_installations (external_id, scope_id, account_login, "
                    "account_type, state) VALUES (:e, :s, :o, 'Organization', 'active') "
                    "RETURNING id"
                ),
                {"e": external, "s": scope, "o": org},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO github_repositories (installation_id, scope_id, external_id, "
                "full_name, owner_login, name, default_branch, archived, security_gate) "
                "VALUES (:i, :s, :e, :f, :o, :n, 'main', :a, :g)"
            ),
            {
                "i": installation,
                "s": scope,
                "e": external + 1,
                "f": f"{org}/{name}",
                "o": org,
                "n": name,
                "a": archived,
                "g": gate,
            },
        )
    return external + 1


class OrgStore(FakeStore):
    """Claude Code rows that name their repository through vcs.* attributes."""

    def __init__(self, org: str, repo: str) -> None:
        super().__init__("")
        self.org, self.repo = org, repo

    async def query(self, statement: str, parameters: dict[str, str], **_: Any) -> Result:
        if "otel_metrics_sum" in statement or "AS kind" in statement:
            return Result([])
        day = NOW.date().isoformat()
        base = {
            "day": day,
            "project_tag": "",
            "vcs": "",
            "vcs_url": "",
            "person_hint": "",
            "api_errors": 0,
            "prompts": 3,
            "sessions": 1,
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "edits_accepted": 0,
            "edits_rejected": 0,
        }
        return Result(
            [
                {
                    **base,
                    "vcs_owner": self.org,
                    "vcs_name": self.repo,
                    "identity": "acct-org",
                    # Seen only through this org repository's usage.
                    "person_hint": "orgonly",
                    "api_requests": 7,
                    "cost": 3.25,
                },
                # Another owner's repository: never kept.
                {
                    **base,
                    "vcs_owner": "someone-else",
                    "vcs_name": "side-project",
                    "identity": "acct-side",
                    "api_requests": 2,
                    "cost": 1.0,
                },
            ]
        )


async def test_every_repository_of_a_watched_org_is_on_the_map(
    engine: AsyncEngine, redis: Any
) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM dev_agent_days"))
        await connection.execute(text("DELETE FROM dev_agent_breakdown"))
        await connection.execute(text("DELETE FROM dev_commits WHERE project_id IS NULL"))
    org = f"orgmap-{uuid.uuid4().hex[:8]}"
    world = await make_world(engine)
    _, service, catalog_repo = await seed_repository(engine, world)
    loose = await seed_org_repository(engine, org, "zabake")
    archived = await seed_org_repository(engine, org, "old-thing", archived=True)
    gated = await seed_org_repository(engine, org, "closed", gate="manual_review")
    elsewhere = await seed_org_repository(engine, f"x{org}", "not-watched")
    github = PerRepoGitHub(
        {
            catalog_repo: [
                commit("1" * 40, "eneserman", "Enes", "feat: catalog", [f"{service}/a.py"], 2)
            ],
            "zabake": [
                commit("2" * 40, "eneserman", "Enes", f"feat: zabake{CLAUDE}", ["src/a.py"], 3),
                commit("3" * 40, "eneserman", "Enes", "fix: zabake", ["src/b.py"], 4),
            ],
            "old-thing": [commit("4" * 40, "x", "X", "old", ["a"], 1)],
            "closed": [commit("5" * 40, "x", "X", "closed", ["a"], 1)],
            "not-watched": [commit("6" * 40, "x", "X", "other", ["a"], 1)],
        }
    )
    report = await collector.run_once(
        engine,
        redis,
        source=github,
        store=OrgStore(org, "zabake"),
        now=NOW,
        budget=50,
        orgs=[org.upper()],  # GitHub logins are case-insensitive
    )
    assert report.commits_added == 3, report
    # Archived, gated and unwatched repositories are never read, nor is a
    # token ever scoped to them.
    assert sorted(github.listed) == sorted([f"acme/{catalog_repo}", f"{org}/zabake"])
    scoped = {i for s in github.scopes for i in s["repository_ids"]}
    assert loose in scoped and not scoped & {archived, gated, elsewhere}

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT sha, project_id, services, unmapped_files FROM dev_commits "
                    "WHERE repository_external_id = :e ORDER BY sha"
                ),
                {"e": loose},
            )
        ).all()
        usage = (
            await connection.execute(
                text("SELECT repository, repository_external_id, project_id FROM dev_agent_days")
            )
        ).all()
    assert [(r[0][0], r[1], list(r[2]), r[3]) for r in rows] == [
        ("2", None, [], 0),
        ("3", None, [], 0),
    ]
    assert [tuple(u) for u in usage] == [(f"{org}/zabake", loose, None)]

    harness = build_harness(require_it_settings().model_copy(update={"dev_activity_orgs": [org]}))
    faces = FakeUsers()
    harness.app.state.dev_avatars = Avatars(faces, transport=httpx.MockTransport(_png))
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-orgviewer", user_type("user-orgviewer", "Viewer", "user-orgviewer@example.test")
    )
    await login_all(harness, ["user-orgviewer"])
    await make_role(harness, engine, "Org dev viewer", ["agent.view", "project.view"])
    async with engine.connect() as connection:
        scope_ref = (
            await connection.execute(
                text(
                    "SELECT s.external_ref FROM projects p JOIN scopes s ON s.id = p.scope_id "
                    "WHERE p.id = :p"
                ),
                {"p": world["project_id"]},
            )
        ).scalar_one()
    await grant(engine, harness, "user-orgviewer", "Org dev viewer", "project", scope_ref)
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        body = (await client.get("/v1/agents/dev/map?window=7d")).json()
        repos = {r["id"]: r for r in body["repositories"]}
        node = repos[f"gh:{loose}"]
        assert node["catalogued"] is False and node["project_id"] is None
        assert (node["full_name"], node["name"]) == (f"{org}/zabake", "zabake")
        assert node["commits"] == 2 and node["agent_commits"] == 1
        assert node["cost_usd"] == 3.25 and node["services"] == 0
        catalogued = next(r for r in body["repositories"] if r["catalogued"])
        assert catalogued["project_id"] == str(world["project_id"])
        # The catalog's first; services only under a catalogued repository.
        assert body["repositories"][0]["catalogued"]
        assert {s["repository"] for s in body["services"]} == {catalogued["id"]}
        assert body["summary"]["repositories"] == 2 and body["summary"]["catalogued"] == 1
        assert body["configured"]["organisations"] == [org]
        assert body["configured"]["repositories"] == 2
        enes = next(p for p in body["people"] if p["key"] == "eneserman")
        assert sorted(enes["repositories"]) == sorted([catalogued["id"], node["id"]])
        assert any(
            e["repository"] == node["id"] and e["agent"] == "claude_code" and e["commits"] == 1
            for e in body["edges"]
        )
        assert not any("someone-else" in r["full_name"] for r in body["repositories"])
        path = f"/v1/agents/dev/commits?window=7d&repository=gh:{loose}"
        listed = (await client.get(path)).json()
        assert [c["short"] for c in listed["items"]] == ["2222222", "3333333"]
        assert listed["items"][0]["project_id"] is None
        assert listed["items"][0]["repository_id"] == f"gh:{loose}"
        by_name = (
            await client.get(f"/v1/agents/dev/commits?window=7d&repository=repo:{org}/zabake")
        ).json()
        assert by_name["total"] == 2
        bad = await client.get("/v1/agents/dev/commits?repository=gh:1;drop")
        assert bad.status_code == 422

        # Faces come from Tracesis, which asks GitHub; scoped like the map.
        face = await client.get("/v1/agents/dev/people/eneserman/avatar")
        assert face.status_code == 200 and face.headers["content-type"] == "image/png"
        assert face.content == PNG and "max-age=86400" in face.headers["cache-control"]
        assert face.headers["x-content-type-options"] == "nosniff"
        assert (await client.get("/v1/agents/dev/people/orgonly/avatar")).status_code == 200
        for nobody in ("name:enes", "claude:abc", "ghost"):
            none = await client.get(f"/v1/agents/dev/people/{nobody}/avatar")
            assert none.status_code == 204, nobody
        assert sorted(faces.calls) == ["eneserman", "orgonly"]

    # Project scope: the catalog's repository only, never the org's others.
    async with harness.api_client() as client:
        await harness.login(client, "user-orgviewer")
        body = (await client.get("/v1/agents/dev/map?window=7d")).json()
        assert [r["catalogued"] for r in body["repositories"]] == [True]
        assert body["configured"]["organisations"] == []
        assert body["configured"]["repositories"] == 1
        enes = next(p for p in body["people"] if p["key"] == "eneserman")
        assert enes["repositories"] == [body["repositories"][0]["id"]]
        listed = (await client.get(path)).json()
        assert listed["items"] == []
        everything = (await client.get("/v1/agents/dev/commits?window=7d")).json()
        assert all(c["project_id"] is not None for c in everything["items"])
        assert (await client.get("/v1/agents/dev/people/eneserman/avatar")).status_code == 200
        # Seen only in a repository outside the catalog: not this viewer's to see.
        assert (await client.get("/v1/agents/dev/people/orgonly/avatar")).status_code == 204

    # Catalogued later: the rows already kept follow the repository.
    other = await make_world(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE projects SET repo_owner = :o, repo_name = 'zabake' WHERE id = :p"),
            {"o": org, "p": other["project_id"]},
        )
    await collector.run_once(
        engine, redis, source=github, store=None, now=NOW, budget=50, orgs=[org]
    )
    async with engine.connect() as connection:
        owners = {
            r[0]
            for r in (
                await connection.execute(
                    text("SELECT project_id FROM dev_commits WHERE repository_external_id = :e"),
                    {"e": loose},
                )
            ).all()
        }
    assert owners == {other["project_id"]}
