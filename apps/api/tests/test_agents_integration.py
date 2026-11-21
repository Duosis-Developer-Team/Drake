"""The agent inventory against the real database (Agent Visibility, phase 2).

The trace store is a double answering the two discovery statements with
chosen rows; what is under test is who an agent becomes, what is kept for
it, and who may see it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis.asyncio as aioredis
from drake_api.ai_agents import discovery
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, login_all, make_role
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

NOW = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
HOUR = int(NOW.replace(minute=0).timestamp())


class Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows


class FakeStore:
    def __init__(self, rollups: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        self.rollups, self.tools = rollups, tools
        self.calls = 0

    async def query(self, statement: str, parameters: dict[str, str], **_: Any) -> Result:
        self.calls += 1
        assert "otel_traces" in statement
        start = datetime.fromisoformat(parameters["start"].replace(" ", "T")).replace(tzinfo=UTC)
        end = datetime.fromisoformat(parameters["end"].replace(" ", "T")).replace(tzinfo=UTC)
        source = self.tools if "gen_ai.tool.name'] AS tool" in statement else self.rollups
        return Result(
            [r for r in source if start <= datetime.fromtimestamp(r["hour"], tz=UTC) < end]
        )


@pytest.fixture
async def redis() -> Any:
    client = aioredis.from_url(require_it_settings().redis_url)
    await client.delete(discovery.LEASE_KEY)
    yield client
    await client.delete(discovery.LEASE_KEY)
    await client.aclose()


async def binding_place(engine: AsyncEngine, world: dict[str, Any]) -> tuple[str, str, str]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT c.cluster_ref, b.namespace, b.workload_name "
                    "FROM service_workload_bindings b JOIN clusters c ON c.id = b.cluster_id "
                    "WHERE b.id = :b"
                ),
                {"b": world["binding_id"]},
            )
        ).one()
    return str(row[0]), str(row[1]), str(row[2])


def usage(**over: Any) -> dict[str, Any]:
    base = {
        "svc": "",
        "cluster": "",
        "ns": "",
        "wk": "",
        "agent_id": "",
        "agent_name": "",
        "ingest": "",
        "ebpf": 0,
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "hour": HOUR,
        "invocations": 0,
        "inference_calls": 0,
        "tool_calls": 0,
        "errors": 0,
        "in_inf": 0,
        "out_inf": 0,
        "in_agent": 0,
        "out_agent": 0,
        "cache_read": 0,
        "cache_write": 0,
        "reasoning": 0,
        "duration_ms": 0,
    }
    return {**base, **over}


async def agents_by_name(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as connection:
        return {
            str(r[0]): r
            for r in (
                await connection.execute(
                    text(
                        "SELECT display_name, confidence, ingest, project_id, binding_id, id "
                        "FROM agents"
                    )
                )
            ).all()
        }


async def seed(engine: AsyncEngine, redis: Any) -> dict[str, Any]:
    world = await make_world(engine)
    cluster, ns, wk = await binding_place(engine, world)
    in_catalog = {"svc": "support-bot", "cluster": cluster, "ns": ns, "wk": wk}
    store = FakeStore(
        rollups=[
            usage(**in_catalog, agent_name="ticket-triager", invocations=3, duration_ms=2400),
            usage(
                **in_catalog,
                agent_name="ticket-triager",
                inference_calls=5,
                errors=1,
                in_inf=1200,
                out_inf=300,
                cache_read=800,
                reasoning=40,
            ),
            usage(
                svc="laptop-bot",
                ingest="external",
                agent_name="research-helper",
                provider="openai",
                model="gpt-5",
                invocations=1,
                in_agent=90,
                out_agent=9,
            ),
            usage(
                svc="",
                ns="shadow",
                wk="batch",
                ebpf=1,
                provider="openai",
                model="gpt-5-mini",
                inference_calls=7,
                in_inf=700,
                out_inf=70,
            ),
        ],
        tools=[
            {
                **in_catalog,
                "agent_id": "",
                "agent_name": "ticket-triager",
                "ingest": "",
                "ebpf": 0,
                "tool": "get_ticket",
                "hour": HOUR,
                "calls": 4,
                "errors": 1,
                "duration_ms": 800,
            },
        ],
    )
    report = await discovery.run_once(engine, store, redis, database="otel", now=NOW)  # type: ignore[arg-type]
    assert report.lease_acquired and report.failed_reads == 0
    return {"world": world, "store": store}


async def test_agents_appear_on_first_sight_with_their_confidence(
    engine: AsyncEngine, redis: Any
) -> None:
    seeded = await seed(engine, redis)
    agents = await agents_by_name(engine)
    assert set(agents) == {"ticket-triager", "research-helper", "batch"}
    triager = agents["ticket-triager"]
    assert triager[1] == "named" and triager[2] == "cluster"
    # Bound to the catalog by its workload: it belongs to the project.
    assert triager[3] == seeded["world"]["project_id"]
    assert triager[4] == seeded["world"]["binding_id"]
    assert agents["research-helper"][1:4] == ("named", "external", None)
    assert agents["batch"][1] == "observed"

    async with engine.connect() as connection:
        rollup = (
            await connection.execute(
                text(
                    "SELECT sum(invocations), sum(inference_calls), sum(errors), "
                    "sum(input_tokens), sum(output_tokens), sum(cache_read_tokens), "
                    "sum(reasoning_tokens), sum(duration_ms) FROM agent_rollups WHERE agent_id = :a"
                ),
                {"a": triager[5]},
            )
        ).one()
        helper_tokens = (
            await connection.execute(
                text("SELECT sum(input_tokens) FROM agent_rollups WHERE agent_id = :a"),
                {"a": agents["research-helper"][5]},
            )
        ).scalar_one()
        tool = (
            await connection.execute(
                text("SELECT tool_name, calls, errors FROM agent_tool_rollups WHERE agent_id = :a"),
                {"a": triager[5]},
            )
        ).one()
    assert tuple(int(v) for v in rollup) == (3, 5, 1, 1200, 300, 800, 40, 2400)
    # Usage reported only on the agent span is counted from there.
    assert int(helper_tokens) == 90
    assert tuple(tool) == ("get_ticket", 4, 1)

    # The next cycle re-reads whole hours and REPLACES them: nothing doubles.
    await redis.delete(discovery.LEASE_KEY)
    await discovery.run_once(engine, seeded["store"], redis, database="otel", now=NOW)  # type: ignore[arg-type]
    async with engine.connect() as connection:
        again = (
            await connection.execute(
                text("SELECT sum(input_tokens) FROM agent_rollups WHERE agent_id = :a"),
                {"a": triager[5]},
            )
        ).scalar_one()
    assert int(again) == 1200


async def _csrf(client: Any) -> dict[str, str]:
    me = (await client.get("/v1/me")).json()
    return {"X-CSRF-Token": me["csrf_token"]}


async def test_the_inventory_is_scoped_like_the_catalog(engine: AsyncEngine, redis: Any) -> None:
    seeded = await seed(engine, redis)
    harness = build_harness(require_it_settings())
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-nobody", user_type("user-nobody", "Nobody", "user-nobody@example.test")
    )
    await login_all(harness, ["user-nobody"])
    await make_role(harness, engine, "Agent viewer", ["agent.view", "project.view"])
    async with engine.connect() as connection:
        scope_ref = (
            await connection.execute(
                text(
                    "SELECT s.external_ref FROM projects p JOIN scopes s ON s.id = p.scope_id "
                    "WHERE p.id = :p"
                ),
                {"p": seeded["world"]["project_id"]},
            )
        ).scalar_one()
    await grant(engine, harness, "user-nobody", "Agent viewer", "project", scope_ref)

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        body = (await client.get("/v1/agents?window=24h")).json()
        names = {a["display_name"] for a in body["items"]}
        assert {"ticket-triager", "research-helper", "batch"} <= names
        triager = next(a for a in body["items"] if a["display_name"] == "ticket-triager")
        assert triager["totals"]["inference_calls"] == 5
        assert triager["models"][0]["model"] == "claude-sonnet-5"
        assert len(triager["series"]) == 24
        assert body["summary"]["outside_catalog"] >= 2
        detail = (await client.get(f"/v1/agents/{triager['id']}")).json()
        assert detail["tools"][0] == {"tool": "get_ticket", "calls": 4, "errors": 1, "avg_ms": 200}
        assert len(detail["series"]) == 168
        connect = (await client.get("/v1/agents/connect")).json()
        assert connect["cluster_endpoint"].endswith(":4318")
        assert connect["external_endpoint"] is None

    async with harness.api_client() as client:
        await harness.login(client, "user-nobody")
        body = (await client.get("/v1/agents")).json()
        # The project's agent only: nothing outside the catalog.
        assert [a["display_name"] for a in body["items"]] == ["ticket-triager"]
        outside = await agents_by_name(engine)
        assert (await client.get(f"/v1/agents/{outside['batch'][5]}")).status_code == 404
        assert (await client.get(f"/v1/agents/{uuid.uuid4()}")).status_code == 404


async def test_a_failed_read_keeps_what_was_kept(engine: AsyncEngine, redis: Any) -> None:
    class Down:
        async def query(self, *_: Any, **__: Any) -> Result:
            raise RuntimeError("store down")

    await make_world(engine)
    report = await discovery.run_once(
        engine, Down(), redis, database="otel", now=NOW - timedelta(hours=1)
    )  # type: ignore[arg-type]
    assert report.failed_reads == report.reads > 0
    assert await agents_by_name(engine) == {}
