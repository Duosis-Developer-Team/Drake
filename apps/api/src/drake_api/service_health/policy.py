"""Service-health reason codes, statuses and typed thresholds.

Every judgement Drake makes about a service is made here, once, from typed
values — not in a route handler and not in the browser. Two consequences
that are the whole point:

- A threshold can be tested without standing up a cluster.
- The frontend renders a decision it was given; it never re-derives one
  from text, so a wording change cannot alter what a user sees as healthy.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class HealthStatus(StrEnum):
    """What Drake is prepared to say about a service.

    `unknown`, `stale` and `not_configured` are deliberately distinct from
    `healthy`. Collapsing them would let an absence of evidence read as
    evidence of health, which is the failure mode this whole layer exists
    to prevent.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    STALE = "stale"
    NOT_CONFIGURED = "not_configured"


# Ordered worst-first, so combining signals is a max() over severity rather
# than a pile of if/else that has to be read to be understood.
_SEVERITY: Final[dict[HealthStatus, int]] = {
    HealthStatus.NOT_CONFIGURED: 0,
    HealthStatus.HEALTHY: 1,
    HealthStatus.UNKNOWN: 2,
    HealthStatus.STALE: 3,
    HealthStatus.DEGRADED: 4,
    HealthStatus.CRITICAL: 5,
}


def worst(*statuses: HealthStatus) -> HealthStatus:
    """The most severe of the given statuses.

    `not_configured` only survives if it is the only thing present: a
    service with one unconfigured signal and one failing one is failing.
    """
    present = [s for s in statuses if s is not None]
    if not present:
        return HealthStatus.UNKNOWN
    if all(s is HealthStatus.NOT_CONFIGURED for s in present):
        return HealthStatus.NOT_CONFIGURED
    ranked = [s for s in present if s is not HealthStatus.NOT_CONFIGURED]
    return max(ranked, key=lambda s: _SEVERITY[s])


class ReasonCode(StrEnum):
    """Machine-readable causes. The UI maps these to text; it never parses."""

    NO_BINDING = "no_binding"
    BINDING_DISABLED = "binding_disabled"
    BINDING_UNRESOLVED = "binding_unresolved"
    DATASOURCE_NOT_CONFIGURED = "datasource_not_configured"
    DATASOURCE_UNAVAILABLE = "datasource_unavailable"
    TELEMETRY_STALE = "telemetry_stale"
    QUERY_FAILED = "query_failed"
    PARTIAL_RESULT = "partial_result"

    NO_READY_REPLICAS = "no_ready_replicas"
    PARTIAL_AVAILABILITY = "partial_availability"
    ROLLOUT_INCOMPLETE = "rollout_incomplete"

    RESTART_SPIKE = "restart_spike"
    OOM_KILLED = "oom_killed"
    CRASH_LOOP = "crash_loop"

    CPU_PRESSURE = "cpu_pressure"
    MEMORY_PRESSURE = "memory_pressure"
    CPU_THROTTLING = "cpu_throttling"

    HIGH_ERROR_RATE = "high_error_rate"
    HIGH_LATENCY = "high_latency"
    APPLICATION_METRICS_MISSING = "application_metrics_missing"


# Short, fixed sentences. Held here so the API can ship them alongside the
# code and no consumer has to invent wording — but the code is the contract.
REASON_TEXT: Final[dict[ReasonCode, str]] = {
    ReasonCode.NO_BINDING: "This service is not bound to a workload yet.",
    ReasonCode.BINDING_DISABLED: "The workload binding is disabled.",
    ReasonCode.BINDING_UNRESOLVED: "The bound workload has not been seen in cluster inventory.",
    ReasonCode.DATASOURCE_NOT_CONFIGURED: "No telemetry datasource is configured.",
    ReasonCode.DATASOURCE_UNAVAILABLE: "The telemetry datasource could not be reached.",
    ReasonCode.TELEMETRY_STALE: "Telemetry is older than this policy allows.",
    ReasonCode.QUERY_FAILED: "A telemetry query failed, so health could not be computed.",
    ReasonCode.PARTIAL_RESULT: "Some signals were unavailable; this result is partial.",
    ReasonCode.NO_READY_REPLICAS: "No replicas are ready.",
    ReasonCode.PARTIAL_AVAILABILITY: "Fewer replicas are ready than desired.",
    ReasonCode.ROLLOUT_INCOMPLETE: "A rollout has not finished.",
    ReasonCode.RESTART_SPIKE: "Containers are restarting repeatedly.",
    ReasonCode.OOM_KILLED: "A container was terminated for exceeding its memory limit.",
    ReasonCode.CRASH_LOOP: "A container is in a crash loop.",
    ReasonCode.CPU_PRESSURE: "CPU use is close to or above the configured limit.",
    ReasonCode.MEMORY_PRESSURE: "Memory use is close to or above the configured limit.",
    ReasonCode.CPU_THROTTLING: "The workload is being CPU throttled.",
    ReasonCode.HIGH_ERROR_RATE: "The error rate is above this policy's threshold.",
    ReasonCode.HIGH_LATENCY: "Latency is above this policy's threshold.",
    ReasonCode.APPLICATION_METRICS_MISSING: (
        "The application publishes no request metrics, so golden signals are unavailable."
    ),
}


