"""The service-health verdict: every rule that must not quietly change.

Pure input/output, so each case states one contract exactly.
"""

from datetime import UTC, datetime, timedelta

import pytest
from drake_api.service_health.engine import (
    ApplicationSignals,
    AvailabilitySignals,
    BindingState,
    ResourceSignals,
    StabilitySignals,
    TelemetryState,
    compute_health,
)
from drake_api.service_health.policy import (
    DEFAULT_POLICY_KEY,
    HealthStatus,
    ReasonCode,
    get_policy,
    policy_keys,
    worst,
)

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
POLICY = get_policy(DEFAULT_POLICY_KEY)
BOUND = BindingState(exists=True, enabled=True, resolved=True)


def evaluate(**overrides):
    """A healthy service, with the named signals replaced."""
    defaults = dict(
        now=NOW,
        policy=POLICY,
        binding=BOUND,
        telemetry=TelemetryState(newest_sample_at=NOW - timedelta(seconds=15)),
        availability=AvailabilitySignals(
            desired_replicas=2,
            ready_replicas=2,
            available_replicas=2,
            generation=4,
            observed_generation=4,
        ),
        stability=StabilitySignals(restarts_in_window=0),
        resources=ResourceSignals(
            cpu_cores_used=0.2,
            cpu_limit_cores=1.0,
            memory_bytes_used=100,
            memory_limit_bytes=1000,
        ),
        application=ApplicationSignals(
            metrics_present=True,
            request_rate=10.0,
            error_ratio=0.0,
            latency_p95_seconds=0.1,
        ),
    )
    defaults.update(overrides)
    return compute_health(**defaults)


def test_a_fully_healthy_service_is_healthy() -> None:
    result = evaluate()
    assert result.status is HealthStatus.HEALTHY
    assert result.reasons == []
    assert result.partial is False


# --- the states that must never read as healthy --------------------------


def test_an_unbound_service_is_not_configured_not_healthy() -> None:
    result = evaluate(binding=BindingState(exists=False))
    assert result.status is HealthStatus.NOT_CONFIGURED
    assert result.reasons == [ReasonCode.NO_BINDING]


def test_a_disabled_binding_is_not_configured() -> None:
    result = evaluate(binding=BindingState(exists=True, enabled=False))
    assert result.status is HealthStatus.NOT_CONFIGURED
    assert ReasonCode.BINDING_DISABLED in result.reasons


def test_a_workload_never_seen_in_inventory_is_unknown() -> None:
    result = evaluate(binding=BindingState(exists=True, enabled=True, resolved=False))
    assert result.status is HealthStatus.UNKNOWN
    assert ReasonCode.BINDING_UNRESOLVED in result.reasons


def test_a_failed_query_blames_drake_not_the_workload() -> None:
    """A query failure says nothing about the service; it must not be critical."""
    result = evaluate(telemetry=TelemetryState(query_failed=True))
    assert result.status is HealthStatus.UNKNOWN
    assert result.reasons == [ReasonCode.QUERY_FAILED]


def test_an_unreachable_datasource_is_unknown_not_critical() -> None:
    result = evaluate(telemetry=TelemetryState(datasource_available=False))
    assert result.status is HealthStatus.UNKNOWN
    assert result.reasons == [ReasonCode.DATASOURCE_UNAVAILABLE]


def test_no_datasource_is_not_configured() -> None:
    result = evaluate(telemetry=TelemetryState(datasource_configured=False))
    assert result.status is HealthStatus.NOT_CONFIGURED


def test_missing_replica_counts_are_unknown_not_zero() -> None:
    """An empty result is not zero replicas."""
    result = evaluate(availability=AvailabilitySignals())
    assert result.availability.status is HealthStatus.UNKNOWN
    assert result.status is not HealthStatus.HEALTHY
    assert "availability.replicas" in result.missing_signals
    assert result.partial is True


# --- freshness -----------------------------------------------------------


def test_stale_telemetry_cannot_report_healthy() -> None:
    old = NOW - timedelta(seconds=POLICY.max_telemetry_age_seconds + 60)
    result = evaluate(telemetry=TelemetryState(newest_sample_at=old))
    assert result.status is HealthStatus.STALE
    assert ReasonCode.TELEMETRY_STALE in result.reasons
    assert result.freshness_age_seconds > POLICY.max_telemetry_age_seconds


def test_stale_telemetry_does_not_soften_a_bad_verdict() -> None:
    """Old evidence of failure is still evidence of failure."""
    old = NOW - timedelta(seconds=POLICY.max_telemetry_age_seconds + 60)
    result = evaluate(
        telemetry=TelemetryState(newest_sample_at=old),
        availability=AvailabilitySignals(desired_replicas=3, ready_replicas=0),
    )
    assert result.status is HealthStatus.CRITICAL
    assert ReasonCode.TELEMETRY_STALE in result.reasons


