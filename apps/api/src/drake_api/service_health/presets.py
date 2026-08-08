"""Metric presets: which curated queries answer for a kind of workload.

A preset is a name for a set of registry template keys. It exists so that
"how do I read this service's health" is a choice between reviewed options
rather than a query someone types, and so that a deployment whose
Prometheus uses different metric names is a new preset rather than a new
code path.

Nothing here contains an expression. Each value is a template key; the
expression lives in the curated registry and is validated on load.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class MetricPreset:
    key: str
    title: str
    description: str
    # Template keys, by the signal each one feeds. A signal with no
    # template is simply not read — and the engine reports it as missing
    # rather than assuming a value.
    desired_replicas: str | None = None
    ready_replicas: str | None = None
    restarts: str | None = None
    cpu_usage: str | None = None
    cpu_limit: str | None = None
    memory_usage: str | None = None
    memory_limit: str | None = None
    cpu_throttling: str | None = None
    request_rate: str | None = None
    error_ratio: str | None = None
    latency_p95: str | None = None
    freshness: str | None = None

    def template_keys(self) -> tuple[str, ...]:
        return tuple(
            key
            for key in (
                self.desired_replicas,
                self.ready_replicas,
                self.restarts,
                self.cpu_usage,
                self.cpu_limit,
                self.memory_usage,
                self.memory_limit,
                self.cpu_throttling,
                self.request_rate,
                self.error_ratio,
                self.latency_p95,
                self.freshness,
            )
            if key
        )

    @property
    def has_application_signals(self) -> bool:
        return bool(self.request_rate or self.error_ratio or self.latency_p95)


# Infrastructure only: replicas, restarts and resources. Correct for any
# workload, and the honest default for one that publishes no HTTP metrics.
KUBERNETES_BASELINE: Final[MetricPreset] = MetricPreset(
    key="kubernetes.baseline.v1",
    title="Kubernetes workload (infrastructure signals)",
    description=(
        "Replicas, restarts and resource use from the cluster's own metrics. "
        "No application metrics are read, so golden signals are reported as "
        "unavailable rather than assumed."
    ),
    desired_replicas="workload.replicas-desired.v1",
    ready_replicas="workload.replicas-ready.v1",
    restarts="workload.restarts-delta.v1",
    cpu_usage="workload.cpu-usage.v1",
    cpu_limit="workload.cpu-limit.v1",
    memory_usage="workload.memory-usage.v1",
    memory_limit="workload.memory-limit.v1",
    cpu_throttling="workload.cpu-throttling.v1",
    freshness="workload.telemetry-freshness.v1",
)

# The same infrastructure signals plus RED, for a service that exposes
# Prometheus HTTP metrics under the conventional names.
HTTP_SERVICE: Final[MetricPreset] = MetricPreset(
    key="http.service.v1",
    title="HTTP service (infrastructure + golden signals)",
    description=(
        "Everything in the Kubernetes baseline, plus request rate, error "
        "ratio and p95 latency from standard HTTP server metrics. If the "
        "application does not publish them, they are reported missing — the "
        "workload is not judged unhealthy for it."
    ),
    desired_replicas=KUBERNETES_BASELINE.desired_replicas,
    ready_replicas=KUBERNETES_BASELINE.ready_replicas,
    restarts=KUBERNETES_BASELINE.restarts,
    cpu_usage=KUBERNETES_BASELINE.cpu_usage,
    cpu_limit=KUBERNETES_BASELINE.cpu_limit,
    memory_usage=KUBERNETES_BASELINE.memory_usage,
    memory_limit=KUBERNETES_BASELINE.memory_limit,
    cpu_throttling=KUBERNETES_BASELINE.cpu_throttling,
    request_rate="workload.request-rate.v1",
    error_ratio="workload.error-ratio.v1",
    latency_p95="workload.latency-p95.v1",
    freshness=KUBERNETES_BASELINE.freshness,
)

# The first pilot. It is the HTTP preset under a name an operator can pick
# from a list — deliberately not a code branch, and carrying no namespace
# or workload name: those are chosen per binding, from inventory.
HERMES_PILOT: Final[MetricPreset] = MetricPreset(
    key="hermes.pilot.v1",
    title="Hermes (pilot)",
    description=(
        "The HTTP service preset, selected for the first pilot onboarding. "
        "It hard-codes no namespace and no workload name: both are chosen "
        "from cluster inventory when the binding is created, so the same "
        "preset fits any other application that publishes the same metrics."
    ),
    desired_replicas=HTTP_SERVICE.desired_replicas,
    ready_replicas=HTTP_SERVICE.ready_replicas,
    restarts=HTTP_SERVICE.restarts,
    cpu_usage=HTTP_SERVICE.cpu_usage,
    cpu_limit=HTTP_SERVICE.cpu_limit,
    memory_usage=HTTP_SERVICE.memory_usage,
    memory_limit=HTTP_SERVICE.memory_limit,
    cpu_throttling=HTTP_SERVICE.cpu_throttling,
    request_rate=HTTP_SERVICE.request_rate,
    error_ratio=HTTP_SERVICE.error_ratio,
    latency_p95=HTTP_SERVICE.latency_p95,
    freshness=HTTP_SERVICE.freshness,
)

_PRESETS: Final[dict[str, MetricPreset]] = {
    preset.key: preset for preset in (KUBERNETES_BASELINE, HTTP_SERVICE, HERMES_PILOT)
}

DEFAULT_PRESET_KEY: Final[str] = KUBERNETES_BASELINE.key


def get_preset(key: str) -> MetricPreset:
    preset = _PRESETS.get(key)
    if preset is None:
        raise KeyError(f"unknown metric preset: {key}")
    return preset


def preset_keys() -> frozenset[str]:
    return frozenset(_PRESETS)


def describe_presets() -> list[dict[str, object]]:
    """What the binding form offers. Template keys only — never expressions."""
    return [
        {
            "key": preset.key,
            "title": preset.title,
            "description": preset.description,
            "signals": sorted(preset.template_keys()),
            "includes_application_signals": preset.has_application_signals,
        }
        for preset in sorted(_PRESETS.values(), key=lambda p: p.key)
    ]