@dataclass(frozen=True)
class AvailabilityThresholds:
    # Below this ratio of ready/desired the service is degraded; zero ready
    # replicas is critical regardless.
    degraded_ready_ratio: float = 1.0
    critical_ready_ratio: float = 0.0


@dataclass(frozen=True)
class StabilityThresholds:
    # Restarts counted over the policy window, per workload.
    degraded_restarts: int = 1
    critical_restarts: int = 5


@dataclass(frozen=True)
class ResourceThresholds:
    # Fractions of the configured limit. A workload with no limit set is
    # not judged on ratios at all — see the engine.
    degraded_utilization: float = 0.85
    critical_utilization: float = 0.95
    degraded_throttle_ratio: float = 0.25
    critical_throttle_ratio: float = 0.50


@dataclass(frozen=True)
class ApplicationThresholds:
    degraded_error_ratio: float = 0.01
    critical_error_ratio: float = 0.05
    degraded_latency_seconds: float = 1.0
    critical_latency_seconds: float = 3.0


@dataclass(frozen=True)
class HealthPolicy:
    key: str
    title: str
    # How old telemetry may be before the answer is `stale` rather than a
    # judgement. Matches the Sprint 3 freshness contract.
    max_telemetry_age_seconds: int
    # Tolerance for a source clock running ahead of ours. A timestamp
    # slightly in the future is a clock difference, not fresher data.
    future_tolerance_seconds: int
    window_seconds: int
    availability: AvailabilityThresholds
    stability: StabilityThresholds
    resources: ResourceThresholds
    application: ApplicationThresholds
    # Whether missing application metrics count against the service. They
    # do not by default: an infrastructure workload that publishes no HTTP
    # metrics is not unhealthy for it.
    require_application_metrics: bool = False


DEFAULT_POLICY_KEY: Final[str] = "default.v1"

_POLICIES: Final[dict[str, HealthPolicy]] = {
    DEFAULT_POLICY_KEY: HealthPolicy(
        key=DEFAULT_POLICY_KEY,
        title="Default service health",
        max_telemetry_age_seconds=300,
        future_tolerance_seconds=60,
        window_seconds=300,
        availability=AvailabilityThresholds(),
        stability=StabilityThresholds(),
        resources=ResourceThresholds(),
        application=ApplicationThresholds(),
    ),
    # Same signals, more patience: for workloads where a slow rollout or an
    # occasional restart is expected and should not page anyone.
    "tolerant.v1": HealthPolicy(
        key="tolerant.v1",
        title="Tolerant (batch and background workloads)",
        max_telemetry_age_seconds=900,
        future_tolerance_seconds=60,
        window_seconds=900,
        availability=AvailabilityThresholds(degraded_ready_ratio=0.75),
        stability=StabilityThresholds(degraded_restarts=3, critical_restarts=10),
        resources=ResourceThresholds(degraded_utilization=0.92, critical_utilization=0.98),
        application=ApplicationThresholds(
            degraded_error_ratio=0.05,
            critical_error_ratio=0.15,
            degraded_latency_seconds=3.0,
            critical_latency_seconds=10.0,
        ),
    ),
}


def get_policy(key: str) -> HealthPolicy:
    policy = _POLICIES.get(key)
    if policy is None:
        raise KeyError(f"unknown health policy: {key}")
    return policy


def policy_keys() -> tuple[str, ...]:
    return tuple(sorted(_POLICIES))
