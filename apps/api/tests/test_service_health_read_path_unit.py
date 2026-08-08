"""The health read path, without a cluster or a Prometheus.

These tests exist for one reason: the orchestrator sits between a provider
that answers vaguely (empty results, timeouts, partial matrices) and an
engine that answers precisely. Every ambiguity has to be resolved here, and
resolving one of them the wrong way produces a dashboard that lies quietly
— a service reported healthy because nothing was measured, or critical
because Drake's own datasource was down.

So each test below pins one of those distinctions.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from drake_api.service_health.cache import (
    HealthCache,
    as_last_good,
    assert_payload_safe,
    build_health_cache_keys,
    datasource_identity,
)
from drake_api.service_health.orchestrator import (
    HealthOrchestrator,
    SignalRead,
    summarize_signals,
)
from drake_api.service_health.policy import HealthStatus
from drake_api.service_health.presets import HTTP_SERVICE, KUBERNETES_BASELINE, get_preset
from fastapi import HTTPException

# Relative to the real clock: `current_health` reads the wall clock itself,
# and a fixed date would make every sample look months stale.
NOW = datetime.now(UTC).replace(microsecond=0)
NOW_TS = int(NOW.timestamp())


# --- doubles -------------------------------------------------------------


def envelope(
    template_key: str,
    value: float | None,
    *,
    data_state: str = "ok",
    cache_state: str = "miss",
    partial: bool = False,
    sample_at: int | None = None,
) -> dict[str, Any]:
    """A broker envelope shaped exactly like the real one."""
    series = (
        []
        if value is None
        else [{"labels": {"namespace": "hermes-dev"}, "points": [[sample_at or NOW_TS, value]]}]
    )
    return {
        "template_key": template_key,
        "template_version": 1,
        "metric_key": "workload.example",
        "scope": {"type": "workload", "ref": "p/e/s/c/ns/wl"},
        "unit": "count",
        "result_type": "timeseries",
        "range": {
            "from": (NOW - timedelta(seconds=300)).isoformat(),
            "to": NOW.isoformat(),
            "requested_step_seconds": 30,
            "effective_step_seconds": 30,
            "step_adjusted": False,
        },
        "source_type": "prometheus",
        "series": series,
        "data_state": data_state if series or data_state != "ok" else "ok",
        "cache_state": cache_state,
        "partial": partial,
        "warnings": [],
        "as_of": NOW.isoformat(),
        "correlation_id": "test",
    }


class FakeBroker:
    """Answers per template key, with switchable failure modes."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.cancelled = 0
        self.hold = False

    async def query(self, principal: Any, request: Any) -> dict[str, Any]:
        self.calls.append(request.template_key)
        self.started.set()
        if self.hold:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        answer = self.answers.get(request.template_key, "empty")
        if isinstance(answer, BaseException):
            raise answer
        if answer == "empty":
            return envelope(request.template_key, None, data_state="empty")
        if answer == "not_configured":
            return envelope(request.template_key, None, data_state="not_configured")
        if isinstance(answer, dict):
            return answer
        return envelope(request.template_key, float(answer))


class FakeRegistry:
    content_hash = "registry-hash-for-tests"


