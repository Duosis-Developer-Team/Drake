"""The cross-service signal endpoint, end to end.

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


class FakeStore:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.mode = "ok"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == "down":
            return httpx.Response(503, text="Code: 210. connection refused")
        sql = request.content.decode()
        group = {
            "cluster": "cluster-a",
            "namespace": NAMESPACE,
            "deployment": WORKLOAD,
            "statefulset": "",
            "daemonset": "",
            "newest": "2026-11-04 11:59:58.000000000",
        }
        stranger = {**group, "deployment": "someone-else"}
        if "otel_logs" in sql:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {**group, "lines": "40", "errors": "3", "warnings": "2"},
                        {**stranger, "lines": "9", "errors": "9", "warnings": "9"},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {**group, "requests": "8", "errors": "2", "p95_ns": 9e8},
                    {**stranger, "requests": "1", "errors": "1", "p95_ns": 1e6},
                ]
            },
        )


def signals_harness(
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


async def test_counts_attach_to_the_requested_binding_only(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = signals_harness(tmp_path)
    binding = await _bound(engine, harness)
    unknown = uuidlib.uuid4()

    async with owner(harness, engine) as client:
        response = await client.get(
            f"/v1/service-health/signals?binding_id={binding['id']}&binding_id={unknown}"
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["logs"] == {"state": "ok"}
    assert body["traces"] == {"state": "ok"}
    [item] = body["items"]
    assert item["binding_id"] == binding["id"]
    assert item["logs"]["errors"] == 3
    assert item["logs"]["lines"] == 40
    assert item["traces"]["requests"] == 8
    assert item["traces"]["error_ratio"] == 0.25
    assert item["traces"]["p95_ms"] == 900.0
    # The other workload in the namespace never leaves the API.
    assert "someone-else" not in response.text
    assert len(store.requests) == 2
    for request in store.requests:
        params = dict(request.url.params)
        assert params["param_namespaces"] == f"['{NAMESPACE}']"
        assert params["param_clusters"] == "['cluster-a']"


async def test_seeing_a_service_is_not_permission_to_read_its_signals(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = signals_harness(tmp_path)
    binding = await _bound(engine, harness)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
        response = await env_user.get(f"/v1/service-health/signals?binding_id={binding['id']}")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert store.requests == []


async def test_each_signal_states_its_own_absence(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, store = signals_harness(tmp_path, traces=False)
    binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        body = (await client.get(f"/v1/service-health/signals?binding_id={binding['id']}")).json()

    assert body["logs"] == {"state": "ok"}
    assert body["traces"] == {"state": "not_configured"}
    assert body["items"][0]["traces"] is None
    assert body["items"][0]["logs"]["errors"] == 3
    assert len(store.requests) == 1


async def test_a_store_outage_is_unavailable_never_zero_errors(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = signals_harness(tmp_path)
    store.mode = "down"
    binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        response = await client.get(f"/v1/service-health/signals?binding_id={binding['id']}")

    body = response.json()
    assert body["logs"] == {"state": "unavailable", "reason": "store_upstream_error"}
    assert body["traces"]["state"] == "unavailable"
    assert body["items"][0]["logs"] is None
    assert "Code: 210" not in response.text


async def test_bounded_inputs(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _ = signals_harness(tmp_path)
    await _bound(engine, harness)
    many = "&".join(f"binding_id={uuidlib.uuid4()}" for _ in range(101))

    async with owner(harness, engine) as client:
        too_many = await client.get(f"/v1/service-health/signals?{many}")
        bad_range = await client.get(
            f"/v1/service-health/signals?binding_id={uuidlib.uuid4()}&range=30d"
        )
        none = await client.get("/v1/service-health/signals")

    assert too_many.status_code == 422
    assert bad_range.status_code == 422
    assert none.status_code == 422
