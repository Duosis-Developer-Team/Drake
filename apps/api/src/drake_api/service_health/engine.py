"""Turn observed signals into one service-health answer.

Pure and deterministic: signals in, verdict out. No I/O, no clock reads
beyond the `now` it is handed, so the whole judgement is testable at a
boundary without a cluster or a Prometheus.

The rules it enforces, stated once here because they are easy to lose in
the details:

- An empty result is not zero.
- A missing metric is not healthy.
- A failed query is a Drake problem, not a workload problem.
- Stale telemetry is not live telemetry.
- One missing optional signal does not make a service critical.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from drake_api.service_health.policy import (
    REASON_TEXT,
    HealthPolicy,
    HealthStatus,
    ReasonCode,
    worst,
)


@dataclass(frozen=True)
class AvailabilitySignals:
    """What the cluster says about replicas. `None` means "not observed"."""

    desired_replicas: int | None = None
    ready_replicas: int | None = None
    available_replicas: int | None = None
    updated_replicas: int | None = None
    generation: int | None = None
    observed_generation: int | None = None


@dataclass(frozen=True)
class StabilitySignals:
    restarts_in_window: float | None = None
    crash_looping: bool = False
    oom_killed: bool = False


@dataclass(frozen=True)
class ResourceSignals:
    cpu_cores_used: float | None = None
    cpu_limit_cores: float | None = None
    memory_bytes_used: float | None = None
    memory_limit_bytes: float | None = None
    cpu_throttled_ratio: float | None = None


@dataclass(frozen=True)
class ApplicationSignals:
    request_rate: float | None = None
    error_ratio: float | None = None
    latency_p95_seconds: float | None = None
    # Distinguishes "the app publishes nothing" from "we could not read it".
    metrics_present: bool = False


@dataclass(frozen=True)
class TelemetryState:
    """How the read itself went, separate from what it said."""

    datasource_configured: bool = True
    datasource_available: bool = True
    query_failed: bool = False
    partial: bool = False
    # Newest sample timestamp across the signals that were read.
    newest_sample_at: datetime | None = None
    served_from_last_good: bool = False


@dataclass(frozen=True)
class BindingState:
    exists: bool = False
    enabled: bool = False
    resolved: bool = False


@dataclass
class SectionResult:
    status: HealthStatus
    reasons: list[ReasonCode] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthResult:
    status: HealthStatus
    computed_at: datetime
    newest_sample_at: datetime | None
    freshness_age_seconds: float | None
    availability: SectionResult
    stability: SectionResult
    resources: SectionResult
    application: SectionResult
    reasons: list[ReasonCode]
    messages: list[str]
    missing_signals: list[str]
    partial: bool
    policy_key: str
    binding_id: str | None
    previous_status: HealthStatus | None = None
    changed: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        def section(name: str, result: SectionResult) -> dict[str, Any]:
            return {
                "status": str(result.status),
                "reasons": [str(r) for r in result.reasons],
                **result.detail,
            }

        return {
            "status": str(self.status),
            "computed_at": self.computed_at.isoformat(),
            "newest_sample_at": (
                self.newest_sample_at.isoformat() if self.newest_sample_at else None
            ),
            "freshness_age_seconds": self.freshness_age_seconds,
            "availability": section("availability", self.availability),
            "stability": section("stability", self.stability),
            "resources": section("resources", self.resources),
            "application": section("application", self.application),
            "reasons": [str(r) for r in self.reasons],
            "messages": self.messages,
            "missing_signals": self.missing_signals,
            "partial": self.partial,
            "policy_key": self.policy_key,
            "binding_id": self.binding_id,
            "previous_status": str(self.previous_status) if self.previous_status else None,
            "changed": self.changed,
        }


def _ratio(used: float | None, limit: float | None) -> float | None:
    """Utilization, or None when there is nothing to compare against.

    A workload with no limit set is not judged on a ratio — inventing a
    denominator would manufacture pressure that was never configured.
    """
    if used is None or limit is None or limit <= 0:
        return None
    return used / limit


def _evaluate_availability(
    signals: AvailabilitySignals, policy: HealthPolicy, missing: list[str]
) -> SectionResult:
    desired, ready = signals.desired_replicas, signals.ready_replicas
    detail: dict[str, Any] = {
        "desired_replicas": desired,
        "ready_replicas": ready,
        "available_replicas": signals.available_replicas,
        "updated_replicas": signals.updated_replicas,
    }

    if desired is None or ready is None:
        missing.append("availability.replicas")
        return SectionResult(HealthStatus.UNKNOWN, [], detail)

    reasons: list[ReasonCode] = []
    status = HealthStatus.HEALTHY

    # A workload scaled to zero on purpose is not unhealthy; it is simply
    # not running, and there is nothing to be ready.
    if desired == 0:
        detail["scaled_to_zero"] = True
        return SectionResult(HealthStatus.HEALTHY, [], detail)

    ratio = ready / desired
    detail["ready_ratio"] = ratio
    if ratio <= policy.availability.critical_ready_ratio:
        status = HealthStatus.CRITICAL
        reasons.append(ReasonCode.NO_READY_REPLICAS)
    elif ratio < policy.availability.degraded_ready_ratio:
        status = HealthStatus.DEGRADED
        reasons.append(ReasonCode.PARTIAL_AVAILABILITY)

    # A controller that has not observed its own latest spec is mid-rollout.
    if (
        signals.generation is not None
        and signals.observed_generation is not None
        and signals.observed_generation < signals.generation
    ):
        reasons.append(ReasonCode.ROLLOUT_INCOMPLETE)
        status = worst(status, HealthStatus.DEGRADED)

    return SectionResult(status, reasons, detail)


def _evaluate_stability(
    signals: StabilitySignals, policy: HealthPolicy, missing: list[str]
) -> SectionResult:
    detail: dict[str, Any] = {
        "restarts_in_window": signals.restarts_in_window,
        "crash_looping": signals.crash_looping,
        "oom_killed": signals.oom_killed,
    }
    reasons: list[ReasonCode] = []
    status = HealthStatus.HEALTHY

    # These two are observations, not counts: they are true or they are not.
    if signals.crash_looping:
        reasons.append(ReasonCode.CRASH_LOOP)
        status = HealthStatus.CRITICAL
    if signals.oom_killed:
        reasons.append(ReasonCode.OOM_KILLED)
        status = worst(status, HealthStatus.DEGRADED)

    restarts = signals.restarts_in_window
    if restarts is None:
        missing.append("stability.restarts")
        return SectionResult(
            worst(status, HealthStatus.UNKNOWN) if not reasons else status, reasons, detail
        )

    if restarts >= policy.stability.critical_restarts:
        reasons.append(ReasonCode.RESTART_SPIKE)
        status = HealthStatus.CRITICAL
    elif restarts >= policy.stability.degraded_restarts:
        reasons.append(ReasonCode.RESTART_SPIKE)
        status = worst(status, HealthStatus.DEGRADED)

    return SectionResult(status, reasons, detail)


def _evaluate_resources(
    signals: ResourceSignals, policy: HealthPolicy, missing: list[str]
) -> SectionResult:
    cpu_ratio = _ratio(signals.cpu_cores_used, signals.cpu_limit_cores)
    memory_ratio = _ratio(signals.memory_bytes_used, signals.memory_limit_bytes)
    detail: dict[str, Any] = {
        "cpu_cores_used": signals.cpu_cores_used,
        "cpu_limit_cores": signals.cpu_limit_cores,
        "cpu_utilization": cpu_ratio,
        "memory_bytes_used": signals.memory_bytes_used,
        "memory_limit_bytes": signals.memory_limit_bytes,
        "memory_utilization": memory_ratio,
        "cpu_throttled_ratio": signals.cpu_throttled_ratio,
    }
    reasons: list[ReasonCode] = []
    status = HealthStatus.HEALTHY
    judged = False

    if signals.cpu_cores_used is None and signals.memory_bytes_used is None:
        missing.append("resources.usage")
        return SectionResult(HealthStatus.UNKNOWN, [], detail)

    for ratio, reason in (
        (cpu_ratio, ReasonCode.CPU_PRESSURE),
        (memory_ratio, ReasonCode.MEMORY_PRESSURE),
    ):
        if ratio is None:
            continue
        judged = True
        if ratio >= policy.resources.critical_utilization:
            reasons.append(reason)
            status = HealthStatus.CRITICAL
        elif ratio >= policy.resources.degraded_utilization:
            reasons.append(reason)
            status = worst(status, HealthStatus.DEGRADED)

    throttle = signals.cpu_throttled_ratio
    if throttle is not None:
        judged = True
        if throttle >= policy.resources.critical_throttle_ratio:
            reasons.append(ReasonCode.CPU_THROTTLING)
            status = HealthStatus.CRITICAL
        elif throttle >= policy.resources.degraded_throttle_ratio:
            reasons.append(ReasonCode.CPU_THROTTLING)
            status = worst(status, HealthStatus.DEGRADED)

    if not judged:
        # Usage is known but no limit is set anywhere: report the numbers,
        # claim nothing about pressure.
        missing.append("resources.limits")
        detail["limits_configured"] = False
        return SectionResult(HealthStatus.HEALTHY, [], detail)

    return SectionResult(status, reasons, detail)


def _evaluate_application(
    signals: ApplicationSignals, policy: HealthPolicy, missing: list[str]
) -> SectionResult:
    detail: dict[str, Any] = {
        "metrics_present": signals.metrics_present,
        "request_rate": signals.request_rate,
        "error_ratio": signals.error_ratio,
        "latency_p95_seconds": signals.latency_p95_seconds,
    }

    if not signals.metrics_present:
        missing.append("application.golden_signals")
        # Not publishing HTTP metrics is a fact about the workload, not a
        # fault in it — unless this policy says the service must.
        if policy.require_application_metrics:
            return SectionResult(
                HealthStatus.DEGRADED, [ReasonCode.APPLICATION_METRICS_MISSING], detail
            )
        return SectionResult(
            HealthStatus.NOT_CONFIGURED, [ReasonCode.APPLICATION_METRICS_MISSING], detail
        )

    reasons: list[ReasonCode] = []
    status = HealthStatus.HEALTHY

    if signals.error_ratio is not None:
        if signals.error_ratio >= policy.application.critical_error_ratio:
            reasons.append(ReasonCode.HIGH_ERROR_RATE)
            status = HealthStatus.CRITICAL
        elif signals.error_ratio >= policy.application.degraded_error_ratio:
            reasons.append(ReasonCode.HIGH_ERROR_RATE)
            status = worst(status, HealthStatus.DEGRADED)

    if signals.latency_p95_seconds is not None:
        if signals.latency_p95_seconds >= policy.application.critical_latency_seconds:
            reasons.append(ReasonCode.HIGH_LATENCY)
            status = HealthStatus.CRITICAL
        elif signals.latency_p95_seconds >= policy.application.degraded_latency_seconds:
            reasons.append(ReasonCode.HIGH_LATENCY)
            status = worst(status, HealthStatus.DEGRADED)

    return SectionResult(status, reasons, detail)


def _terminal(
    status: HealthStatus,
    reason: ReasonCode,
    now: datetime,
    policy_key: str,
    binding_id: str | None,
    previous: HealthStatus | None,
) -> HealthResult:
    """A verdict reached before any signal was worth evaluating."""
    empty = SectionResult(HealthStatus.UNKNOWN)
    return HealthResult(
        status=status,
        computed_at=now,
        newest_sample_at=None,
        freshness_age_seconds=None,
        availability=empty,
        stability=SectionResult(HealthStatus.UNKNOWN),
        resources=SectionResult(HealthStatus.UNKNOWN),
        application=SectionResult(HealthStatus.UNKNOWN),
        reasons=[reason],
        messages=[REASON_TEXT[reason]],
        missing_signals=[],
        partial=False,
        policy_key=policy_key,
        binding_id=binding_id,
        previous_status=previous,
        changed=None if previous is None else previous is not status,
    )


def compute_health(
    *,
    now: datetime,
    policy: HealthPolicy,
    binding: BindingState,
    telemetry: TelemetryState,
    availability: AvailabilitySignals,
    stability: StabilitySignals,
    resources: ResourceSignals,
    application: ApplicationSignals,
    binding_id: str | None = None,
    previous_status: HealthStatus | None = None,
) -> HealthResult:
    """The single place a service's health is decided."""
    # --- states that stop the evaluation before it starts ----------------
    if not binding.exists:
        return _terminal(
            HealthStatus.NOT_CONFIGURED,
            ReasonCode.NO_BINDING,
            now,
            policy.key,
            binding_id,
            previous_status,
        )
    if not binding.enabled:
        return _terminal(
            HealthStatus.NOT_CONFIGURED,
            ReasonCode.BINDING_DISABLED,
            now,
            policy.key,
            binding_id,
            previous_status,
        )
    if not binding.resolved:
        return _terminal(
            HealthStatus.UNKNOWN,
            ReasonCode.BINDING_UNRESOLVED,
            now,
            policy.key,
            binding_id,
            previous_status,
        )
    if not telemetry.datasource_configured:
        return _terminal(
            HealthStatus.NOT_CONFIGURED,
            ReasonCode.DATASOURCE_NOT_CONFIGURED,
            now,
            policy.key,
            binding_id,
            previous_status,
        )
    # A datasource we cannot reach, or a query that failed, says nothing
    # about the workload. Reporting either as unhealthy would blame the
    # service for Drake's own outage.
    if not telemetry.datasource_available:
        return _terminal(
            HealthStatus.UNKNOWN,
            ReasonCode.DATASOURCE_UNAVAILABLE,
            now,
            policy.key,
            binding_id,
            previous_status,
        )
    if telemetry.query_failed:
        return _terminal(
            HealthStatus.UNKNOWN,
            ReasonCode.QUERY_FAILED,
            now,
            policy.key,
            binding_id,
            previous_status,
        )

    # --- freshness -------------------------------------------------------
    age: float | None = None
    stale = False
    if telemetry.newest_sample_at is not None:
        age = (now - telemetry.newest_sample_at).total_seconds()
        # A sample slightly ahead of us is a clock difference, not data from
        # the future; only a large skew is treated as unusable.
        if age < 0:
            age = 0.0 if -age <= policy.future_tolerance_seconds else abs(age)
        stale = age > policy.max_telemetry_age_seconds
    else:
        stale = True

    # --- signals ---------------------------------------------------------
    missing: list[str] = []
    availability_result = _evaluate_availability(availability, policy, missing)
    stability_result = _evaluate_stability(stability, policy, missing)
    resources_result = _evaluate_resources(resources, policy, missing)
    application_result = _evaluate_application(application, policy, missing)

    reasons: list[ReasonCode] = []
    for section in (availability_result, stability_result, resources_result, application_result):
        reasons.extend(section.reasons)

    status = worst(
        availability_result.status,
        stability_result.status,
        resources_result.status,
        application_result.status,
    )

    partial = telemetry.partial or bool(missing)
    if partial and ReasonCode.PARTIAL_RESULT not in reasons:
        reasons.append(ReasonCode.PARTIAL_RESULT)

    # Stale data cannot support a healthy verdict. It can still support a
    # bad one: a workload that was failing when last observed is not
    # improved by the observation being old.
    if stale or telemetry.served_from_last_good:
        reasons.append(ReasonCode.TELEMETRY_STALE)
        if status in (HealthStatus.HEALTHY, HealthStatus.UNKNOWN, HealthStatus.NOT_CONFIGURED):
            status = HealthStatus.STALE

    # Deduplicate while preserving the order signals were evaluated in, so
    # the first cause a reader sees is the most structural one.
    ordered: list[ReasonCode] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)

    return HealthResult(
        status=status,
        computed_at=now,
        newest_sample_at=telemetry.newest_sample_at,
        freshness_age_seconds=age,
        availability=availability_result,
        stability=stability_result,
        resources=resources_result,
        application=application_result,
        reasons=ordered,
        messages=[REASON_TEXT[r] for r in ordered],
        missing_signals=missing,
        partial=partial,
        policy_key=policy.key,
        binding_id=binding_id,
        previous_status=previous_status,
        changed=None if previous_status is None else previous_status is not status,
    )


def window_expression(policy: HealthPolicy) -> str:
    """The `{{window}}` value for this policy's queries."""
    return f"{int(timedelta(seconds=policy.window_seconds).total_seconds())}s"