class FakeRedis:
    """Enough Redis for the cache, plus a record of what was deleted."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.store.pop(key, None)


def context(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": str(uuid.uuid4()),
        "lifecycle": "active",
        "resolved_resource_uid": "uid-1",
        "resolved": True,
        "resolved_at": NOW.isoformat(),
        "preset_key": KUBERNETES_BASELINE.key,
        "health_policy_key": "default.v1",
        "revision": 1,
        "namespace": "hermes-dev",
        "workload_kind": "Deployment",
        "workload_name": "hermes-api",
        "cluster_ref": "cl-1",
        "cluster_id": str(uuid.uuid4()),
        "project_key": "pilot",
        "environment_key": "dev",
        "service_key": "api",
        "environment_service_id": str(uuid.uuid4()),
        "datasource_configured": True,
        "datasource_identity": "ds-1",
    }
    base.update(overrides)
    return base


def orchestrator(broker: FakeBroker, redis: FakeRedis | None = None) -> HealthOrchestrator:
    return HealthOrchestrator(
        engine=None,  # type: ignore[arg-type] - load_context is not exercised here
        broker=broker,  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
        cache=HealthCache(redis or FakeRedis()),  # type: ignore[arg-type]
    )


HEALTHY_BASELINE = {
    "workload.replicas-desired.v1": 3,
    "workload.replicas-ready.v1": 3,
    "workload.restarts-delta.v1": 0,
    "workload.cpu-usage.v1": 0.4,
    "workload.cpu-limit.v1": 2.0,
    "workload.memory-usage.v1": 200_000_000,
    "workload.memory-limit.v1": 1_000_000_000,
    "workload.cpu-throttling.v1": 0.0,
    "workload.telemetry-freshness.v1": 5,
}


# --- empty is not zero ---------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_replica_result_is_missing_not_zero_replicas() -> None:
    """The failure this whole layer is built to prevent.

    A provider that returns no points for `replicas-ready` has told us
    nothing. Reading that as `0 ready` would report a perfectly healthy
    service as having no ready replicas — critical, paging, and wrong.
    """
    broker = FakeBroker({**HEALTHY_BASELINE, "workload.replicas-ready.v1": "empty"})
    result, signals = await orchestrator(broker).compute(None, context(), now=NOW)

    assert signals["ready_replicas"].value is None
    assert signals["ready_replicas"].state == "empty"
    assert "availability.replicas" in result.missing_signals
    assert result.availability.status is HealthStatus.UNKNOWN
    assert "no_ready_replicas" not in [str(r) for r in result.reasons]
    assert result.status is not HealthStatus.CRITICAL


@pytest.mark.asyncio
async def test_a_measured_zero_is_still_reported_as_a_failure() -> None:
    """The other half of the same distinction.

    An absent value must not become 0 — but a real 0 must not be softened
    into "unknown" either, or a genuinely dead service would read as merely
    unobserved.
    """
    broker = FakeBroker({**HEALTHY_BASELINE, "workload.replicas-ready.v1": 0})
    result, signals = await orchestrator(broker).compute(None, context(), now=NOW)

    assert signals["ready_replicas"].value == 0.0
    assert result.availability.status is HealthStatus.CRITICAL
    assert "no_ready_replicas" in [str(r) for r in result.reasons]


@pytest.mark.asyncio
async def test_a_workload_scaled_to_zero_on_purpose_is_not_critical() -> None:
    broker = FakeBroker(
        {**HEALTHY_BASELINE, "workload.replicas-desired.v1": 0, "workload.replicas-ready.v1": 0}
    )
    result, _ = await orchestrator(broker).compute(None, context(), now=NOW)
    assert result.availability.status is HealthStatus.HEALTHY
    assert result.availability.detail["scaled_to_zero"] is True


# --- a Drake failure is not a workload failure ---------------------------


@pytest.mark.asyncio
async def test_one_failed_optional_query_is_partial_not_critical() -> None:
    """A single unavailable metric must not manufacture an incident."""
    broker = FakeBroker(
        {**HEALTHY_BASELINE, "workload.cpu-throttling.v1": HTTPException(503, "provider down")}
    )
    result, signals = await orchestrator(broker).compute(None, context(), now=NOW)

    assert signals["cpu_throttling"].state == "failed"
    assert result.partial is True
    assert "partial_result" in [str(r) for r in result.reasons]
    assert result.status is HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_every_query_failing_is_unknown_not_critical() -> None:
    """A datasource outage is Drake's problem, and says so."""
    broker = FakeBroker({key: HTTPException(503, "provider down") for key in HEALTHY_BASELINE})
    result, _ = await orchestrator(broker).compute(None, context(), now=NOW)

    assert result.status is HealthStatus.UNKNOWN
    assert [str(r) for r in result.reasons] == ["datasource_unavailable"]


@pytest.mark.asyncio
async def test_an_unconfigured_datasource_is_its_own_state() -> None:
    broker = FakeBroker({key: "not_configured" for key in HEALTHY_BASELINE})
    result, _ = await orchestrator(broker).compute(None, context(), now=NOW)

    assert result.status is HealthStatus.NOT_CONFIGURED
    assert [str(r) for r in result.reasons] == ["datasource_not_configured"]


