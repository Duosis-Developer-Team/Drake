"""Health templates from OpenTelemetry metrics, through the real broker
(ADR-0038 phase 3).

Real PostgreSQL, Redis, RBAC, bindings and broker; Prometheus and
ClickHouse are fakes behind the production adapters' transports.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import seed_catalog_world
from test_service_health_read_api_integration import create_binding, owner, seed_workload
from test_telemetry_api_integration import (
    FAKE_CONNECTOR_URL,
    FakeProvider,
    configure_alpha_prometheus,
    engine,
    migrated_db,
)

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]


class FakeClickHouse:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.mode = "ok"

    def handler(self, request: httpx.Request) -> httpx.Response:
        sql = request.content.decode()
        self.statements.append(sql)
        if self.mode == "down":
            return httpx.Response(503, text="Code: 210. connection refused")
        # The metric name is a typed parameter, never part of the SQL.
        if request.url.params.get("param_metric") == "k8s.deployment.available":
            return httpx.Response(200, json={"data": [{"k": "0", "v": 2}, {"k": "2", "v": 3}]})
        return httpx.Response(200, json={"data": []})


def _harness(tmp_path: Path, templates: list[str]) -> tuple[Any, FakeProvider, FakeClickHouse]:
    from drake_api.settings import ObservabilityStoreConnector, TelemetryConnector
    from harness_s1 import build_harness, require_it_settings

    password_file = tmp_path / "reader"
    password_file.write_text("it-reader", encoding="utf-8")
    provider, store = FakeProvider(), FakeClickHouse()
    settings = require_it_settings().model_copy(
        update={
            "telemetry_connectors": {"it-fake": TelemetryConnector(url=FAKE_CONNECTOR_URL)},
            "telemetry_fresh_ttl_override_seconds": 60,
            "observability_store": ObservabilityStoreConnector(
                url="http://127.0.0.1:58199",
                username="drake_reader",
                password_file=str(password_file),
            ),
            "otel_metrics_clusters": ["cluster-a"],
            "otel_metrics_templates": templates,
        }
    )
    harness = build_harness(
        settings,
        telemetry_transport=httpx.MockTransport(provider.handler),
        observability_transport=httpx.MockTransport(store.handler),
    )
    return harness, provider, store


async def _query(
    engine: AsyncEngine, harness: Any, template: str, binding_id: str
) -> dict[str, Any]:
    """As the health engine asks: the system evaluator, workload scope."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from drake_api.incidents.system_actor import ensure_system_evaluator
    from drake_api.telemetry.broker import QueryInput

    principal = await ensure_system_evaluator(engine)
    now = datetime.now(UTC).replace(microsecond=0)
    result: dict[str, Any] = await harness.app.state.telemetry_broker.query(
        principal,
        QueryInput(
            template_key=template,
            scope_type="workload",
            scope_id=uuid.UUID(binding_id),
            from_dt=now - timedelta(minutes=30),
            to_dt=now,
            step_seconds=60,
            parameters={},
        ),
    )
    return result


async def _world(
    engine: AsyncEngine, harness: Any, *, prometheus: bool = True
) -> tuple[dict[str, Any], str | None]:
    world = await seed_catalog_world(engine)
    await seed_workload(engine, world["cluster_a"].id)
    integration_id = await configure_alpha_prometheus(engine, world) if prometheus else None
    async with owner(harness, engine) as client:
        binding = await create_binding(client, world)
    return binding, integration_id


async def test_a_switched_template_is_answered_from_opentelemetry(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, provider, store = _harness(tmp_path, ["workload.replicas-ready.v1"])
    binding, _ = await _world(engine, harness)

    envelope = await _query(engine, harness, "workload.replicas-ready.v1", binding["id"])
    desired = await _query(engine, harness, "workload.replicas-desired.v1", binding["id"])

    assert envelope["source_type"] == "opentelemetry"
    assert envelope["data_state"] == "ok"
    values = [p[1] for p in envelope["series"][0]["points"]]
    assert values[:3] == [2.0, 2.0, 3.0]  # carried forward, then the new count
    # The switched template never reached Prometheus; the other one did.
    assert store.statements and all("k8s.deployment.available" not in s for s in store.statements)
    assert desired["source_type"] == "prometheus"
    assert provider.calls == 1


async def test_with_nothing_switched_prometheus_answers_as_before(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, provider, store = _harness(tmp_path, [])
    binding, _ = await _world(engine, harness)

    response = await _query(engine, harness, "workload.replicas-ready.v1", binding["id"])

    assert response["source_type"] == "prometheus"
    assert provider.calls == 1
    assert store.statements == []


async def test_a_store_outage_is_not_a_prometheus_outage(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, provider, store = _harness(tmp_path, ["workload.replicas-ready.v1"])
    binding, integration_id = await _world(engine, harness)
    store.mode = "down"

    try:
        response = await _query(engine, harness, "workload.replicas-ready.v1", binding["id"])
        assert response["data_state"] in ("unavailable", "stale")
    except Exception as error:  # no last-good yet: the broker answers 503
        assert getattr(error, "status_code", None) == 503
    assert provider.calls == 0
    async with engine.connect() as connection:
        error_code = (
            await connection.execute(
                text("SELECT last_error_code FROM integrations WHERE id = :id"),
                {"id": integration_id},
            )
        ).scalar_one()
    # The Prometheus integration's health was not touched by the store.
    assert error_code is None


async def test_without_prometheus_a_switched_template_still_answers(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A cluster with only the Tracesis agent: no Prometheus integration."""
    harness, provider, _store = _harness(tmp_path, ["workload.replicas-ready.v1"])
    binding, _ = await _world(engine, harness, prometheus=False)

    answered = await _query(engine, harness, "workload.replicas-ready.v1", binding["id"])
    other = await _query(engine, harness, "workload.replicas-desired.v1", binding["id"])

    assert answered["source_type"] == "opentelemetry"
    assert answered["data_state"] == "ok"
    # What OpenTelemetry does not answer is reported honestly, not guessed.
    assert other["data_state"] == "not_configured"
    assert provider.calls == 0
