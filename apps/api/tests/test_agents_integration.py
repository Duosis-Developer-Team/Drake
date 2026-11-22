"""The agent inventory against the real database (Agent Visibility, phase 2).

The trace store is a double answering the two discovery statements with
chosen rows; what is under test is who an agent becomes, what is kept for
it, and who may see it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
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
GENCAY = "8c1f0b2a-7d3e-4c55-9e10-2b6a4f3c9d01"


class Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows


class FakeStore:
    def __init__(
        self,
        rollups: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        edges: list[dict[str, Any]] | None = None,
        mcp: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rollups, self.tools, self.edges = rollups, tools, edges or []
        self.mcp = mcp or []
        self.callers: list[dict[str, Any]] = []
        self.calls = 0

    async def query(self, statement: str, parameters: dict[str, str], **_: Any) -> Result:
        self.calls += 1
        assert "otel_traces" in statement
        start = datetime.fromisoformat(parameters["start"].replace(" ", "T")).replace(tzinfo=UTC)
        end = datetime.fromisoformat(parameters["end"].replace(" ", "T")).replace(tzinfo=UTC)
        if "p_agent_name" in statement:
            source = self.edges
        elif "AS caller_key" in statement:
            source = self.callers
        elif "user_agent.name'] AS client" in statement:
            source = self.mcp
        elif "gen_ai.tool.name'] AS tool" in statement:
            source = self.tools
        else:
            source = self.rollups
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


IDENTITY = {
    "svc": "",
    "cluster": "",
    "ns": "",
    "wk": "",
    "agent_id": "",
    "agent_name": "",
    "ingest": "",
    "ebpf": 0,
}


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
                "server": "hermes-mcp.hermes-test.svc",
                "hour": HOUR,
                "calls": 4,
                "errors": 1,
                "duration_ms": 800,
            },
        ],
    )
    triager = {
        f"p_{k}": v for k, v in {**IDENTITY, **in_catalog, "agent_name": "ticket-triager"}.items()
    }
    helper = {
        f"c_{k}": v
        for k, v in {
            **IDENTITY,
            "svc": "laptop-bot",
            "agent_name": "research-helper",
            "ingest": "external",
        }.items()
    }
    store.edges = [{**triager, **helper, "hour": HOUR, "calls": 3, "errors": 1}]
    mcp_row = {"method": "tools/call", "hour": HOUR, "calls": 0, "errors": 0, "duration_ms": 0}
    store.mcp = [
        {
            **mcp_row,
            **in_catalog,
            "svc": "hermes-mcp",
            "tool": "hermes_list_tasks",
            "client": "claude-ai",
            "calls": 8,
            "duration_ms": 400,
        },
        {
            **mcp_row,
            **in_catalog,
            "svc": "hermes-mcp",
            "tool": "hermes_create_task",
            "client": "Cursor",
            "calls": 2,
            "errors": 1,
            "duration_ms": 300,
        },
        {
            **mcp_row,
            "svc": "shadow-mcp",
            "cluster": "",
            "ns": "lab",
            "wk": "mcp",
            "tool": "run_sql",
            "client": "",
            "calls": 5,
        },
    ]
    person = {**in_catalog, "svc": "hermes-mcp", "hour": HOUR, "errors": 0}
    store.callers = [
        {
            **person,
            "tool": "hermes_list_tasks",
            "client": "claude-ai",
            "user_id": GENCAY,
            "user_name": "Gencay Coskun",
            "calls": 5,
            "last_seen": HOUR + 600,
        },
        {
            **person,
            "tool": "hermes_create_task",
            "client": "Cursor",
            "user_id": GENCAY,
            "user_name": "Gencay Coskun",
            "calls": 1,
            "errors": 1,
            "last_seen": HOUR + 1200,
        },
        {
            **person,
            "tool": "hermes_list_tasks",
            "client": "claude-ai",
            "user_id": "",
            "user_name": "Ayse",
            "calls": 2,
            "last_seen": HOUR + 300,
        },
        # An email is never a person here: dropped, the call stays unknown.
        {
            **person,
            "tool": "hermes_list_tasks",
            "client": "claude-ai",
            "user_id": "",
            "user_name": "someone@example.com",
            "calls": 1,
            "last_seen": HOUR + 60,
        },
    ]
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
        assert detail["tools"][0] == {
            "tool": "get_ticket",
            "server": "hermes-mcp.hermes-test.svc",
            "calls": 4,
            "errors": 1,
            "avg_ms": 200,
        }
        topo = (await client.get("/v1/agents/topology?window=24h")).json()
        by_label = {n["label"]: n for n in topo["nodes"]}
        assert by_label["ticket-triager"]["kind"] == "agent"
        assert by_label["get_ticket"]["server"] == "hermes-mcp.hermes-test.svc"
        assert by_label["claude-sonnet-5"]["provider"] == "anthropic"
        delegation = [e for e in topo["edges"] if e["kind"] == "agent"]
        assert delegation == [
            {
                "source": by_label["ticket-triager"]["id"],
                "target": by_label["research-helper"]["id"],
                "kind": "agent",
                "calls": 3,
                "errors": 1,
            }
        ]
        mcp = (await client.get("/v1/agents/mcp-servers?window=24h")).json()
        by_service = {m["service_name"]: m for m in mcp["servers"]}
        assert set(by_service) == {"hermes-mcp", "shadow-mcp"}
        hermes = by_service["hermes-mcp"]
        assert (hermes["calls"], hermes["errors"], hermes["avg_ms"]) == (10, 1, 70)
        assert [t["name"] for t in hermes["tools"]] == ["hermes_list_tasks", "hermes_create_task"]
        assert {c["name"]: c["calls"] for c in hermes["clients"]} == {"claude-ai": 8, "Cursor": 2}
        assert hermes["project"] is not None and len(hermes["series"]) == 24
        assert sum(p["calls"] for p in hermes["series"]) == 10
        # Who used it: by name, never by email; the email-named call is
        # simply not attributed (8 of 10 calls name a person).
        assert hermes["attributed_calls"] == 8
        assert [(u["name"], u["calls"], u["errors"]) for u in hermes["users"]] == [
            ("Gencay Coskun", 6, 1),
            ("Ayse", 2, 0),
        ]
        gencay = hermes["users"][0]
        assert gencay["id"] == GENCAY and gencay["clients"] == ["Cursor", "claude-ai"]
        assert datetime.fromisoformat(gencay["last_seen_at"]).timestamp() == HOUR + 1200
        assert "example.com" not in str(mcp)
        tools = {t["name"]: t for t in hermes["tools"]}
        assert [u["name"] for u in tools["hermes_list_tasks"]["users"]] == ["Gencay Coskun", "Ayse"]
        assert [u["name"] for u in tools["hermes_create_task"]["users"]] == ["Gencay Coskun"]
        clients = {c["name"]: c for c in hermes["clients"]}
        assert [u["calls"] for u in clients["claude-ai"]["users"]] == [5, 2]
        shadow = by_service["shadow-mcp"]
        assert shadow["users"] == [] and shadow["attributed_calls"] == 0
        focused = (await client.get(f"/v1/agents/topology?focus={triager['id']}")).json()
        assert {n["label"] for n in focused["nodes"] if n["kind"] == "agent"} == {
            "ticket-triager",
            "research-helper",
        }
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
        topo = (await client.get("/v1/agents/topology")).json()
        # The helper is outside this caller's scope: no node, and no link to it.
        assert {n["label"] for n in topo["nodes"] if n["kind"] == "agent"} == {"ticket-triager"}
        assert [e for e in topo["edges"] if e["kind"] == "agent"] == []
        mcp = (await client.get("/v1/agents/mcp-servers")).json()
        # Only the server bound to this caller's project.
        assert [m["service_name"] for m in mcp["servers"]] == ["hermes-mcp"]
        helper_id = outside["research-helper"][5]
        assert (await client.get(f"/v1/agents/topology?focus={helper_id}")).status_code == 404


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


TRACE = "cd" * 16
STRANGER = "ef" * 16


class RunStore:
    """The trace store over HTTP, answering the two run statements."""

    def __init__(self, place: tuple[str, str, str]) -> None:
        self.cluster, self.ns, self.wk = place
        self.requests: list[httpx.Request] = []

    def span(self, span_id: str, parent: str, start_ms: int, **over: Any) -> dict[str, Any]:
        base = {
            "start_ns": str((1_000_000 + start_ms) * 1_000_000),
            "span_id": span_id,
            "parent_span_id": parent,
            "name": span_id,
            "kind": "Internal",
            "duration_ns": "100000000",
            "status": "Unset",
            "svc": "support-bot",
            "cluster": self.cluster,
            "ns": self.ns,
            "wk": self.wk,
            "agent_id": "",
            "agent_name": "ticket-triager",
            "ingest": "",
            "ebpf": 0,
            "genai": 1,
            "op": "",
            "inference": 0,
            "provider": "",
            "model": "",
            "input_tokens": "0",
            "output_tokens": "0",
            "cache_read_tokens": "0",
            "reasoning_tokens": "0",
            "tool": "",
            "tool_type": "",
            "mcp_method": "",
            "server": "",
            "error_type": "",
            "http_status": "",
            "http_route": "",
        }
        return {**base, **over}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        sql = request.content.decode()
        params = dict(request.url.params)
        if "GROUP BY t.TraceId" in sql:
            assert params["param_svc"] == "support-bot"
            assert params["param_agent_name"] == "ticket-triager"
            row = {
                "trace_id": TRACE,
                "started": "2026-11-21 17:00:00.000000000",
                "span_ns": "900000000",
                "agent_ns": "800000000",
                "invocations": "1",
                "model_calls": "1",
                "tool_calls": "1",
                "errors": "1",
                "in_inf": "1200",
                "out_inf": "90",
                "in_agent": "0",
                "out_agent": "0",
                "models": ["claude-sonnet-5"],
                "tools": ["get_ticket"],
            }
            return httpx.Response(200, json={"data": [row]})
        if params.get("param_trace_id") == TRACE:
            rows = [
                self.span("root", "", 0, op="invoke_agent"),
                self.span(
                    "chat",
                    "root",
                    10,
                    op="chat",
                    inference=1,
                    provider="anthropic",
                    model="claude-sonnet-5",
                    input_tokens="1200",
                    output_tokens="90",
                ),
                self.span("billing", "root", 200, svc="billing", genai=0, agent_name=""),
                self.span(
                    "tool", "billing", 210, op="execute_tool", tool="get_ticket", status="Error"
                ),
                self.span(
                    "helper",
                    "root",
                    400,
                    svc="laptop-bot",
                    op="invoke_agent",
                    agent_name="research-helper",
                    ingest="external",
                    cluster="",
                    ns="",
                    wk="",
                ),
            ]
            return httpx.Response(200, json={"data": rows})
        if params.get("param_trace_id") == STRANGER:
            rows = [
                self.span(
                    "x",
                    "",
                    0,
                    svc="laptop-bot",
                    op="invoke_agent",
                    agent_name="research-helper",
                    cluster="",
                    ns="",
                    wk="",
                )
            ]
            return httpx.Response(200, json={"data": rows})
        return httpx.Response(200, json={"data": []})


def run_harness(tmp_path: Path, place: tuple[str, str, str]) -> tuple[Any, RunStore]:
    from drake_api.settings import ObservabilityStoreConnector

    password_file = tmp_path / "reader"
    password_file.write_text("it-reader", encoding="utf-8")
    store = RunStore(place)
    settings = require_it_settings().model_copy(
        update={
            "observability_traces_enabled": True,
            "observability_store": ObservabilityStoreConnector(
                url="http://127.0.0.1:58199",
                username="drake_reader",
                password_file=str(password_file),
            ),
        }
    )
    harness = build_harness(settings, observability_transport=httpx.MockTransport(store.handler))
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-nobody", user_type("user-nobody", "Nobody", "user-nobody@example.test")
    )
    return harness, store


async def test_runs_are_read_on_demand_and_shown_within_scope(
    engine: AsyncEngine, redis: Any, tmp_path: Path
) -> None:
    seeded = await seed(engine, redis)
    place = await binding_place(engine, seeded["world"])
    harness, store = run_harness(tmp_path, place)
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
    agents = await agents_by_name(engine)
    triager, helper = agents["ticket-triager"][5], agents["research-helper"][5]

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        body = (await client.get(f"/v1/agents/{triager}/runs?window=7d&status=error")).json()
        assert body["state"] == "ok"
        (run,) = body["runs"]
        assert (run["trace_id"], run["duration_ms"], run["input_tokens"]) == (TRACE, 800.0, 1200)
        assert "HAVING errors > 0" in store.requests[-1].content.decode()

        detail = (await client.get(f"/v1/agents/{triager}/runs/{TRACE.upper()}")).json()
        steps = {s["span_id"]: s for s in detail["spans"]}
        # The owner sees every agent: the helper is named; billing is not an agent.
        assert set(steps) == {"root", "chat", "tool", "helper"}
        assert detail["hidden_spans"] == 1
        assert steps["tool"]["parent_id"] == "root" and steps["tool"]["status"] == "error"
        assert steps["helper"]["agent"]["name"] == "research-helper"
        assert steps["chat"]["agent"]["self"] is True

        missing = await client.get(f"/v1/agents/{triager}/runs/{STRANGER}")
        assert missing.status_code == 404
        assert (await client.get(f"/v1/agents/{triager}/runs/not-a-trace")).status_code == 404

    async with harness.api_client() as client:
        await harness.login(client, "user-nobody")
        detail = (await client.get(f"/v1/agents/{triager}/runs/{TRACE}")).json()
        # An agent outside the catalog is not this caller's to see.
        assert {s["span_id"] for s in detail["spans"]} == {"root", "chat", "tool"}
        assert detail["hidden_spans"] == 2
        assert (await client.get(f"/v1/agents/{helper}/runs")).status_code == 404


async def test_without_a_trace_store_runs_say_so(engine: AsyncEngine, redis: Any) -> None:
    await seed(engine, redis)
    triager = (await agents_by_name(engine))["ticket-triager"][5]
    harness = build_harness(require_it_settings())
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        body = (await client.get(f"/v1/agents/{triager}/runs")).json()
        assert body == {
            "agent_id": str(triager),
            "window": "24h",
            "status": "any",
            "state": "not_configured",
            "runs": [],
        }
