"""The binding trace endpoints, end to end (ADR-0029).

Real PostgreSQL, real Redis, real RBAC and the real binding repository.
Only ClickHouse is a fake behind the production adapter's HTTP transport,
answering each statement by what it selects.
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

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


class FakeStore:
    """A ClickHouse that answers the four trace statements by shape."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.mode = "ok"
        self.spans: list[dict[str, Any]] = [
            {
                "ts": "2026-11-04 11:59:58.000000000",
                "span_id": "00f067aa0ba902b7",
                "parent_span_id": "",
                "name": "POST /v1/payments",
                "kind": "Server",
                "duration_ns": "1200000000",
                "status": "Error",
                "status_message": "",
                "attributes": {"http.request.method": "POST", "client.address": "10.0.0.7"},
                "pod": "alpha-api-1",
            }
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == "down":
            return httpx.Response(503, text="Code: 210. connection refused")
        sql = request.content.decode()
        if "WHERE TraceId" in sql:
            return httpx.Response(200, json={"data": self.spans})
        if "GROUP BY route" in sql:
            row = {"route": "POST /v1/payments", "requests": "4", "errors": "1"}
            return httpx.Response(200, json={"data": [{**row, "p50_ns": 1e8, "p95_ns": 9e8}]})
        if "ORDER BY" in sql:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "ts": "2026-11-04 11:59:58.000000000",
                            "trace_id": TRACE,
                            "route": "POST /v1/payments",
                            "duration_ns": "1200000000",
                            "status": "Error",
                            "method": "POST",
                            "http_status": "502",
                            "path": "/v1/payments",
                            "pod": "alpha-api-1",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"data": [{"requests": "4", "errors": "1", "p95_ns": 9e8}]})


def traces_harness(tmp_path: Path, *, enabled: bool = True) -> tuple[Any, FakeStore]:
    from drake_api.settings import ObservabilityStoreConnector
    from harness_s1 import build_harness, require_it_settings

    password_file = tmp_path / "reader"
    password_file.write_text("it-reader", encoding="utf-8")
    store = FakeStore()
    settings = require_it_settings().model_copy(
        update={
            "observability_traces_enabled": enabled,
            "observability_store": ObservabilityStoreConnector(
                url="http://127.0.0.1:58199",
                username="drake_reader",
                password_file=str(password_file),
            ),
        }
    )
    harness = build_harness(settings, observability_transport=httpx.MockTransport(store.handler))
    user_type = type(harness.provider.users["user-owner"])
    for subject in ("user-plain", "user-env", "user-b-only", "user-cluster"):
        harness.provider.users.setdefault(
            subject,
            user_type(subject, subject.replace("user-", "").title(), f"{subject}@example.test"),
        )
    return harness, store


async def _bound(engine: AsyncEngine, harness: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
    return world, binding


async def test_routes_totals_and_requests_come_from_the_bindings_scope(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = traces_harness(tmp_path)
    _, binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        response = await client.get(
            f"/v1/service-health/bindings/{binding['id']}/traces?status=error&sort=slowest"
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "ok"
    assert body["totals"] == {"requests": 4, "errors": 1, "error_ratio": 0.25, "p95_ms": 900.0}
    assert body["routes"][0]["route"] == "POST /v1/payments"
    assert body["requests"][0]["trace_id"] == TRACE
    assert len(store.requests) == 3
    for request in store.requests:
        params = dict(request.url.params)
        assert params["param_namespace"] == NAMESPACE
        assert params["param_workload"] == WORKLOAD
        assert params["param_cluster"] == "cluster-a"
    assert "58199" not in response.text


async def test_a_trace_returns_only_allowlisted_span_attributes(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = traces_harness(tmp_path)
    _, binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        response = await client.get(f"/v1/service-health/bindings/{binding['id']}/traces/{TRACE}")

    assert response.status_code == 200, response.text
    [span] = response.json()["spans"]
    assert span["attributes"] == {"http.request.method": "POST"}
    assert "10.0.0.7" not in response.text
    [request] = store.requests
    assert dict(request.url.params)["param_namespace"] == NAMESPACE


async def test_a_trace_outside_the_scope_is_indistinguishable_from_none(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = traces_harness(tmp_path)
    store.spans = []
    _, binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        missing = await client.get(f"/v1/service-health/bindings/{binding['id']}/traces/{TRACE}")
        malformed = await client.get(
            f"/v1/service-health/bindings/{binding['id']}/traces/not-a-trace"
        )

    assert missing.status_code == 404
    assert malformed.status_code == 404
    assert missing.json()["error"]["message"] == malformed.json()["error"]["message"]


async def test_seeing_a_service_is_not_permission_to_read_its_traces(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = traces_harness(tmp_path)
    _, binding = await _bound(engine, harness)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
        visible = await env_user.get(f"/v1/service-health/bindings/{binding['id']}")
        traces = await env_user.get(f"/v1/service-health/bindings/{binding['id']}/traces")
        trace = await env_user.get(f"/v1/service-health/bindings/{binding['id']}/traces/{TRACE}")
        unknown = await env_user.get(f"/v1/service-health/bindings/{uuidlib.uuid4()}/traces")

    assert visible.status_code == 200
    assert traces.status_code == 404
    assert trace.status_code == 404
    assert unknown.status_code == 404
    assert store.requests == []


async def test_disabled_traces_are_not_configured_and_query_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = traces_harness(tmp_path, enabled=False)
    _, binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        body = (await client.get(f"/v1/service-health/bindings/{binding['id']}/traces")).json()

    assert body["state"] == "not_configured"
    assert body["totals"] is None
    assert store.requests == []


async def test_a_store_outage_is_unavailable_never_an_empty_list(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = traces_harness(tmp_path)
    store.mode = "down"
    _, binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        response = await client.get(f"/v1/service-health/bindings/{binding['id']}/traces")

    body = response.json()
    assert body["state"] == "unavailable"
    assert body["reason"] == "store_upstream_error"
    assert body["totals"] is None
    assert "Code: 210" not in response.text
