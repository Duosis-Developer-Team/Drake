"""The binding log endpoint, end to end (ADR-0029).

Real PostgreSQL, real Redis, real RBAC and the real binding repository.
Only ClickHouse is a deterministic fake behind the same HTTP transport the
production adapter uses, so what these tests assert about who may read
which logs is about the shipped authorization path, not a stub.
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
    """A ClickHouse that records what it was asked and answers on cue."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.mode = "ok"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == "down":
            return httpx.Response(503, text="Code: 210. connection refused")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "ts": "2026-10-28 11:59:58.000000000",
                        "severity_text": "ERROR",
                        "severity_number": 17,
                        "body": "POST /v1/payments 502",
                        "trace_id": "",
                        "span_id": "",
                        "pod": "alpha-api-1",
                        "container": "api",
                    }
                ]
            },
        )


def logs_harness(tmp_path: Path, *, enabled: bool = True) -> tuple[Any, FakeStore]:
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
    for subject in ("user-plain", "user-env", "user-b-only", "user-cluster"):
        harness.provider.users.setdefault(
            subject,
            user_type(subject, subject.replace("user-", "").title(), f"{subject}@example.test"),
        )
    return harness, store


async def test_the_binding_decides_whose_logs_are_read(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, store = logs_harness(tmp_path)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        response = await client.get(
            f"/v1/service-health/bindings/{binding['id']}/logs?severity=error&q=payments"
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "ok"
    assert [entry["body"] for entry in body["entries"]] == ["POST /v1/payments 502"]
    [request] = store.requests
    params = dict(request.url.params)
    # Every scope value came from the binding row, none from the request.
    assert params["param_namespace"] == NAMESPACE
    assert params["param_workload"] == WORKLOAD
    assert params["param_cluster"] == "cluster-a"
    assert params["param_term"] == "payments"
    assert params["param_min_severity"] == "17"
    # Nothing about the store leaks into the response.
    assert "58199" not in response.text
    assert "drake_reader" not in response.text


async def test_seeing_a_service_is_not_permission_to_read_its_logs(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`environment.view` shows the binding; only `logs.view` opens its logs."""
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, store = logs_harness(tmp_path)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)

    async with harness.api_client() as env_user:
        # Signing in creates the identity a grant attaches to.
        await harness.login(env_user, "user-env")
        await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
        visible = await env_user.get(f"/v1/service-health/bindings/{binding['id']}")
        logs = await env_user.get(f"/v1/service-health/bindings/{binding['id']}/logs")
        unknown = await env_user.get(f"/v1/service-health/bindings/{uuidlib.uuid4()}/logs")

    assert visible.status_code == 200
    # Indistinguishable from a binding that does not exist.
    assert logs.status_code == 404
    assert unknown.status_code == 404
    assert logs.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert store.requests == []


async def test_disabled_logs_are_not_configured_and_query_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, store = logs_harness(tmp_path, enabled=False)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        body = (await client.get(f"/v1/service-health/bindings/{binding['id']}/logs")).json()

    assert body["state"] == "not_configured"
    assert body["entries"] == []
    assert store.requests == []


async def test_a_store_outage_is_unavailable_never_an_empty_page(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, store = logs_harness(tmp_path)
    store.mode = "down"

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        response = await client.get(f"/v1/service-health/bindings/{binding['id']}/logs")

    body = response.json()
    assert response.status_code == 200
    assert body["state"] == "unavailable"
    assert body["reason"] == "store_upstream_error"
    # The upstream message is never relayed.
    assert "Code: 210" not in response.text


async def test_out_of_contract_parameters_are_refused(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    harness, store = logs_harness(tmp_path)

    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
        base = f"/v1/service-health/bindings/{binding['id']}/logs"
        too_wide = await client.get(f"{base}?range=30d")
        bad_severity = await client.get(f"{base}?severity=everything")
        huge_page = await client.get(f"{base}?limit=5000")

    assert too_wide.status_code == 422
    assert bad_severity.status_code == 422
    assert huge_page.status_code == 422
    assert store.requests == []
