"""A developer's imported Claude Code history against the real database.

An export names repositories the way the developer's clone does, which can
be a name GitHub has since moved; the import lands it on the repository it
became, under `backfill:<account>`, and the telemetry harvest never touches
those rows.
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from drake_api.dev_activity import collector
from drake_api.dev_activity.usage_import import apply, parse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_dev_activity_integration import NOW, FakeStore, redis, seed_repository
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db", "redis"]

ACCOUNT = "8e56f0a3-1111-4222-8333-444455556666"


class MovedRepositories:
    """GitHub, as far as renames go: an old name answers with the new id."""

    def __init__(self, moved: dict[str, int]) -> None:
        self.moved = moved
        self.asked: list[str] = []

    async def installation_token(self, installation_id: int, **_: Any) -> str:
        return "token"

    async def repository_id(self, token: Any, owner: str, repo: str) -> int | None:
        self.asked.append(f"{owner}/{repo}".lower())
        return self.moved.get(f"{owner}/{repo}".lower())


async def _world(engine: AsyncEngine) -> tuple[dict[str, Any], str, str, int]:
    world = await make_world(engine)
    key, _service, _name = await seed_repository(engine, world)
    async with engine.connect() as connection:
        owner, name = (
            await connection.execute(
                text("SELECT repo_owner, repo_name FROM projects WHERE id = :p"),
                {"p": world["project_id"]},
            )
        ).one()
        external = (
            await connection.execute(
                text("SELECT external_id FROM github_repositories WHERE full_name = :f"),
                {"f": f"{owner}/{name}"},
            )
        ).scalar_one()
    return world, key, f"{owner}/{name}".lower(), int(external)


def _export(repository: str, day: str, *, cost: float = 12.5, requests: int = 40) -> str:
    import json

    return json.dumps(
        {
            "format": "tracesis.claude-code-usage.v1",
            "login": "eneserman",
            "account_uuid": ACCOUNT,
            "days": [
                {
                    "day": day,
                    "repository": repository,
                    "cost_usd": cost,
                    "input_tokens": 10,
                    "output_tokens": 2000,
                    "cache_read_tokens": 900000,
                    "cache_creation_tokens": 30000,
                    "api_requests": requests,
                    "prompts": 3,
                    "sessions": 1,
                    "edits_accepted": 7,
                },
                {
                    "day": day,
                    "repository": "someone/side-project",
                    "cost_usd": 99,
                    "api_requests": 9,
                },
            ],
            "breakdown": [
                {
                    "day": day,
                    "repository": repository,
                    "kind": "model",
                    "name": "claude-opus-5-5",
                    "calls": requests,
                    "failures": 0,
                    "cost_usd": cost,
                    "tokens": 932010,
                },
                {
                    "day": day,
                    "repository": repository,
                    "kind": "mcp",
                    "name": "mcp__github__get_issue",
                    "calls": 2,
                    "failures": 1,
                    "cost_usd": 0,
                    "tokens": 0,
                },
            ],
        }
    )


async def _rows(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT day, repository, repository_external_id, project_id, person_hint, "
                    "cost_usd, api_requests FROM dev_agent_days WHERE identity = :i ORDER BY day"
                ),
                {"i": f"backfill:{ACCOUNT}"},
            )
        ).all()


async def test_an_old_name_lands_on_the_repository_and_a_reimport_replaces_itself(
    engine: AsyncEngine,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM dev_agent_days"))
        await connection.execute(text("DELETE FROM dev_agent_breakdown"))
    world, _key, full, external = await _world(engine)
    owner = full.partition("/")[0]
    old = f"{owner}/old-name-{uuid.uuid4().hex[:6]}"
    github = MovedRepositories({old: external})
    day = (NOW - timedelta(days=10)).date().isoformat()

    # A dry run resolves and reports, and writes nothing.
    dry = await apply(
        engine, parse(_export(old, day)), renames=collector.Renames(github), dry_run=True
    )
    assert dry.written_days == 1 and dry.landed[old] == full
    assert await _rows(engine) == []

    renames = collector.Renames(github)
    outcome = await apply(engine, parse(_export(old, day)), renames=renames)
    assert outcome.written_days == 1 and outcome.written_breakdown == 2
    assert outcome.skipped == {"someone/side-project": 1}
    rows = await _rows(engine)
    assert [(str(r[0]), r[1], r[2], r[3], r[4], float(r[5]), r[6]) for r in rows] == [
        (day, full, external, world["project_id"], "eneserman", 12.5, 40)
    ]
    # The same file again: the same rows, never twice. A corrected file
    # replaces them.
    await apply(engine, parse(_export(old, day)), renames=renames)
    await apply(engine, parse(_export(old, day, cost=20.0, requests=55)), renames=renames)
    rows = await _rows(engine)
    assert [(float(r[5]), r[6]) for r in rows] == [(20.0, 55)]
    # GitHub was asked once per process (the dry run had its own); the
    # answer was remembered across the three imports.
    assert github.asked == [old, old]
    async with engine.connect() as connection:
        kinds = (
            await connection.execute(
                text(
                    "SELECT kind, name, calls, failures FROM dev_agent_breakdown "
                    "WHERE identity = :i ORDER BY kind"
                ),
                {"i": f"backfill:{ACCOUNT}"},
            )
        ).all()
    assert [tuple(k) for k in kinds] == [
        ("mcp", "mcp__github__get_issue", 2, 1),
        ("model", "claude-opus-5-5", 55, 0),
    ]


async def test_the_harvest_never_replaces_imported_history(engine: AsyncEngine, redis: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM dev_agent_days"))
        await connection.execute(text("DELETE FROM dev_agent_breakdown"))
    _, key, full, _ = await _world(engine)
    today = NOW.date().isoformat()
    await apply(engine, parse(_export(full, today)))
    async with engine.begin() as connection:
        # A stale telemetry row of today: the harvest's to replace.
        await connection.execute(
            text(
                "INSERT INTO dev_agent_days (day, repository, identity, tool) "
                "VALUES (:d, :r, 'acct-stale', 'claude_code')"
            ),
            {"d": NOW.date(), "r": full},
        )
    report = await collector.run_once(
        engine, redis, source=None, store=FakeStore(key), now=NOW, budget=0
    )
    assert report.lease_acquired, report
    async with engine.connect() as connection:
        identities = sorted(
            r[0]
            for r in (await connection.execute(text("SELECT identity FROM dev_agent_days"))).all()
        )
        imported_tools = (
            await connection.execute(
                text("SELECT count(*) FROM dev_agent_breakdown WHERE identity = :i"),
                {"i": f"backfill:{ACCOUNT}"},
            )
        ).scalar_one()
    assert identities == ["acct-gencay", f"backfill:{ACCOUNT}"]
    assert imported_tools == 2
