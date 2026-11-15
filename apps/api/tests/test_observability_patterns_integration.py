"""The log patterns endpoint, end to end.

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

ROW = {
    "pattern": "payment charge failed: upstream timeout after <n>ms",
    "lines": "118",
    "severity_number": 17,
    "severity_text": "ERROR",
    "first_ts": "2026-11-15 11:05:00.000000000",
    "last_ts": "2026-11-15 11:59:00.000000000",
    "latest": "payment charge failed: upstream timeout after 1700ms token=abc123",
    "earliest": "payment charge failed: upstream timeout after 1650ms",
}


class FakeStore:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.mode = "ok"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == "down":
            return httpx.Response(503, text="Code: 210. connection refused")
        return httpx.Response(200, json={"data": [ROW]})


def patterns_harness(tmp_path: Path, *, enabled: bool = True) -> tuple[Any, FakeStore]:
    from drake_api.settings import ObservabilityStoreConnector
    from harness_s1 import build_harness, require_it_settings

    password_file = tmp_path / "reader"
    password_file.write_text("it-reader", encoding="utf-8")
    store = FakeStore()
    settings = require_it_settings().model_copy(
        update={
            "observability_logs_enabled": enabled,
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


async def test_patterns_come_back_scoped_and_redacted(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, store = patterns_harness(tmp_path)
    binding = await _bound(engine, harness)

    async with owner(harness, engine) as client:
        response = await client.get(
            f"/v1/service-health/bindings/{binding['id']}/logs/patterns?severity=error&q=payment"
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "ok"
    [pattern] = body["patterns"]
    assert pattern["lines"] == 118
    assert pattern["severity"] == "ERROR"
    assert "abc123" not in response.text
    [request] = store.requests
    params = dict(request.url.params)
    assert params["param_namespace"] == NAMESPACE
    assert params["param_workload"] == WORKLOAD
    assert params["param_min_severity"] == "17"
    assert params["param_term"] == "payment"


async def test_seeing_a_service_is_not_permission_to_read_its_patterns(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, store = patterns_harness(tmp_path)
    binding = await _bound(engine, harness)

    async with harness.api_client() as env_user:
        await harness.login(env_user, "user-env")
        await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
        visible = await env_user.get(f"/v1/service-health/bindings/{binding['id']}")
        patterns = await env_user.get(f"/v1/service-health/bindings/{binding['id']}/logs/patterns")
        unknown = await env_user.get(f"/v1/service-health/bindings/{uuidlib.uuid4()}/logs/patterns")

    assert visible.status_code == 200
    assert patterns.status_code == 404
    assert unknown.status_code == 404
    assert store.requests == []


async def test_states_stay_apart(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, store = patterns_harness(tmp_path, enabled=False)
    binding = await _bound(engine, harness)
    async with owner(harness, engine) as client:
        off = (
            await client.get(f"/v1/service-health/bindings/{binding['id']}/logs/patterns")
        ).json()
    assert off["state"] == "not_configured"
    assert store.requests == []

    harness, store = patterns_harness(tmp_path)
    store.mode = "down"
    async with owner(harness, engine) as client:
        down = await client.get(f"/v1/service-health/bindings/{binding['id']}/logs/patterns")
        bad = await client.get(
            f"/v1/service-health/bindings/{binding['id']}/logs/patterns?range=7d"
        )
    assert down.json()["state"] == "unavailable"
    assert "Code: 210" not in down.text
    assert bad.status_code == 422
