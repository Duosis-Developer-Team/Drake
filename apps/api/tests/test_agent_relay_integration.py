"""The relay end to end over real PostgreSQL and Redis (ADR-0037 §3).

An agent long-polls the ingest listener for reads of its own cluster's
stores and posts the answers back. Proven here:

- a read issued by the API reaches the agent of THAT cluster and its answer
  comes back to the caller;
- an agent never sees another cluster's queue and cannot answer another
  cluster's request (uniform 404);
- a cluster whose agent has stopped sending heartbeats fails a read at once;
- a request nobody is waiting for any more is never handed out;
- the ClickHouse adapter routes a kept cluster's read through all of this.
"""

import asyncio
import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from agent_helpers import pop_headers
from cryptography.hazmat.primitives.asymmetric import ec
from drake_api.agents.internal_app import create_internal_agent_app
from drake_api.catalog.service import CatalogService
from drake_api.observability.store import ClickHouseStore
from drake_api.relay import AgentRelay, RelayUnavailableError, queue_key
from drake_api.settings import ObservabilityStoreConnector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_agent_inventory_integration import (  # noqa: F401 - fixtures
    ca_settings,
    engine,
    enroll_agent,
    migrated_db,
)

pytestmark = pytest.mark.integration


class Agent:
    def __init__(self, client: httpx.AsyncClient, key: ec.EllipticCurvePrivateKey, agent_id: str):
        self.client = client
        self.key = key
        self.agent_id = agent_id

    async def post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        body = json.dumps(payload).encode()
        headers = pop_headers(self.key, self.agent_id, "POST", path, body)
        headers["Content-Type"] = "application/json"
        return await self.client.post(path, content=body, headers=headers, timeout=30)

    async def next(self, wait: int = 3) -> httpx.Response:
        return await self.post("/internal/v1/agent/relay/next", {"wait_seconds": wait})

    async def answer(self, request_id: str, document: dict[str, Any]) -> httpx.Response:
        return await self.post(
            "/internal/v1/agent/relay/result",
            {
                "id": request_id,
                "status_code": 200,
                "content_type": "application/json",
                "body_b64": base64.b64encode(json.dumps(document).encode()).decode(),
            },
        )


async def _cluster(db: AsyncEngine, ref: str, location: str) -> str:
    async with db.begin() as connection:
        created = await CatalogService(connection, source_kind="operator").create_cluster(
            ref, ref.title()
        )
        await connection.execute(
            text("UPDATE clusters SET telemetry_location = :loc WHERE id = :id"),
            {"loc": location, "id": created.id},
        )
    return str(created.id)


async def _beat(db: AsyncEngine, agent_id: str, seconds_ago: int = 0) -> None:
    async with db.begin() as connection:
        await connection.execute(
            text(
                "UPDATE cluster_agents SET last_heartbeat_at = now() - make_interval(secs => :s) "
                "WHERE id = :id"
            ),
            {"s": seconds_ago, "id": agent_id},
        )


@pytest.fixture
async def world(engine: AsyncEngine, tmp_path: Path) -> Any:  # noqa: F811
    settings = ca_settings(tmp_path)
    redis = aioredis.from_url(settings.redis_url)
    for key in await redis.keys("relay:*"):
        await redis.delete(key)
    kept = await _cluster(engine, "kept-1", "cluster")
    other = await _cluster(engine, "kept-2", "cluster")
    await _cluster(engine, "central-1", "central")
    kept_agent, kept_key = await enroll_agent(engine, kept)
    other_agent, other_key = await enroll_agent(engine, other)
    await _beat(engine, kept_agent)
    await _beat(engine, other_agent)
    app = create_internal_agent_app(settings, surface="ingest")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://agent")
    yield {
        "settings": settings,
        "engine": engine,
        "redis": redis,
        "relay": AgentRelay(settings, redis=redis, engine=engine),
        "kept": Agent(client, kept_key, kept_agent),
        "other": Agent(client, other_key, other_agent),
        "kept_id": kept,
        "kept_agent": kept_agent,
    }
    await client.aclose()
    await redis.aclose()