@pytest.mark.asyncio
async def test_the_four_unavailability_reasons_stay_distinct() -> None:
    """Missing metric, empty result, query failure and no datasource.

    Four different things happened; four different states come back. Any
    two of them collapsing into one would leave an operator unable to tell
    "fix your app" from "fix Drake".
    """
    broker = FakeBroker(
        {
            **HEALTHY_BASELINE,
            "workload.restarts-delta.v1": "empty",
            "workload.cpu-throttling.v1": HTTPException(500, "boom"),
            "workload.memory-limit.v1": "not_configured",
        }
    )
    _, signals = await orchestrator(broker).compute(None, context(), now=NOW)

    assert signals["restarts"].state == "empty"
    assert signals["cpu_throttling"].state == "failed"
    assert signals["memory_limit"].state == "not_configured"
    assert signals["cpu_usage"].state == "ok"
    # A signal the preset never reads is absent entirely — a fifth state,
    # and not the same as any of the four above.
    assert "request_rate" not in signals


@pytest.mark.asyncio
async def test_a_provider_exception_that_is_not_an_http_error_is_still_only_failed() -> None:
    broker = FakeBroker({**HEALTHY_BASELINE, "workload.cpu-usage.v1": RuntimeError("kaboom")})
    result, signals = await orchestrator(broker).compute(None, context(), now=NOW)
    assert signals["cpu_usage"].state == "failed"
    assert result.status is not HealthStatus.CRITICAL


# --- staleness -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_last_good_broker_response_produces_a_stale_verdict() -> None:
    """Health served from the broker's last-good cache says so."""
    answers = dict(HEALTHY_BASELINE)
    answers["workload.replicas-ready.v1"] = envelope(
        "workload.replicas-ready.v1", 3, data_state="stale", cache_state="stale"
    )
    result, signals = await orchestrator(FakeBroker(answers)).compute(None, context(), now=NOW)

    assert signals["ready_replicas"].state == "stale"
    assert result.status is HealthStatus.STALE
    assert "telemetry_stale" in [str(r) for r in result.reasons]


@pytest.mark.asyncio
async def test_telemetry_older_than_the_policy_allows_is_stale() -> None:
    old = NOW_TS - 4000  # the default policy tolerates 300s
    answers = {
        key: envelope(key, float(value), sample_at=old) for key, value in HEALTHY_BASELINE.items()
    }
    result, _ = await orchestrator(FakeBroker(answers)).compute(None, context(), now=NOW)
    assert result.status is HealthStatus.STALE
    assert result.freshness_age_seconds is not None
    assert result.freshness_age_seconds > 300


# --- binding states ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_disabled_or_unresolved_binding_is_answered_without_querying() -> None:
    """No provider round-trip for a verdict that is already determined."""
    broker = FakeBroker(HEALTHY_BASELINE)
    orch = orchestrator(broker)

    disabled, _ = await orch.compute(None, context(lifecycle="disabled"), now=NOW)
    assert disabled.status is HealthStatus.NOT_CONFIGURED
    assert [str(r) for r in disabled.reasons] == ["binding_disabled"]

    unresolved, _ = await orch.compute(
        None, context(resolved=False, resolved_resource_uid=None), now=NOW
    )
    assert unresolved.status is HealthStatus.UNKNOWN
    assert [str(r) for r in unresolved.reasons] == ["binding_unresolved"]

    assert broker.calls == []


@pytest.mark.asyncio
async def test_an_unbound_service_is_not_configured_rather_than_healthy() -> None:
    result, signals = await orchestrator(FakeBroker({})).compute(None, None, now=NOW)
    assert result.status is HealthStatus.NOT_CONFIGURED
    assert [str(r) for r in result.reasons] == ["no_binding"]
    assert signals == {}


# --- golden signals ------------------------------------------------------


@pytest.mark.asyncio
async def test_an_application_publishing_no_http_metrics_is_not_penalised() -> None:
    """Not publishing RED metrics is a fact about the service, not a fault."""
    answers = {
        **HEALTHY_BASELINE,
        "workload.request-rate.v1": "empty",
        "workload.error-ratio.v1": "empty",
        "workload.latency-p95.v1": "empty",
    }
    result, _ = await orchestrator(FakeBroker(answers)).compute(
        None, context(preset_key=HTTP_SERVICE.key), now=NOW
    )
    assert result.application.status is HealthStatus.NOT_CONFIGURED
    assert "application.golden_signals" in result.missing_signals
    assert result.status is not HealthStatus.CRITICAL