def test_a_slightly_future_timestamp_is_clock_skew_not_freshness() -> None:
    ahead = NOW + timedelta(seconds=POLICY.future_tolerance_seconds - 5)
    result = evaluate(telemetry=TelemetryState(newest_sample_at=ahead))
    assert result.freshness_age_seconds == 0.0
    assert result.status is HealthStatus.HEALTHY


def test_last_good_data_is_reported_as_stale() -> None:
    result = evaluate(
        telemetry=TelemetryState(
            newest_sample_at=NOW - timedelta(seconds=10), served_from_last_good=True
        )
    )
    assert result.status is HealthStatus.STALE
    assert ReasonCode.TELEMETRY_STALE in result.reasons


def test_no_sample_timestamp_at_all_is_stale() -> None:
    result = evaluate(telemetry=TelemetryState(newest_sample_at=None))
    assert result.status is HealthStatus.STALE


# --- availability boundaries --------------------------------------------


@pytest.mark.parametrize(
    ("desired", "ready", "expected"),
    [
        (3, 3, HealthStatus.HEALTHY),
        (3, 2, HealthStatus.DEGRADED),
        (3, 1, HealthStatus.DEGRADED),
        (3, 0, HealthStatus.CRITICAL),
        (1, 0, HealthStatus.CRITICAL),
        (0, 0, HealthStatus.HEALTHY),  # scaled to zero on purpose
    ],
)
def test_availability_boundaries(desired: int, ready: int, expected: HealthStatus) -> None:
    result = evaluate(
        availability=AvailabilitySignals(desired_replicas=desired, ready_replicas=ready)
    )
    assert result.availability.status is expected


def test_an_unobserved_generation_reports_an_incomplete_rollout() -> None:
    result = evaluate(
        availability=AvailabilitySignals(
            desired_replicas=2, ready_replicas=2, generation=7, observed_generation=6
        )
    )
    assert ReasonCode.ROLLOUT_INCOMPLETE in result.availability.reasons
    assert result.status is HealthStatus.DEGRADED


# --- stability -----------------------------------------------------------


@pytest.mark.parametrize(
    ("restarts", "expected"),
    [
        (0, HealthStatus.HEALTHY),
        (1, HealthStatus.DEGRADED),
        (4, HealthStatus.DEGRADED),
        (5, HealthStatus.CRITICAL),
        (50, HealthStatus.CRITICAL),
    ],
)
def test_restart_boundaries(restarts: int, expected: HealthStatus) -> None:
    result = evaluate(stability=StabilitySignals(restarts_in_window=restarts))
    assert result.stability.status is expected


def test_a_crash_loop_is_critical_on_its_own() -> None:
    result = evaluate(stability=StabilitySignals(restarts_in_window=0, crash_looping=True))
    assert result.status is HealthStatus.CRITICAL
    assert ReasonCode.CRASH_LOOP in result.reasons


def test_an_oom_kill_is_recorded_even_without_restarts() -> None:
    result = evaluate(stability=StabilitySignals(restarts_in_window=0, oom_killed=True))
    assert ReasonCode.OOM_KILLED in result.reasons
    assert result.status is HealthStatus.DEGRADED


# --- resources -----------------------------------------------------------


@pytest.mark.parametrize(
    ("used", "limit", "expected"),
    [
        (0.5, 1.0, HealthStatus.HEALTHY),
        (0.85, 1.0, HealthStatus.DEGRADED),
        (0.94, 1.0, HealthStatus.DEGRADED),
        (0.95, 1.0, HealthStatus.CRITICAL),
        (2.0, 1.0, HealthStatus.CRITICAL),
    ],
)
def test_cpu_utilization_boundaries(used: float, limit: float, expected: HealthStatus) -> None:
    result = evaluate(resources=ResourceSignals(cpu_cores_used=used, cpu_limit_cores=limit))
    assert result.resources.status is expected


def test_usage_without_a_limit_invents_no_ratio() -> None:
    """No limit configured means no pressure claim — not a made-up denominator."""
    result = evaluate(resources=ResourceSignals(cpu_cores_used=9.9, memory_bytes_used=10**12))
    assert result.resources.status is HealthStatus.HEALTHY
    assert result.resources.detail["cpu_utilization"] is None
    assert result.resources.detail["limits_configured"] is False
    assert "resources.limits" in result.missing_signals


def test_no_usage_at_all_is_unknown() -> None:
    result = evaluate(resources=ResourceSignals())
    assert result.resources.status is HealthStatus.UNKNOWN


def test_throttling_is_judged_when_present() -> None:
    result = evaluate(
        resources=ResourceSignals(cpu_cores_used=0.1, cpu_limit_cores=1.0, cpu_throttled_ratio=0.6)
    )
    assert ReasonCode.CPU_THROTTLING in result.resources.reasons
    assert result.resources.status is HealthStatus.CRITICAL