async def test_a_read_reaches_its_clusters_agent_and_the_answer_comes_back(world: Any) -> None:
    relay: AgentRelay = world["relay"]
    pending = asyncio.create_task(
        relay.call(
            "kept-1",
            "clickhouse.query",
            {"statement": "SELECT 1", "params": {}},
            timeout_seconds=10,
        )
    )
    response = await world["kept"].next()
    assert response.status_code == 200
    item = response.json()
    assert item["kind"] == "clickhouse.query"
    assert item["request"] == {"statement": "SELECT 1", "params": {}}
    assert (await world["kept"].answer(item["id"], {"data": [{"n": 1}]})).status_code == 200
    reply = await asyncio.wait_for(pending, 5)
    assert reply.status_code == 200
    assert json.loads(reply.body) == {"data": [{"n": 1}]}
    # Answered once: a second answer for the same request finds no owner.
    assert (await world["kept"].answer(item["id"], {"data": []})).status_code == 404


async def test_an_agent_never_sees_or_answers_another_clusters_reads(world: Any) -> None:
    relay: AgentRelay = world["relay"]
    pending = asyncio.create_task(
        relay.call(
            "kept-1",
            "clickhouse.query",
            {"statement": "SELECT 1", "params": {}},
            timeout_seconds=10,
        )
    )
    # The other cluster's agent waits on its own queue and finds nothing.
    assert (await world["other"].next(wait=1)).status_code == 204
    item = (await world["kept"].next()).json()
    # Knowing the id is not enough to answer for a cluster that is not yours.
    assert (await world["other"].answer(item["id"], {"data": []})).status_code == 404
    assert (await world["kept"].answer(item["id"], {"data": []})).status_code == 200
    await asyncio.wait_for(pending, 5)


async def test_a_silent_agent_fails_a_read_at_once(world: Any) -> None:
    await _beat(world["engine"], world["kept_agent"], seconds_ago=600)
    relay = AgentRelay(world["settings"], redis=world["redis"], engine=world["engine"])
    started = time.monotonic()
    with pytest.raises(RelayUnavailableError, match="store_agent_offline"):
        await relay.call("kept-1", "clickhouse.query", {}, timeout_seconds=10)
    assert time.monotonic() - started < 2


async def test_a_central_cluster_is_not_relayed(world: Any) -> None:
    with pytest.raises(RelayUnavailableError, match="store_not_configured"):
        await world["relay"].call("central-1", "clickhouse.query", {}, timeout_seconds=1)


async def test_a_read_nobody_waits_for_is_never_handed_out(world: Any) -> None:
    stale = json.dumps(
        {
            "id": uuid.uuid4().hex,
            "kind": "clickhouse.query",
            "deadline": time.time() - 1,
            "request": {},
        }
    )
    await world["redis"].rpush(queue_key(world["kept_id"]), stale)
    assert (await world["kept"].next(wait=1)).status_code == 204


async def test_an_unanswered_read_times_out_for_the_caller(world: Any) -> None:
    with pytest.raises(RelayUnavailableError, match="store_timeout"):
        await world["relay"].call("kept-1", "clickhouse.query", {}, timeout_seconds=0.5)


async def test_relay_routes_refuse_an_unsigned_caller(world: Any) -> None:
    response = await world["kept"].client.post(
        "/internal/v1/agent/relay/next", json={"wait_seconds": 1}
    )
    assert response.status_code in (401, 403)


async def test_the_clickhouse_adapter_reads_a_kept_cluster_through_its_agent(
    world: Any, tmp_path: Path
) -> None:
    settings = world["settings"].model_copy(
        update={
            "observability_store": ObservabilityStoreConnector(url="http://127.0.0.1:1"),
        }
    )
    store = ClickHouseStore(settings, relay=world["relay"])

    async def serve() -> None:
        item = (await world["kept"].next()).json()
        assert item["request"]["params"]["param_cluster"] == "kept-1"
        await world["kept"].answer(item["id"], {"data": [{"Body": "hello"}]})

    agent = asyncio.create_task(serve())
    result = await store.query(
        "SELECT Body FROM otel.otel_logs WHERE ResourceAttributes['k8s.cluster.name'] = "
        "{cluster:String}",
        {"cluster": "kept-1"},
        max_rows=10,
        timeout_seconds=5,
        correlation_id="it",
    )
    await agent
    assert result.rows == [{"Body": "hello"}]
