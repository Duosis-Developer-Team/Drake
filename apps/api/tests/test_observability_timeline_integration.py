"""The timeline endpoint and the window on logs and traces, end to end.

Real PostgreSQL, real Redis, real RBAC and the real binding repository.
Only ClickHouse is a fake behind the production adapter's HTTP transport.
"""

import uuid as uuidlib
from datetime import UTC, datetime, timedelta
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
        bucket = int(dict(request.url.params).get("param_bucket", "150"))
        now = int(datetime.now(UTC).timestamp())
        current = now - now % bucket
        if "toStartOfInterval" in sql and "otel_traces" in sql:
            return httpx.Response(
                200,
                json={"data": [{"t": current, "requests": "8", "errors": "2", "p95_ns": 9e8}]},
            )
        if "toStartOfInterval" in sql and "otel_logs" in sql:
            return httpx.Response(
                200, json={"data": [{"t": current, "lines": "30", "errors": "3", "warnings": "1"}]}
            )
        return httpx.Response(200, json={"data": []})


def timeline_harness(tmp_path: Path, *, traces: bool = True) -> tuple[Any, FakeStore]:
    from drake_api.settings import ObservabilityStoreConnector
    from harness_s1 import build_harness, require_it_settings

    password_file = tmp_path / "reader"
    password_file.write_text("it-reader", encoding="utf-8")
    store = FakeStore()
    settings = require_it_settings().model_copy(
        update={
            "observability_logs_enabled": True,
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


async def test_the_timeline_returns_both_lanes_bucketed_and_scoped(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = timeline_harness(tmp_path)
    binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        response = await client.get(f"/v1/service-health/bindings/{binding['id']}/timeline")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bucket_seconds"] == 150
    assert len(body["buckets"]) == 24
    assert body["buckets"][-1]["partial"] is True
    assert body["requests"]["state"] == "ok"
    assert body["requests"]["buckets"][-1] == {
        "requests": 8,
        "errors": 2,
        "error_ratio": 0.25,
        "p95_ms": 900.0,
    }
    assert body["logs"]["buckets"][-1] == {"lines": 30, "errors": 3, "warnings": 1}
    assert len(store.requests) == 2
    for request in store.requests:
        params = dict(request.url.params)
        assert params["param_namespace"] == NAMESPACE
        assert params["param_workload"] == WORKLOAD


async def test_each_lane_states_its_own_absence(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, store = timeline_harness(tmp_path, traces=False)
    binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        body = (
            await client.get(f"/v1/service-health/bindings/{binding['id']}/timeline?range=6h")
        ).json()

    assert body["bucket_seconds"] == 900
    assert body["requests"] == {"state": "not_configured", "buckets": []}
    assert body["logs"]["state"] == "ok"
    assert len(store.requests) == 1


async def test_seeing_a_service_is_not_permission_to_read_its_timeline(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = timeline_harness(tmp_path)
    binding = await _bound(engine, harness)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
        visible = await env_user.get(f"/v1/service-health/bindings/{binding['id']}")
        timeline = await env_user.get(f"/v1/service-health/bindings/{binding['id']}/timeline")
        unknown = await env_user.get(f"/v1/service-health/bindings/{uuidlib.uuid4()}/timeline")

    assert visible.status_code == 200
    assert timeline.status_code == 404
    assert unknown.status_code == 404
    assert store.requests == []


async def test_an_outage_is_unavailable_never_a_flat_line(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = timeline_harness(tmp_path)
    store.mode = "down"
    binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        response = await client.get(f"/v1/service-health/bindings/{binding['id']}/timeline")

    body = response.json()
    assert body["requests"]["state"] == "unavailable"
    assert body["requests"]["buckets"] == []
    assert body["logs"]["state"] == "unavailable"
    assert "Code: 210" not in response.text


async def test_logs_and_traces_accept_a_window_inside_the_range(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _ = timeline_harness(tmp_path)
    binding = await _bound(engine, harness)
    now = datetime.now(UTC)
    start = (now - timedelta(minutes=30)).isoformat()
    end = (now - timedelta(minutes=25)).isoformat()
    outside = (now - timedelta(hours=3)).isoformat()

    async with owner(harness, engine) as client:
        logs = await client.get(
            f"/v1/service-health/bindings/{binding['id']}/logs",
            params={"start": start, "end": end},
        )
        traces = await client.get(
            f"/v1/service-health/bindings/{binding['id']}/traces",
            params={"start": start, "end": end},
        )
        refused = await client.get(
            f"/v1/service-health/bindings/{binding['id']}/logs",
            params={"start": outside, "end": outside},
        )

    assert logs.status_code == 200, logs.text
    assert logs.json()["window"]["start"].startswith(start[:16])
    assert traces.status_code == 200, traces.text
    assert traces.json()["window"]["end"].startswith(end[:16])
    assert refused.status_code == 422
