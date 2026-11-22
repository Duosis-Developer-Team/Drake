"""The development office against the real database.

The office is the map's people and repositories as places, who is at work
right now (from the collector's presence read, a double here) and one day's
commits and deliveries. What is under test: the day's bounds, who carries a
delivery, that bots stay out, and that everything is scoped like the map.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from drake_api.dev_activity import collector
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, login_all, make_role
from test_dev_activity_integration import seed_repository
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

NOW = datetime.now(UTC).replace(microsecond=0)
DAY = datetime.combine(NOW.date(), datetime.min.time(), tzinfo=UTC)


class FakeLoop:
    reads_commits = True
    reads_usage = True

    def __init__(self, presence: list[collector.Presence]) -> None:
        self._presence = presence
        self.calls = 0

    async def presence(self, now: datetime | None = None) -> list[collector.Presence]:
        self.calls += 1
        return self._presence


async def _repository(engine: AsyncEngine, project_id: Any) -> tuple[str, int]:
    async with engine.connect() as connection:
        full, external = (
            await connection.execute(
                text(
                    "SELECT r.full_name, r.external_id FROM github_repositories r "
                    "JOIN projects p ON lower(r.full_name) = lower(p.repo_owner || '/' || "
                    "p.repo_name) WHERE p.id = :p"
                ),
                {"p": project_id},
            )
        ).one()
    return str(full), int(external)


async def _commit(
    engine: AsyncEngine,
    *,
    project_id: Any,
    full: str,
    external: int,
    sha: str,
    login: str | None,
    name: str,
    agent: str,
    at: datetime,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO dev_commits (project_id, sha, repository, repository_external_id, "
                "authored_at, author_login, author_name, agent, title) "
                "VALUES (:p, :sha, :full, :ext, :at, :login, :name, :agent, :title)"
            ),
            {
                "p": project_id,
                "sha": sha,
                "full": full,
                "ext": external,
                "at": at,
                "login": login,
                "name": name,
                "agent": agent,
                "title": f"feat: {sha[:4]}",
            },
        )


async def _rollout(engine: AsyncEngine, world: dict[str, Any], sha: str, at: datetime) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO deployment_revisions (cluster_id, namespace, workload_kind, "
                "workload_name, workload_uid, revision, project_id, environment_id, "
                "commit_sha, rollout_started_at, last_seen_at) "
                "VALUES (:c, 'ns', 'Deployment', :w, :uid, 1, :p, :e, :sha, :at, :at)"
            ),
            {
                "c": world["cluster_id"],
                "w": f"w-{uuid.uuid4().hex[:6]}",
                "uid": str(uuid.uuid4()),
                "p": world["project_id"],
                "e": world["environment_id"],
                "sha": sha,
                "at": at,
            },
        )


async def test_the_office_is_the_map_as_places_and_a_day_to_play(engine: AsyncEngine) -> None:
    world = await make_world(engine)
    other = await make_world(engine)
    await seed_repository(engine, world)
    await seed_repository(engine, other)
    full, external = await _repository(engine, world["project_id"])
    other_full, other_external = await _repository(engine, other["project_id"])
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM dev_person_aliases"))

    mine = {"project_id": world["project_id"], "full": full, "external": external}
    a, b = "a" * 40, "b" * 40
    await _commit(engine, **mine, sha=a, login="gencaycoskuntr", name="Gencay",
                  agent="claude_code", at=DAY + timedelta(hours=2))  # fmt: skip
    await _commit(engine, **mine, sha=b, login="eneserman", name="Enes", agent="none",
                  at=DAY + timedelta(hours=3))  # fmt: skip
    await _commit(engine, **mine, sha="c" * 40, login="dependabot[bot]", name="dependabot",
                  agent="none", at=DAY + timedelta(hours=4))  # fmt: skip
    # The day before: on the map (desks), not among the day's events.
    await _commit(engine, **mine, sha="d" * 40, login="eneserman", name="Enes", agent="none",
                  at=DAY - timedelta(hours=3))  # fmt: skip
    await _commit(engine, project_id=other["project_id"], full=other_full,
                  external=other_external, sha="e" * 40, login="someone", name="Someone",
                  agent="claude_code", at=DAY + timedelta(hours=1))  # fmt: skip
    # Two workloads of one delivery, minutes apart: one box to carry.
    await _rollout(engine, world, a, DAY + timedelta(hours=5))
    await _rollout(engine, world, a, DAY + timedelta(hours=5, minutes=3))
    await _rollout(engine, other, "e" * 40, DAY + timedelta(hours=6))

    loop = FakeLoop(
        [
            collector.Presence(
                identity="acct-gencay",
                person_hint="gencaycoskuntr",
                repository=collector.Repository(full.lower(), external, world["project_id"]),
                last_seen=NOW - timedelta(minutes=2),
            ),
            collector.Presence(
                identity="acct-someone",
                person_hint="someone",
                repository=collector.Repository(
                    other_full.lower(), other_external, other["project_id"]
                ),
                last_seen=NOW - timedelta(minutes=4),
            ),
        ]
    )
    harness = build_harness(require_it_settings())
    harness.app.state.dev_activity = loop
    start = DAY.isoformat()
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        reply = await client.get("/v1/agents/dev/office", params={"day_start": start})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        rooms = {r["id"]: r for r in body["rooms"]}
        room = rooms[f"gh:{external}"]
        assert room["catalogued"] and room["project_id"] == str(world["project_id"])
        # A desk for everyone who worked there, the busiest first; no bots.
        assert set(room["desks"]) == {"gencaycoskuntr", "eneserman"}
        assert all("[bot]" not in d for r in body["rooms"] for d in r["desks"])
        people = {p["key"]: p for p in body["people"]}
        assert people["gencaycoskuntr"]["portrait"] is False
        assert "dependabot[bot]" not in people
        live = {v["person"]: v for v in body["live"]}
        assert live["gencaycoskuntr"]["repository"] == room["id"]
        mine_events = [e for e in body["events"] if e["repository"] == room["id"]]
        assert [(e["kind"], e.get("short")) for e in mine_events] == [
            ("commit", "aaaaaaa"),
            ("commit", "bbbbbbb"),
            ("deployment", "aaaaaaa"),
        ]
        delivery = mine_events[-1]
        # The deployed commit's author carries it.
        assert delivery["person"] == "gencaycoskuntr" and delivery["environment"] == "dev"
        assert [e["at"] for e in body["events"]] == sorted(e["at"] for e in body["events"])
        # The cluster at the office's centre, and how its workloads are doing.
        cluster = body["cluster"]
        assert cluster is not None and cluster["name"] and cluster["ref"]
        assert 0 <= cluster["healthy"] <= cluster["workloads"]
        assert room["commits"] >= 2

        # The day is bounded: an offset is required, and 90 days is the reach.
        naive = await client.get(
            "/v1/agents/dev/office", params={"day_start": "2026-01-01T00:00:00"}
        )
        assert naive.status_code == 422
        old = (DAY - timedelta(days=120)).isoformat()
        assert (
            await client.get("/v1/agents/dev/office", params={"day_start": old})
        ).status_code == 422
        # The viewer's local midnight: a day that starts three hours earlier.
        local = (DAY - timedelta(hours=3)).astimezone().isoformat()
        assert (
            await client.get("/v1/agents/dev/office", params={"day_start": local})
        ).status_code == 200

    # Scoped like the map: a viewer of the other project sees nothing of this one.
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-officeviewer", user_type("user-officeviewer", "Viewer", "v@example.test")
    )
    await login_all(harness, ["user-officeviewer"])
    await make_role(harness, engine, "Office viewer", ["agent.view", "project.view"])
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
    await grant(engine, harness, "user-officeviewer", "Office viewer", "project", scope_ref)
    async with harness.api_client() as client:
        await harness.login(client, "user-officeviewer")
        body = (await client.get("/v1/agents/dev/office", params={"day_start": start})).json()
        assert f"gh:{external}" not in {r["id"] for r in body["rooms"]}
        assert f"gh:{other_external}" in {r["id"] for r in body["rooms"]}
        assert {v["person"] for v in body["live"]} == {"someone"}
        assert all(e["repository"] == f"gh:{other_external}" for e in body["events"])
        assert "gencaycoskuntr" not in {p["key"] for p in body["people"]}
        assert any(e["kind"] == "deployment" for e in body["events"])
        # Without cluster.view, no cluster is named.
        assert body["cluster"] is None
