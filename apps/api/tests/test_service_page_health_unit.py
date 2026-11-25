"""A service's own page: one computation for the verdict and the metrics
behind it, and never a cold wait when the incident runner computed one in
the last few minutes."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from drake_api.service_health import orchestrator as orch
from drake_api.service_health.orchestrator import (
    DETAIL_RECENT_SECONDS,
    HealthOrchestrator,
    SignalRead,
    signals_from_payload,
)

CONTEXT: dict[str, Any] = {
    "id": "b1",
    "revision": 1,
    "resolved_resource_uid": "uid-1",
    "preset_key": "kubernetes.baseline.v1",
    "health_policy_key": "default.v1",
    "datasource_identity": "ds",
    "project_key": "hermes",
    "environment_key": "test",
    "service_key": "api",
}


class _Cache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.values.get(key)


def _orchestrator(cache: _Cache) -> HealthOrchestrator:
    registry = SimpleNamespace(content_hash="r1")
    return HealthOrchestrator(None, None, registry, cache)  # type: ignore[arg-type]


def _verdict(age_seconds: float, status: str = "healthy") -> dict[str, Any]:
    at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return {"status": status, "computed_at": at.isoformat(), "signals": {}}


class _Computes:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, principal: Any, context: Any, *, refresh: bool = False) -> Any:
        self.calls += 1
        await asyncio.sleep(0.01)
        return _verdict(0, status="computed")


async def test_the_verdict_and_metrics_asked_together_compute_once(monkeypatch: Any) -> None:
    cache = _Cache()
    o = _orchestrator(cache)
    computes = _Computes()
    monkeypatch.setattr(o, "current_health", computes)
    first, second = await asyncio.gather(
        o.detail_health(None, CONTEXT), o.detail_health(None, CONTEXT)
    )  # type: ignore[arg-type]
    assert computes.calls == 1
    assert first["status"] == second["status"] == "computed"


async def test_the_runners_recent_verdict_is_served_at_once_and_recomputed_behind(
    monkeypatch: Any,
) -> None:
    cache = _Cache()
    o = _orchestrator(cache)
    cache.values[o.cache_keys(CONTEXT).recent] = _verdict(40)
    computes = _Computes()
    monkeypatch.setattr(o, "current_health", computes)

    served = await o.detail_health(None, CONTEXT)  # type: ignore[arg-type]
    assert served["status"] == "healthy" and served["cached"] is True
    await asyncio.gather(*list(orch._IN_FLIGHT.values()))
    assert computes.calls == 1


async def test_an_old_verdict_is_not_served_as_current(monkeypatch: Any) -> None:
    cache = _Cache()
    o = _orchestrator(cache)
    cache.values[o.cache_keys(CONTEXT).recent] = _verdict(DETAIL_RECENT_SECONDS + 30)
    computes = _Computes()
    monkeypatch.setattr(o, "current_health", computes)
    served = await o.detail_health(None, CONTEXT)  # type: ignore[arg-type]
    assert served["status"] == "computed"


async def test_a_fresh_verdict_costs_nothing(monkeypatch: Any) -> None:
    cache = _Cache()
    o = _orchestrator(cache)
    cache.values[o.cache_keys(CONTEXT).fresh] = _verdict(5)
    computes = _Computes()
    monkeypatch.setattr(o, "current_health", computes)
    served = await o.detail_health(None, CONTEXT)  # type: ignore[arg-type]
    assert served["status"] == "healthy" and computes.calls == 0


def test_metrics_rebuilt_from_a_stored_verdict_read_the_same() -> None:
    now = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
    reads = {
        "ok": SignalRead("ok", value=3.0, newest_sample_at=now, data_state="ok"),
        "cached": SignalRead("cached", value=1.0, data_state="ok", cache_state="fresh_hit"),
        "stale": SignalRead("stale", value=2.0, data_state="ok", cache_state="stale"),
        "empty": SignalRead("empty", data_state="ok"),
        "failed": SignalRead("failed", failed=True),
        "absent": SignalRead("absent", not_configured=True),
    }
    payload = {"signals": {name: read.to_payload() for name, read in reads.items()}}
    rebuilt = signals_from_payload(payload)
    assert {n: r.to_payload() for n, r in rebuilt.items()} == payload["signals"]
    assert rebuilt["ok"].value == 3.0