@pytest.mark.asyncio
async def test_a_high_error_ratio_is_critical_when_the_app_does_publish() -> None:
    answers = {
        **HEALTHY_BASELINE,
        "workload.request-rate.v1": 50.0,
        "workload.error-ratio.v1": 0.4,
        "workload.latency-p95.v1": 0.2,
    }
    result, _ = await orchestrator(FakeBroker(answers)).compute(
        None, context(preset_key=HTTP_SERVICE.key), now=NOW
    )
    assert result.status is HealthStatus.CRITICAL
    assert "high_error_rate" in [str(r) for r in result.reasons]


# --- cancellation --------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelling_a_health_read_cancels_every_query_it_started() -> None:
    """No orphan queries: a client that leaves takes its provider load with it."""
    broker = FakeBroker(HEALTHY_BASELINE)
    broker.hold = True
    orch = orchestrator(broker)

    task = asyncio.create_task(orch.compute(None, context(), now=NOW))
    await asyncio.wait_for(broker.started.wait(), timeout=2)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert broker.cancelled >= 1
    # Nothing is still running: every task the orchestrator created was
    # awaited on the way out.
    assert all(t.done() for t in asyncio.all_tasks() if t is not asyncio.current_task())


# --- metric summary ------------------------------------------------------


@pytest.mark.asyncio
async def test_the_metric_summary_reports_null_never_zero() -> None:
    broker = FakeBroker({**HEALTHY_BASELINE, "workload.cpu-usage.v1": "empty"})
    _, signals = await orchestrator(broker).compute(None, context(), now=NOW)
    summary = summarize_signals(signals, KUBERNETES_BASELINE)

    assert summary["resources"]["cpu_usage"]["value"] is None
    assert summary["resources"]["cpu_usage"]["state"] == "empty"
    # With no numerator there is no utilization to report — not 0%.
    assert summary["resources"]["cpu_utilization"] is None
    # A signal this preset does not read at all is its own state.
    assert summary["application"]["request_rate"]["state"] == "not_collected"
    assert summary["application"]["request_rate"]["value"] is None


def test_utilization_is_absent_when_no_limit_is_configured() -> None:
    signals = {
        "cpu_usage": SignalRead("workload.cpu-usage.v1", value=1.5, data_state="ok"),
        "cpu_limit": SignalRead("workload.cpu-limit.v1", value=None, data_state="empty"),
    }
    summary = summarize_signals(signals, KUBERNETES_BASELINE)
    assert summary["resources"]["cpu_usage"]["value"] == 1.5
    assert summary["resources"]["cpu_utilization"] is None


def test_a_zero_limit_does_not_divide() -> None:
    signals = {
        "memory_usage": SignalRead("workload.memory-usage.v1", value=100.0),
        "memory_limit": SignalRead("workload.memory-limit.v1", value=0.0),
    }
    summary = summarize_signals(signals, KUBERNETES_BASELINE)
    assert summary["resources"]["memory_utilization"] is None


# --- cache identity ------------------------------------------------------


def keys_for(**overrides: Any) -> Any:
    base = dict(
        registry_hash="rh",
        binding_id="b-1",
        revision=1,
        resolved_resource_uid="uid-1",
        preset_key="kubernetes.baseline.v1",
        policy_key="default.v1",
        datasource_identity="ds-1",
        project_key="pilot",
        environment_key="dev",
        service_key="api",
        window_seconds=300,
        step_seconds=30,
    )
    base.update(overrides)
    return build_health_cache_keys(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "change",
    [
        {"revision": 2},
        {"preset_key": "http.service.v1"},
        {"policy_key": "tolerant.v1"},
        {"datasource_identity": "ds-2"},
        {"resolved_resource_uid": "uid-2"},
        {"registry_hash": "rh-2"},
        {"environment_key": "prod"},
        {"service_key": "worker"},
        {"project_key": "other"},
        {"window_seconds": 900},
        {"step_seconds": 60},
    ],
)
def test_anything_a_verdict_depends_on_changes_its_cache_key(change: dict[str, Any]) -> None:
    """Invalidation by identity.

    Every one of these changes what "healthy" would mean, so every one of
    them must make the previous answer unaddressable rather than merely
    scheduled for deletion.
    """
    assert keys_for().fresh != keys_for(**change).fresh
    assert keys_for().last_good != keys_for(**change).last_good


