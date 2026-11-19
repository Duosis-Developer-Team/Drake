"""The environment overview endpoint, end to end.

Real PostgreSQL, real Redis, real RBAC and the real binding repository.
Only ClickHouse is a fake behind the production adapter's HTTP transport.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, seed_catalog_world
from test_service_health_read_api_integration import (
    NAMESPACE,
    WORKLOAD,
    create_binding,
    owner,
    seed_workload,
)
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

KEY = f"cluster-a|{NAMESPACE}|deployment|{WORKLOAD}"
STRANGER = f"cluster-a|{NAMESPACE}|deployment|someone-else"


def _rows(sql: str) -> list[dict[str, Any]]:
    """What the store answers per statement; every statement also gets a
    row for another workload in the namespace, which must never surface."""
    if "INNER JOIN" in sql:
        return [{"caller": STRANGER, "callee": KEY, "calls": "5", "errors": "0", "p95_ns": 1e6}]
    if "AS operation" in sql:
        return [
            {
                "wk": KEY,
                "system": "postgresql",
                "name": "auth_db",
                "operation": "connect",
                "calls": "12",
                "errors": "0",
                "p95_ns": 1.1e8,
            },
            {
                "wk": STRANGER,
                "system": "postgresql",
                "name": "x",
                "operation": "SELECT secret",
                "calls": "999",
                "errors": "0",
                "p95_ns": 1e6,
            },
        ]
    if "AS route" in sql:
        return [
            {
                "wk": KEY,
                "route": "GET /home",
                "requests": "8",
                "errors": "1",
                "client_errors": "2",
                "long_lived": "0",
                "p50_ns": 1e8,
                "p95_ns": 9e8,
                "p99_ns": 1.2e9,
            },
            {
                "wk": STRANGER,
                "route": "GET /secret",
                "requests": "1",
                "errors": "0",
                "client_errors": "0",
                "long_lived": "0",
                "p50_ns": 1,
                "p95_ns": 1,
                "p99_ns": 1,
            },
        ]
    if "AS code" in sql:
        return [
            {"wk": KEY, "code": "200", "requests": "6"},
            {"wk": KEY, "code": "401", "requests": "2"},
            {"wk": STRANGER, "code": "500", "requests": "50"},
        ]
    if "AS pattern" in sql:
        return [
            {
                "pattern": "GET /health <n>",
                "lines": "40",
                "severity_number": 9,
                "severity_text": "INFO",
                "workloads": "1",
            }
        ]
    return []


class FakeStore:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.mode = "ok"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == "down":
            return httpx.Response(503, text="Code: 210. connection refused")
        return httpx.Response(200, json={"data": _rows(request.content.decode())})


def overview_harness(
    tmp_path: Path, *, logs: bool = True, traces: bool = True
) -> tuple[Any, FakeStore]:
    from drake_api.settings import ObservabilityStoreConnector
    from harness_s1 import build_harness, require_it_settings

    password_file = tmp_path / "reader"
    password_file.write_text("it-reader", encoding="utf-8")
    store = FakeStore()
    settings = require_it_settings().model_copy(
        update={
            "observability_logs_enabled": logs,
            "observability_traces_enabled": traces,
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
        "user-env", user_type("user-env", "Env", "user-env@example.test")
    )
    return harness, store


async def _bound(engine: AsyncEngine, harness: Any) -> dict[str, Any]:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    async with owner(harness, engine) as client:
        return await create_binding(client, world)


async def test_every_section_answers_for_the_requested_binding_only(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = overview_harness(tmp_path)
    binding = await _bound(engine, harness)
    unknown = uuidlib.uuid4()

    async with owner(harness, engine) as client:
        response = await client.get(
            f"/v1/service-health/overview?binding_id={binding['id']}&binding_id={unknown}&range=24h"
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bindings"] == [{"binding_id": binding["id"], "traces": True, "logs": True}]
    assert len(body["buckets"]) == 24 and body["bucket_seconds"] == 3600
    for section in ("traffic", "latency", "codes", "routes", "calls", "databases"):
        assert body[section]["state"] == "ok", section
    for section in ("log_lines", "log_patterns"):
        assert body[section]["state"] == "ok", section

    [traffic] = body["traffic"]["data"]
    assert traffic["binding_id"] == binding["id"] and len(traffic["buckets"]) == 24
    [codes] = body["codes"]["data"]
    assert codes["codes"] == {"200": 6, "401": 2}
    [route] = body["routes"]["data"]
    assert route["route"] == "GET /home" and route["p95_ms"] == 900.0
    [db] = body["databases"]["data"]
    assert db["operation"] == "connect" and db["calls"] == 12
    # A call from a workload the caller was not given is not a call here.
    assert body["calls"]["data"] == []
    assert body["log_patterns"]["data"][0]["lines"] == 40
    # Too few to be a finding; every detector could run.
    assert body["findings"] == {"items": [], "skipped": []}

    # Another workload in the namespace never leaves the API.
    for word in ("someone-else", "GET /secret", "SELECT secret"):
        assert word not in response.text
    assert len(store.requests) == 8
    for request in store.requests:
        params = dict(request.url.params)
        assert params["param_namespaces"] == f"['{NAMESPACE}']"
        assert params["param_workloads"] == f"['{KEY}']"
        assert WORKLOAD not in request.content.decode()


async def test_seeing_a_service_is_not_permission_to_read_its_overview(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = overview_harness(tmp_path)
    binding = await _bound(engine, harness)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
        response = await env_user.get(f"/v1/service-health/overview?binding_id={binding['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["bindings"] == []
    assert body["traffic"] == {"state": "ok", "data": None}
    assert store.requests == []


async def test_each_signal_states_its_own_absence(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, store = overview_harness(tmp_path, traces=False)
    binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        body = (await client.get(f"/v1/service-health/overview?binding_id={binding['id']}")).json()

    assert body["traffic"] == {"state": "not_configured"}
    assert body["calls"] == {"state": "not_configured"}
    assert body["log_lines"]["state"] == "ok"
    assert len(store.requests) == 2


async def test_a_store_outage_is_unavailable_never_zero(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = overview_harness(tmp_path)
    store.mode = "down"
    binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        response = await client.get(f"/v1/service-health/overview?binding_id={binding['id']}")

    body = response.json()
    assert body["traffic"] == {"state": "unavailable", "reason": "store_upstream_error"}
    assert body["log_patterns"]["state"] == "unavailable"
    assert "Code: 210" not in response.text


async def test_bounded_inputs(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _ = overview_harness(tmp_path)
    await _bound(engine, harness)
    many = "&".join(f"binding_id={uuidlib.uuid4()}" for _ in range(33))

    async with owner(harness, engine) as client:
        too_many = await client.get(f"/v1/service-health/overview?{many}")
        bad_range = await client.get(
            f"/v1/service-health/overview?binding_id={uuidlib.uuid4()}&range=30d"
        )
        none = await client.get("/v1/service-health/overview")

    assert too_many.status_code == 422
    assert bad_range.status_code == 422
    assert none.status_code == 422