# --- application signals -------------------------------------------------


def test_missing_application_metrics_do_not_make_a_workload_unhealthy() -> None:
    """A service that publishes no HTTP metrics is not thereby failing."""
    result = evaluate(application=ApplicationSignals(metrics_present=False))
    assert result.application.status is HealthStatus.NOT_CONFIGURED
    assert ReasonCode.APPLICATION_METRICS_MISSING in result.reasons
    # The overall verdict is still driven by the signals that do exist.
    assert result.status is not HealthStatus.CRITICAL
    assert "application.golden_signals" in result.missing_signals


def test_a_policy_may_require_application_metrics() -> None:
    from dataclasses import replace

    strict = replace(POLICY, require_application_metrics=True)
    result = evaluate(policy=strict, application=ApplicationSignals(metrics_present=False))
    assert result.application.status is HealthStatus.DEGRADED


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (0.0, HealthStatus.HEALTHY),
        (0.01, HealthStatus.DEGRADED),
        (0.049, HealthStatus.DEGRADED),
        (0.05, HealthStatus.CRITICAL),
    ],
)
def test_error_ratio_boundaries(ratio: float, expected: HealthStatus) -> None:
    result = evaluate(
        application=ApplicationSignals(metrics_present=True, error_ratio=ratio, request_rate=5)
    )
    assert result.application.status is expected


def test_latency_boundaries() -> None:
    for latency, expected in (
        (0.5, HealthStatus.HEALTHY),
        (1.0, HealthStatus.DEGRADED),
        (3.0, HealthStatus.CRITICAL),
    ):
        result = evaluate(
            application=ApplicationSignals(
                metrics_present=True, error_ratio=0.0, latency_p95_seconds=latency
            )
        )
        assert result.application.status is expected, latency


# --- combination and determinism ----------------------------------------


def test_the_worst_signal_decides_the_overall_status() -> None:
    result = evaluate(
        availability=AvailabilitySignals(desired_replicas=2, ready_replicas=1),  # degraded
        stability=StabilitySignals(restarts_in_window=9),  # critical
    )
    assert result.status is HealthStatus.CRITICAL


def test_not_configured_never_masks_a_real_failure() -> None:
    result = evaluate(
        application=ApplicationSignals(metrics_present=False),  # not_configured
        availability=AvailabilitySignals(desired_replicas=2, ready_replicas=0),  # critical
    )
    assert result.status is HealthStatus.CRITICAL


def test_worst_returns_not_configured_only_when_nothing_else_is_present() -> None:
    assert worst(HealthStatus.NOT_CONFIGURED, HealthStatus.NOT_CONFIGURED) is (
        HealthStatus.NOT_CONFIGURED
    )
    assert worst(HealthStatus.NOT_CONFIGURED, HealthStatus.HEALTHY) is HealthStatus.HEALTHY
    assert worst(HealthStatus.STALE, HealthStatus.DEGRADED) is HealthStatus.DEGRADED


def test_the_same_input_always_produces_the_same_output() -> None:
    first = evaluate(stability=StabilitySignals(restarts_in_window=3))
    second = evaluate(stability=StabilitySignals(restarts_in_window=3))
    assert first.to_payload() == second.to_payload()


def test_partial_results_are_flagged_not_hidden() -> None:
    result = evaluate(telemetry=TelemetryState(newest_sample_at=NOW, partial=True))
    assert result.partial is True
    assert ReasonCode.PARTIAL_RESULT in result.reasons


def test_change_from_the_previous_status_is_reported() -> None:
    result = evaluate(previous_status=HealthStatus.CRITICAL)
    assert result.previous_status is HealthStatus.CRITICAL
    assert result.changed is True
    unchanged = evaluate(previous_status=HealthStatus.HEALTHY)
    assert unchanged.changed is False


def test_every_reason_the_engine_can_emit_has_text() -> None:
    """A code with no sentence would reach the UI as a raw identifier."""
    from drake_api.service_health.policy import REASON_TEXT

    for code in ReasonCode:
        assert code in REASON_TEXT, code
        assert REASON_TEXT[code].strip()


def test_payload_carries_no_query_text_or_credentials() -> None:
    payload = str(evaluate().to_payload())
    for leak in ("promql", "sum(", "rate(", "password", "token", "bearer", "http://"):
        assert leak not in payload.lower()


def test_every_declared_policy_loads_and_is_typed() -> None:
    for key in policy_keys():
        policy = get_policy(key)
        assert policy.key == key
        assert policy.max_telemetry_age_seconds > 0
        assert 0 < policy.availability.degraded_ready_ratio <= 1.0
    with pytest.raises(KeyError):
        get_policy("no.such.policy")