def test_the_same_inputs_produce_the_same_key() -> None:
    assert keys_for().fresh == keys_for().fresh


def test_fresh_and_last_good_are_different_keys() -> None:
    assert keys_for().fresh != keys_for().last_good


def test_a_config_ref_is_hashed_and_never_reproduced() -> None:
    identity = datasource_identity("int-1", "secret/prometheus-basic-auth", "configured")
    assert "secret/prometheus-basic-auth" not in identity
    assert identity != datasource_identity("int-1", "different-ref", "configured")
    assert datasource_identity(None, None, "") == "none"


def test_a_provider_shaped_payload_is_refused_by_the_cache() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        assert_payload_safe({"status": "healthy", "detail": {"provider_url": "http://prom"}})
    with pytest.raises(ValueError, match="unsafe"):
        assert_payload_safe({"promql": "up{job='x'}"})
    assert_payload_safe({"status": "healthy", "reasons": []})


# --- last-good honesty ---------------------------------------------------


def test_last_good_keeps_the_time_it_was_actually_computed() -> None:
    """The one dishonest thing this layer must never do.

    Restamping `computed_at` would present an hour-old answer as current,
    which is worse than showing nothing.
    """
    computed = NOW - timedelta(hours=1)
    served = as_last_good(
        {"status": "healthy", "computed_at": computed.isoformat(), "reasons": []}, now=NOW
    )
    assert served["computed_at"] == computed.isoformat()
    assert served["served_at"] == NOW.isoformat()
    assert served["age_seconds"] == 3600.0
    assert served["served_from_last_good"] is True
    assert served["partial"] is True


def test_stale_evidence_cannot_support_a_healthy_verdict() -> None:
    served = as_last_good({"status": "healthy", "computed_at": NOW.isoformat()}, now=NOW)
    assert served["status"] == "stale"
    assert "telemetry_stale" in served["reasons"]


def test_stale_evidence_still_supports_a_bad_one() -> None:
    """A service that was failing when last seen is not fixed by time passing."""
    for status in ("critical", "degraded"):
        served = as_last_good({"status": status, "computed_at": NOW.isoformat()}, now=NOW)
        assert served["status"] == status
        assert "telemetry_stale" in served["reasons"]


@pytest.mark.asyncio
async def test_a_failed_read_serves_last_good_and_does_not_delete_it() -> None:
    """The property that makes last-good worth having.

    A provider outage must not consume the last real answer: the first
    failure serves it, and so does the second.
    """
    redis = FakeRedis()
    ctx = context()

    good = orchestrator(FakeBroker(HEALTHY_BASELINE), redis)
    first = await good.current_health(None, ctx)
    assert first["status"] == "healthy"
    assert first["cached"] is False

    keys = good.cache_keys(ctx)
    assert keys.last_good in redis.store

    # The datasource goes away. The verdict must not become "critical", and
    # the stored last-good must survive being read.
    down = FakeBroker({k: HTTPException(503, "down") for k in HEALTHY_BASELINE})
    broken = orchestrator(down, redis)
    outage = await broken.current_health(None, ctx, refresh=True)
    assert outage["status"] == "stale"
    assert outage["served_from_last_good"] is True
    assert outage["computed_at"] == first["computed_at"]
    assert keys.last_good in redis.store
    assert redis.deleted == []

    again = await broken.current_health(None, ctx, refresh=True)
    assert again["served_from_last_good"] is True


@pytest.mark.asyncio
async def test_an_outage_with_no_last_good_reports_unknown_rather_than_inventing_one() -> None:
    redis = FakeRedis()
    down = FakeBroker({k: HTTPException(503, "down") for k in HEALTHY_BASELINE})
    broken = orchestrator(down, redis)
    result = await broken.current_health(None, context())
    assert result["status"] == "unknown"
    assert result["served_from_last_good"] is False


