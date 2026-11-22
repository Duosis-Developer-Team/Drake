"""A developer's past Claude Code usage, and clones that still use a repository's
old name: renamed repositories are resolved once through GitHub, imported
history is kept apart from telemetry, and the export carries counts only."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from drake_api.dev_activity.collector import (
    Project,
    Renames,
    Repository,
    Resolver,
    _safe_identity,
)
from drake_api.dev_activity.repository import People
from drake_api.dev_activity.usage_import import ImportRefusedError, parse
from drake_api.github_app.auth import GitHubAppAuth, InstallationToken
from drake_api.github_app.client import GitHubClient
from drake_api.testing import make_settings

ORG = "Duosis-Developer-Team"
TRACESIS_ID = 1324492653
REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolver(orgs: tuple[str, ...] = (ORG,)) -> tuple[Resolver, list[Project]]:
    projects = [
        Project(uuid.uuid4(), "tracesis", f"{ORG}/Tracesis"),
        Project(uuid.uuid4(), "hermes", f"{ORG}/Hermes"),
    ]
    known = {f"{ORG}/tracesis".lower(): TRACESIS_ID, f"{ORG}/hermes".lower(): 11}
    return Resolver(projects, known, orgs), projects


class FakeGitHub:
    """repository_id answers by name, as GitHub would after a rename."""

    def __init__(self, moved: dict[str, int]) -> None:
        self.moved = moved
        self.asked: list[str] = []
        self.tokens: list[dict[str, Any]] = []

    async def installation_token(self, installation_id: int, **kwargs: Any) -> str:
        self.tokens.append({"installation": installation_id, **kwargs})
        return "token"

    async def repository_id(self, token: Any, owner: str, repo: str) -> int | None:
        self.asked.append(f"{owner}/{repo}")
        return self.moved.get(f"{owner}/{repo}")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _renames(source: FakeGitHub, clock: Clock | None = None) -> Renames:
    renames = Renames(source, clock=clock or Clock())

    async def installation(engine: Any, owner: str) -> int | None:
        return 77 if owner == ORG.lower() else None

    renames._installation = installation  # type: ignore[method-assign]
    return renames


async def test_an_old_repository_name_lands_on_the_repository_it_became() -> None:
    resolver, projects = _resolver()
    github = FakeGitHub({f"{ORG.lower()}/drake": TRACESIS_ID})
    renames = _renames(github)
    old = {"owner": ORG.lower(), "name": "drake", "url": f"https://github.com/{ORG.lower()}/drake"}
    # Before: an org repository nobody knows, with no project.
    assert resolver(**old) == Repository(f"{ORG.lower()}/drake", None, None)
    assert resolver.unknown(**old) == f"{ORG.lower()}/drake"
    await renames.teach(None, resolver, [resolver.unknown(**old)])  # type: ignore[arg-type]
    assert resolver(**old) == Repository(f"{ORG}/tracesis".lower(), TRACESIS_ID, projects[0].id)
    # Asked with a token narrowed to metadata, through the owner's installation.
    assert github.tokens == [{"installation": 77, "permissions": {"metadata": "read"}}]
    # Learned: the name is no longer unknown, and GitHub is not asked again.
    assert resolver.unknown(**old) is None
    # A later cycle builds a new resolver; the answer is remembered.
    fresh, fresh_projects = _resolver()
    await renames.teach(None, fresh, [f"{ORG.lower()}/drake"])  # type: ignore[arg-type]
    assert github.asked == [f"{ORG.lower()}/drake"]
    landed = fresh(**old)
    assert landed is not None and landed.project_id == fresh_projects[0].id


async def test_an_unknown_name_stays_unresolved_and_is_asked_about_hourly() -> None:
    resolver, _ = _resolver()
    github = FakeGitHub({})
    clock = Clock()
    renames = _renames(github, clock)
    name = f"{ORG.lower()}/nothing-here"
    await renames.teach(None, resolver, [name])  # type: ignore[arg-type]
    assert resolver(owner=ORG, name="nothing-here") == Repository(name, None, None)
    await renames.teach(None, resolver, [name])  # type: ignore[arg-type]
    assert github.asked == [name]
    clock.now += Renames.MISSING_SECONDS + 1
    await renames.teach(None, resolver, [name])  # type: ignore[arg-type]
    assert github.asked == [name, name]


async def test_known_foreign_and_gated_names_are_never_asked_about() -> None:
    resolver, _ = _resolver()
    # Known names, and owners nobody watches, are not "unknown".
    assert resolver.unknown(owner=ORG, name="Tracesis") is None
    assert resolver.unknown(owner="someone", name="side-project") is None
    github = FakeGitHub({})
    renames = _renames(github)
    await renames.teach(None, resolver, [f"{ORG}/Gated-Fixture".lower()])  # type: ignore[arg-type]
    assert github.asked == []


async def test_a_cycle_asks_about_a_bounded_number_of_names() -> None:
    resolver, _ = _resolver()
    github = FakeGitHub({})
    renames = _renames(github)
    names = [f"{ORG.lower()}/repo-{i}" for i in range(Renames.MAX_LOOKUPS + 5)]
    await renames.teach(None, resolver, names)  # type: ignore[arg-type]
    assert len(github.asked) == Renames.MAX_LOOKUPS


async def test_a_failed_lookup_is_not_remembered() -> None:
    resolver, _ = _resolver()

    class Broken(FakeGitHub):
        async def repository_id(self, token: Any, owner: str, repo: str) -> int | None:
            self.asked.append(f"{owner}/{repo}")
            raise RuntimeError("github is unreachable")

    github = Broken({})
    renames = _renames(github)
    name = f"{ORG.lower()}/drake"
    await renames.teach(None, resolver, [name])  # type: ignore[arg-type]
    await renames.teach(None, resolver, [name])  # type: ignore[arg-type]
    assert github.asked == [name, name]


def _client(handler: Any) -> GitHubClient:
    settings = make_settings().model_copy(
        update={
            "github_app_enabled": True,
            "github_app_client_id": "Iv1.localtest",
            "github_api_base_url": "http://127.0.0.1:59097",
        }
    )
    return GitHubClient(settings, GitHubAppAuth(settings), transport=httpx.MockTransport(handler))


def _token() -> InstallationToken:
    return InstallationToken(
        token="ghs_" + "a" * 80,
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        permissions={"metadata": "read"},
    )


async def test_github_moved_a_repository_its_id_is_read_without_following_the_redirect() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/repos/Duosis-Developer-Team/drake":
            return httpx.Response(
                301,
                headers={"Location": f"https://api.github.com/repositories/{TRACESIS_ID}"},
                json={
                    "message": "Moved Permanently",
                    "url": f"https://api.github.com/repositories/{TRACESIS_ID}",
                },
            )
        if request.url.path == "/repos/Duosis-Developer-Team/Tracesis":
            return httpx.Response(200, json={"id": TRACESIS_ID, "full_name": f"{ORG}/Tracesis"})
        return httpx.Response(404, json={"message": "Not Found"})

    client = _client(handler)
    assert await client.repository_id(_token(), ORG, "drake") == TRACESIS_ID
    assert await client.repository_id(_token(), ORG, "Tracesis") == TRACESIS_ID
    assert await client.repository_id(_token(), ORG, "nothing") is None
    # The redirect was read, never followed.
    assert f"/repositories/{TRACESIS_ID}" not in seen


def test_imported_history_is_the_same_person_as_live_telemetry() -> None:
    account = "2469ab4d-2374-4e41-9451-e2735cb0c11c"
    people = People([("claude", account, "gencay", "Gencay")])
    assert people.of_claude(account, None) == ("gencay", True)
    assert people.of_claude(f"backfill:{account}", None) == ("gencay", True)
    nobody = People([])
    assert nobody.of_claude(f"backfill:{account}", "eneserman") == ("eneserman", True)
    # No hint and no alias: one claude node for both, not two.
    assert nobody.of_claude(f"backfill:{account}", None) == nobody.of_claude(account, None)


def test_telemetry_can_never_write_an_imported_identity() -> None:
    assert _safe_identity("backfill:abc") is None
    assert _safe_identity("BACKFILL:abc") is None
    assert _safe_identity("2469ab4d") == "2469ab4d"


def _export(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format": "tracesis.claude-code-usage.v1",
        "login": "eneserman",
        "account_uuid": "8e56f0a3-0000-4000-8000-000000000000",
        "days": [
            {
                "day": "2026-11-20",
                "repository": "duosis-developer-team/drake",
                "cost_usd": 12.5,
                "input_tokens": 10,
                "output_tokens": 2000,
                "cache_read_tokens": 900000,
                "cache_creation_tokens": 30000,
                "api_requests": 40,
                "prompts": 3,
                "sessions": 1,
                "edits_accepted": 7,
            }
        ],
        "breakdown": [
            {
                "day": "2026-11-20",
                "repository": "duosis-developer-team/drake",
                "kind": "model",
                "name": "claude-opus-5-5",
                "calls": 40,
                "failures": 0,
                "cost_usd": 12.5,
                "tokens": 932010,
            }
        ],
    }
    data.update(overrides)
    return data


def test_an_export_is_read_whole_or_not_at_all() -> None:
    plan = parse(json.dumps(_export()))
    assert plan.identity == "backfill:8e56f0a3-0000-4000-8000-000000000000"
    assert plan.login == "eneserman" and len(plan.days) == 1 and len(plan.breakdown) == 1
    assert (
        parse(json.dumps(_export(account_uuid=None)), "8E56F0A3-1111").identity
        == "backfill:8e56f0a3-1111"
    )
    refusals = [
        json.dumps(_export(format="something-else")),
        json.dumps(_export(login="not a login")),
        json.dumps(_export(account_uuid=None)),
        json.dumps(_export(days=[{**_export()["days"][0], "api_requests": -1}])),
        json.dumps(_export(days=[{**_export()["days"][0], "repository": "../etc"}])),
        json.dumps(_export(days=_export()["days"] * 2)),
        json.dumps(_export(breakdown=[{**_export()["breakdown"][0], "kind": "prompt"}])),
        json.dumps(_export(login="someone@example.com")),
        "not json",
    ]
    for raw in refusals:
        with pytest.raises(ImportRefusedError):
            parse(raw)


# --------------------------------------------------------------------------
# the developer-side export
# --------------------------------------------------------------------------


def _load_export() -> Any:
    spec = importlib.util.spec_from_file_location(
        "export_claude_code_usage", REPO_ROOT / "scripts" / "export-claude-code-usage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkout(path: Path, origin: str) -> None:
    path.mkdir(parents=True)
    # A throwaway checkout for the export to recognise; fixed arguments.
    subprocess.run(["git", "init", "-q", str(path)], check=True)  # noqa: S603, S607
    subprocess.run(  # noqa: S603
        ["git", "-C", str(path), "remote", "add", "origin", origin],  # noqa: S607
        check=True,
    )


def _line(**record: Any) -> str:
    return json.dumps(record) + "\n"


def _assistant(
    ts: str,
    cwd: str,
    sid: str,
    mid: str,
    out: int,
    content: list[Any],
    model: str = "claude-opus-5-5",
) -> str:
    usage = {
        "input_tokens": 100,
        "output_tokens": out,
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 10_000,
        "cache_creation": {"ephemeral_1h_input_tokens": 10_000, "ephemeral_5m_input_tokens": 0},
    }
    return _line(
        type="assistant",
        timestamp=ts,
        cwd=cwd,
        sessionId=sid,
        requestId=f"req-{mid}",
        message={"id": mid, "model": model, "usage": usage, "content": content},
    )


def test_the_export_counts_what_tracesis_shows_and_nothing_else(
    tmp_path: Path, capsys: Any
) -> None:
    exporter = _load_export()
    home = tmp_path / "home"
    work = tmp_path / "work"
    _checkout(work / "drake", "git@github.com:Duosis-Developer-Team/drake.git")
    _checkout(work / "mine", "https://github.com/someone/side-project.git")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    projects = home / ".claude" / "projects" / "-work-drake"
    (projects / "s1" / "subagents").mkdir(parents=True)
    drake, sid = str(work / "drake"), "s1"
    secret = "PROMPT TEXT THAT MUST NEVER LEAVE"
    (projects / "s1.jsonl").write_text(
        _line(
            type="user",
            timestamp="2026-11-20T09:00:00Z",
            cwd=drake,
            sessionId=sid,
            promptId="p1",
            uuid="u1",
            message={"content": secret},
        )
        # One API response streamed as two lines: counted once, at its largest.
        + _assistant(
            "2026-11-20T09:00:05Z", drake, sid, "m1", 10, [{"type": "text", "text": secret}]
        )
        + _assistant(
            "2026-11-20T09:00:06Z",
            drake,
            sid,
            "m1",
            500,
            [{"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file": secret}}],
        )
        + _line(
            type="user",
            timestamp="2026-11-20T09:00:07Z",
            cwd=drake,
            sessionId=sid,
            uuid="u2",
            message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": secret}]},
        )
        # The shell wandered off to a scratch directory: still this session's.
        + _assistant(
            "2026-11-20T09:01:00Z",
            str(scratch),
            sid,
            "m2",
            200,
            [{"type": "tool_use", "id": "t2", "name": "Bash"}],
        )
        + _line(
            type="user",
            timestamp="2026-11-20T09:01:01Z",
            cwd=str(scratch),
            sessionId=sid,
            uuid="u3",
            message={"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": True}]},
        )
        + _assistant("2026-11-20T09:02:00Z", drake, sid, "m3", 0, [], model="<synthetic>")
        + _line(
            type="user",
            timestamp="2026-11-20T09:03:00Z",
            cwd=drake,
            sessionId=sid,
            isMeta=True,
            promptId="p2",
            uuid="u4",
            message={"content": "meta"},
        )
        # Outside the window.
        + _assistant("2026-09-01T09:00:00Z", drake, sid, "m0", 999, [])
    )
    (projects / "s1" / "subagents" / "agent-1.jsonl").write_text(
        _assistant(
            "2026-11-21T10:00:00Z",
            str(tmp_path / "gone" / "worktree"),
            sid,
            "m4",
            50,
            [{"type": "tool_use", "id": "t3", "name": "mcp__github__get_issue"}],
        )
        + _line(
            type="user",
            timestamp="2026-11-21T10:00:01Z",
            cwd=drake,
            sessionId=sid,
            isSidechain=True,
            promptId="p9",
            uuid="u9",
            message={"content": "sidechain prompt"},
        )
        + _line(
            type="user",
            timestamp="2026-11-21T10:00:02Z",
            cwd=drake,
            sessionId=sid,
            uuid="u10",
            message={"content": [{"type": "tool_result", "tool_use_id": "t3"}]},
        )
    )
    other = home / ".claude" / "projects" / "-work-mine"
    other.mkdir(parents=True)
    (other / "s2.jsonl").write_text(
        _assistant("2026-11-20T11:00:00Z", str(work / "mine"), "s2", "m9", 100, [])
    )
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {
                    "accountUuid": "8e56f0a3-0000-4000-8000-000000000000",
                    "emailAddress": "e@x.test",
                }
            }
        )
    )

    assert (
        exporter.main(
            ["--login", "eneserman", "--until", "2026-11-22T00:00:00Z", "--home", str(home)]
        )
        == 0
    )
    out, err = capsys.readouterr()
    assert secret not in out and "@" not in out and str(tmp_path) not in out
    data = json.loads(out)
    assert data["format"] == "tracesis.claude-code-usage.v1"
    assert data["account_uuid"] == "8e56f0a3-0000-4000-8000-000000000000"
    days = {(d["day"], d["repository"]): d for d in data["days"]}
    assert set(days) == {
        ("2026-11-20", "duosis-developer-team/drake"),
        ("2026-11-21", "duosis-developer-team/drake"),
    }
    first = days[("2026-11-20", "duosis-developer-team/drake")]
    assert first["api_requests"] == 2  # m1 once, m2; never <synthetic>
    assert first["output_tokens"] == 700
    assert first["prompts"] == 1 and first["sessions"] == 1 and first["edits_accepted"] == 1
    # Opus 5.5: 100x4 + 1Mx0.20 + 10kx8 (1h write) per Mtok, plus output at 20.
    per_message = (100 * 4 + 1_000_000 * 0.20 + 10_000 * 8) / 1e6
    assert first["cost_usd"] == pytest.approx(2 * per_message + 700 * 20 / 1e6)
    tools = {(b["day"], b["kind"], b["name"]): b for b in data["breakdown"]}
    assert tools[("2026-11-20", "tool", "Bash")]["failures"] == 1
    assert tools[("2026-11-21", "mcp", "mcp__github__get_issue")]["calls"] == 1
    assert tools[("2026-11-20", "model", "claude-opus-5-5")]["calls"] == 2
    assert days[("2026-11-21", "duosis-developer-team/drake")]["prompts"] == 0
    assert data["unpriced_models"] == []
    assert "eneserman" in err and "$" in err


def test_the_export_stops_where_telemetry_starts(tmp_path: Path) -> None:
    exporter = _load_export()
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(
        json.dumps({"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}})
    )
    (claude / "settings.json.bak-20261122154500").write_text(json.dumps({"env": {}}))
    (claude / "settings.json.bak-20261123090000").write_text(
        json.dumps({"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}})
    )
    started = exporter.telemetry_start(str(tmp_path))
    assert started is not None
    assert started == dt.datetime(2026, 11, 22, 15, 45).astimezone(dt.UTC)
    (claude / "settings.json").write_text(json.dumps({"env": {}}))
    assert exporter.telemetry_start(str(tmp_path)) is None