@pytest.mark.asyncio
async def test_a_mutated_binding_cannot_reach_the_previous_verdict() -> None:
    """What "invalidate on binding mutation" actually means here."""
    redis = FakeRedis()
    ctx = context()
    orch = orchestrator(FakeBroker(HEALTHY_BASELINE), redis)
    await orch.current_health(None, ctx)

    # A lifecycle change, a preset change or a policy change all bump the
    # revision; the next read computes a different key and misses.
    mutated = context(id=ctx["id"], revision=2)
    assert await HealthCache(redis).get(orch.cache_keys(mutated).fresh) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_second_read_is_served_from_cache_without_querying_again() -> None:
    redis = FakeRedis()
    broker = FakeBroker(HEALTHY_BASELINE)
    orch = orchestrator(broker, redis)
    ctx = context()

    await orch.current_health(None, ctx)
    first_calls = len(broker.calls)
    cached = await orch.current_health(None, ctx)

    assert cached["cached"] is True
    assert len(broker.calls) == first_calls


@pytest.mark.asyncio
async def test_refresh_bypasses_the_cache() -> None:
    redis = FakeRedis()
    broker = FakeBroker(HEALTHY_BASELINE)
    orch = orchestrator(broker, redis)
    ctx = context()

    await orch.current_health(None, ctx)
    before = len(broker.calls)
    await orch.current_health(None, ctx, refresh=True)
    assert len(broker.calls) > before


@pytest.mark.asyncio
async def test_invalidate_drops_only_the_fresh_verdict() -> None:
    """Last-good outlives invalidation; that is the point of keeping it."""
    redis = FakeRedis()
    orch = orchestrator(FakeBroker(HEALTHY_BASELINE), redis)
    ctx = context()
    await orch.current_health(None, ctx)
    keys = orch.cache_keys(ctx)

    await orch.invalidate(ctx)
    assert keys.fresh not in redis.store
    assert keys.last_good in redis.store


# --- bounded series ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_signal_the_preset_does_not_read_has_no_series() -> None:
    """The allowlist is the preset. There is no other way in."""
    orch = orchestrator(FakeBroker(HEALTHY_BASELINE))
    for signal in ("request_rate", "definitely_not_a_signal", "__class__"):
        with pytest.raises(HTTPException) as error:
            await orch.read_series(None, context(), signal, 3600, 60)
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_series_labels_are_filtered_and_series_count_is_capped() -> None:
    many = {
        "series": [
            {
                "labels": {"namespace": "hermes-dev", "pod": f"p-{index}", "__name__": "secret"},
                "points": [[NOW_TS, 1.0]],
            }
            for index in range(40)
        ],
        "unit": "cores",
        "data_state": "ok",
        "cache_state": "miss",
        "partial": False,
        "warnings": [],
        "range": {},
        "as_of": NOW.isoformat(),
    }
    orch = orchestrator(FakeBroker({"workload.cpu-usage.v1": many}))
    result = await orch.read_series(None, context(), "cpu_usage", 3600, 60)

    assert len(result["series"]) == 12
    assert result["series_truncated"] is True
    # Truncation is disclosed, not silent.
    assert result["partial"] is True
    assert all("__name__" not in entry["labels"] for entry in result["series"])
    assert result["series"][0]["labels"] == {"namespace": "hermes-dev", "pod": "p-0"}


@pytest.mark.asyncio
async def test_a_series_request_carries_no_query_and_no_template_key() -> None:
    """What comes back describes a signal, never how it was obtained."""
    orch = orchestrator(FakeBroker(HEALTHY_BASELINE))
    result = await orch.read_series(None, context(), "cpu_usage", 3600, 60)
    serialized = str(result)
    assert "workload.cpu-usage.v1" not in serialized
    assert "sum(" not in serialized
    assert result["signal"] == "cpu_usage"


def test_every_preset_signal_maps_to_a_registry_template() -> None:
    """A preset naming a template that does not exist would fail at read time."""
    from drake_api.telemetry.registry import find_template, get_registry

    registry = get_registry()
    for key in ("kubernetes.baseline.v1", "http.service.v1", "hermes.pilot.v1"):
        preset = get_preset(key)
        for template_key in preset.template_keys():
            template = find_template(registry, template_key)
            assert template is not None, f"{key} names missing template {template_key}"
            assert "workload" in template.scope_types
