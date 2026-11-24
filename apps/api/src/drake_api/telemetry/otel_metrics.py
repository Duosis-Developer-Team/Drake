"""Health signals answered from OpenTelemetry metrics (ADR-0038, phase 3).

The broker runs every health signal as a registry template and expects a
Prometheus range-query matrix back. For the templates listed here, and only
on the clusters and templates the settings switch over, the same matrix is
computed from the OpenTelemetry metrics the `drake-otel-metrics` collector
writes to ClickHouse (`otel_metrics_gauge`, and `otel_metrics_sum` for the
CPU-seconds counter). Everything after the matrix is
unchanged: normalization, envelopes, caching, the health engine.

Each answer reproduces its Prometheus template's semantics:

- `workload.replicas-desired.v1` / `workload.replicas-ready.v1`: the
  workload's latest replica count at each step, looking back at most five
  minutes (Prometheus's staleness), the maximum if several series match.
  A Deployment's "ready" is `k8s.deployment.available`: kube-state-metrics'
  `replicas_ready` and `available_replicas` differ only for Deployments with
  `minReadySeconds`, and phase 2 compared them equal.
- `workload.restarts-delta.v1`: the increase of every container's restart
  count over the window ending at each step, summed. Restart counts never
  decrease, so the increase within a window is its maximum minus its
  minimum.
- `workload.memory-usage.v1` / `workload.memory-limit.v1` /
  `workload.cpu-limit.v1`: the sum over the workload's containers of each
  container's latest value at each step, looking back five minutes. A
  container is its pod and name, so a restarted container (new id) is not
  counted twice while its old samples are still within the lookback.
- `workload.cpu-usage.v1`: per container, the CPU-seconds counter's
  increase over the window ending at each step divided by the time between
  its first and last sample there (Prometheus's `rate`, counter resets
  included); summed over the containers. At least two samples are needed.

The cluster, namespace and node screens and the remaining workload signals
(`_SPECS`) are built by one statement builder. Each mirrors its PromQL:
the latest value per series within the lookback, a counter's rate or
increase over the window, then the template's own aggregation by its output
labels. Those statements return one row per series, its points as an array,
so a 24-hour screen stays within the reader's row ceiling.

Every statement names its cluster as `{cluster:String}`, so the store routes
it to the cluster's own ClickHouse when the cluster keeps its telemetry
(ADR-0037 §3). Values travel as typed parameters, never as SQL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from drake_api.observability.store import (
    ClickHouseStore,
    StoreContractError,
    StoreUnavailableError,
    safe_identifier,
)
from drake_api.telemetry.provider import (
    ProviderContractError,
    ProviderUnavailableError,
    RangeQueryResult,
)

# Prometheus's lookback: a sample older than this is no longer "current".
STALENESS_SECONDS = 300
MAX_ROWS = 1000
SOURCE = "otel-metrics"

# Workload kind -> (desired metric, ready metric, resource attribute naming it)
_KIND: dict[str, tuple[str, str, str]] = {
    "Deployment": ("k8s.deployment.desired", "k8s.deployment.available", "k8s.deployment.name"),
    "StatefulSet": (
        "k8s.statefulset.desired_pods",
        "k8s.statefulset.ready_pods",
        "k8s.statefulset.name",
    ),
    "DaemonSet": (
        "k8s.daemonset.desired_scheduled_nodes",
        "k8s.daemonset.ready_nodes",
        "k8s.daemonset.name",
    ),
}

# Answered by the statement builder (`_SPECS`, below).
_SPEC_KEYS = (
    "cluster.namespace-cpu-limit.v1",
    "cluster.namespace-cpu-requested.v1",
    "cluster.namespace-memory-limit.v1",
    "cluster.namespace-memory-requested.v1",
    "cluster.namespace-memory-usage.v1",
    "cluster.namespace-cpu-usage.v1",
    "cluster.namespace-restarts-24h.v1",
    "cluster.namespace-running-pods.v1",
    "cluster.node-cpu-allocatable.v1",
    "cluster.node-memory-allocatable.v1",
    "cluster.node-pods-allocatable.v1",
    "cluster.node-cpu-cores.v1",
    "cluster.node-cpu-utilization.v1",
    "cluster.node-load5.v1",
    "cluster.node-memory-utilization.v1",
    "cluster.node-root-disk-size.v1",
    "cluster.node-root-disk-used.v1",
    "namespace.pod-cpu-limit.v1",
    "namespace.pod-cpu-requested.v1",
    "namespace.pod-memory-limit.v1",
    "namespace.pod-memory-usage.v1",
    "namespace.pod-cpu-usage.v1",
    "namespace.pod-restarts-24h.v1",
    "workload.cpu-throttling.v1",
    "workload.telemetry-freshness.v1",
)

# Template -> (table, metric) for the per-container sums.
_CONTAINER_SUM: dict[str, str] = {
    "workload.memory-usage.v1": "container.memory.working_set",
    "workload.memory-limit.v1": "k8s.container.memory_limit",
    "workload.cpu-limit.v1": "k8s.container.cpu_limit",
}

SUPPORTED_TEMPLATES = (
    "workload.replicas-desired.v1",
    "workload.replicas-ready.v1",
    "workload.restarts-delta.v1",
    "workload.memory-usage.v1",
    "workload.memory-limit.v1",
    "workload.cpu-limit.v1",
    "workload.cpu-usage.v1",
    *_SPEC_KEYS,
)

# Templates whose SQL already answers every step exactly (no carry-forward).
_EXACT_STEPS = frozenset(SUPPORTED_TEMPLATES) - {
    "workload.replicas-desired.v1",
    "workload.replicas-ready.v1",
}

# The latest value per series per step slot, then the maximum across series.
_LATEST = """
SELECT k, max(v) AS v FROM (
    SELECT
        -- A sample inside the lookback before `start` is current at slot 0.
        toUInt32(greatest(0, ceil(
            (toInt64(toUnixTimestamp(TimeUnix)) - {start:UInt32}) / {step:UInt32}
        ))) AS k,
        ResourceAttributes['__NAME_ATTR__'] AS series,
        argMax(Value, TimeUnix) AS v
    FROM __DATABASE__.otel_metrics_gauge
    WHERE MetricName = {metric:String}
      AND ResourceAttributes['tracesis.source'] = 'otel-metrics'
      AND ResourceAttributes['k8s.cluster.name'] = {cluster:String}
      AND ResourceAttributes['k8s.namespace.name'] = {namespace:String}
      AND ResourceAttributes['__NAME_ATTR__'] = {workload:String}
      AND TimeUnix > toDateTime({start:UInt32} - {lookback:UInt32})
      AND TimeUnix <= toDateTime({end:UInt32})
    GROUP BY k, series
)
GROUP BY k
ORDER BY k
LIMIT {max_rows:UInt16}
"""

# Each sample counts towards every step whose window (t - window, t] holds
# it; per container and step the increase is max - min; summed per step.
_RESTARTS = """
SELECT k, sum(inc) AS v FROM (
    SELECT k, series, max(Value) - min(Value) AS inc FROM (
        SELECT
            Value,
            concat(
                ResourceAttributes['k8s.pod.uid'], '/', ResourceAttributes['k8s.container.name']
            ) AS series,
            toInt64(toUnixTimestamp(TimeUnix)) AS s,
            -- Steps t with t - window < s <= t: from the first step at or
            -- after s, up to (not including) the first step at s + window.
            arrayJoin(range(
                toUInt32(greatest(0, ceil((s - {start:UInt32}) / {step:UInt32}))),
                toUInt32(greatest(0, ceil((s + {window:UInt32} - {start:UInt32}) / {step:UInt32})))
            )) AS k
        FROM __DATABASE__.otel_metrics_gauge
        WHERE MetricName = 'k8s.container.restarts'
          AND ResourceAttributes['tracesis.source'] = 'otel-metrics'
          AND ResourceAttributes['k8s.cluster.name'] = {cluster:String}
          AND ResourceAttributes['k8s.namespace.name'] = {namespace:String}
          AND ResourceAttributes['__NAME_ATTR__'] = {workload:String}
          AND TimeUnix > toDateTime({start:UInt32} - {window:UInt32})
          AND TimeUnix <= toDateTime({end:UInt32})
    )
    WHERE k <= {steps:UInt32}
    GROUP BY k, series
)
GROUP BY k
ORDER BY k
LIMIT {max_rows:UInt16}
"""

# Each sample is current at every step t with s <= t < s + lookback; per
# container and step the latest sample counts; summed per step.
_CONTAINER_LATEST = """
SELECT k, sum(v) AS v FROM (
    SELECT k, series, argMax(Value, s) AS v FROM (
        SELECT
            Value,
            concat(
                ResourceAttributes['k8s.pod.uid'], '/', ResourceAttributes['k8s.container.name']
            ) AS series,
            toInt64(toUnixTimestamp(TimeUnix)) AS s,
            arrayJoin(range(
                toUInt32(greatest(0, ceil((s - {start:UInt32}) / {step:UInt32}))),
                toUInt32(greatest(0, ceil(
                    (s + {lookback:UInt32} - {start:UInt32}) / {step:UInt32}
                )))
            )) AS k
        FROM __DATABASE__.otel_metrics_gauge
        WHERE MetricName = {metric:String}
          AND ResourceAttributes['tracesis.source'] = 'otel-metrics'
          AND ResourceAttributes['k8s.cluster.name'] = {cluster:String}
          AND ResourceAttributes['k8s.namespace.name'] = {namespace:String}
          AND ResourceAttributes['__NAME_ATTR__'] = {workload:String}
          AND TimeUnix > toDateTime({start:UInt32} - {lookback:UInt32})
          AND TimeUnix <= toDateTime({end:UInt32})
    )
    WHERE k <= {steps:UInt32}
    GROUP BY k, series
)
GROUP BY k
ORDER BY k
LIMIT {max_rows:UInt16}
"""

# Per container and step, the counter's increase over the window ending at
# the step (a decrease is a reset: the new value is the increase since it),
# over the seconds between the first and last sample. Summed per step.
_CPU_RATE = """
SELECT k, sum(r) AS v FROM (
    SELECT k, series,
        arraySort(groupArray((s, Value))) AS points,
        arrayMap(p -> p.2, points) AS vs,
        arraySum(arrayMap((d, x) -> if(d < 0, x, d), arrayDifference(vs), vs)) AS increase,
        tupleElement(points[-1], 1) - tupleElement(points[1], 1) AS seconds,
        increase / seconds AS r
    FROM (
        SELECT
            Value,
            concat(
                ResourceAttributes['k8s.pod.uid'], '/', ResourceAttributes['k8s.container.name']
            ) AS series,
            toInt64(toUnixTimestamp(TimeUnix)) AS s,
            arrayJoin(range(
                toUInt32(greatest(0, ceil((s - {start:UInt32}) / {step:UInt32}))),
                toUInt32(greatest(0, ceil((s + {window:UInt32} - {start:UInt32}) / {step:UInt32})))
            )) AS k
        FROM __DATABASE__.otel_metrics_sum
        WHERE MetricName = 'container.cpu.time'
          AND ResourceAttributes['tracesis.source'] = 'otel-metrics'
          AND ResourceAttributes['k8s.cluster.name'] = {cluster:String}
          AND ResourceAttributes['k8s.namespace.name'] = {namespace:String}
          AND ResourceAttributes['__NAME_ATTR__'] = {workload:String}
          AND TimeUnix > toDateTime({start:UInt32} - {window:UInt32})
          AND TimeUnix <= toDateTime({end:UInt32})
    )
    WHERE k <= {steps:UInt32}
    GROUP BY k, series
    HAVING count() >= 2 AND seconds > 0
)
GROUP BY k
ORDER BY k
LIMIT {max_rows:UInt16}
"""


# --- the statement builder (cluster, namespace and node screens) ---------

_POD_CONTAINER = (
    "concat(ResourceAttributes['k8s.pod.uid'], '/', ResourceAttributes['k8s.container.name'])"
)
_NODE = "ResourceAttributes['k8s.node.name']"
_NAMESPACE = "ResourceAttributes['k8s.namespace.name']"
_POD = "ResourceAttributes['k8s.pod.name']"
_DAY = 86400
# How long a sample stays current for the screens: two collection intervals
# and a half. Prometheus ends a series as soon as its pod or container goes
# (a staleness marker); OpenTelemetry sends no such marker, so a five-minute
# lookback would count a finished CronJob pod for five more minutes.
FRESH_SECONDS = 150

_THROTTLED = "container_cpu_cfs_throttled_periods_total"
_PERIODS = "container_cpu_cfs_periods_total"

_SCOPE_FILTER = {
    "cluster": "",
    "namespace": " AND ResourceAttributes['k8s.namespace.name'] = {namespace:String}",
    "workload": (
        " AND ResourceAttributes['k8s.namespace.name'] = {namespace:String}"
        " AND ResourceAttributes['__NAME_ATTR__'] = {workload:String}"
    ),
}


def _slots(window: str) -> str:
    """Steps t with t - window < s <= t, never past the last step."""
    return (
        "arrayJoin(range("
        "toUInt32(greatest(0, ceil((s - {start:UInt32}) / {step:UInt32}))), "
        "least(toUInt32({steps:UInt32} + 1), toUInt32(greatest(0, ceil("
        "(s + {" + window + ":UInt32} - {start:UInt32}) / {step:UInt32}))))"
        ")) AS k"
    )


@dataclass(frozen=True)
class _Spec:
    scope: str  # "workload" | "namespace" | "cluster"
    table: str  # "gauge" | "sum"
    metrics: tuple[str, ...]
    series: str  # what one series is (one container, one node, ...)
    labels: tuple[tuple[str, str], ...] = ()  # (output label, expression)
    mode: str = "latest"  # "latest" | "rate" | "increase"
    window: str = "fresh"  # "fresh" | "window" | "day"
    value: str = "Value"
    where: str = ""
    running_only: bool = False  # only pods whose phase is Running
    present_only: bool = False  # only pods that still exist (kube_pod_info)
    pod_reduce: str = ""  # an aggregation per pod before the labels'
    aggregate: str = "sum(v)"
    drop_zero: bool = False


# Pods current at a step (any phase, as kube_pod_info) or, with
# __PHASE__ = " WHERE v = 2", Running ones (k8s_cluster: Running = 2).
_PODS = (
    "SELECT k, series FROM ("
    " SELECT k, series, argMax(val, s) AS v FROM ("
    " SELECT Value AS val, ResourceAttributes['k8s.pod.uid'] AS series,"
    " toInt64(toUnixTimestamp(TimeUnix)) AS s, __SLOTS__"
    " FROM __DATABASE__.otel_metrics_gauge"
    " WHERE MetricName = 'k8s.pod.phase'"
    " AND ResourceAttributes['tracesis.source'] = 'otel-metrics'"
    " AND ResourceAttributes['k8s.cluster.name'] = {cluster:String}__SCOPE__"
    " AND TimeUnix > toDateTime({start:UInt32} - {fresh:UInt32})"
    " AND TimeUnix <= toDateTime({end:UInt32})"
    ") WHERE k <= {steps:UInt32} GROUP BY k, series"
    ")__PHASE__"
)


def _spec_statement(spec: _Spec) -> str:
    """Every fragment here is a constant of this module (`_SPECS`); every
    value (cluster, namespace, workload, metric names, times) is a typed
    parameter. Nothing a caller sends becomes SQL text."""
    names = [name for name, _ in spec.labels]
    carried = "".join(f", {expr} AS g_{name}" for name, expr in spec.labels)
    any_labels = "".join(f", any(g_{name}) AS {name}" for name in names)
    any_again = "".join(f", any({name}) AS {name}" for name in names)
    keep = "".join(f", {name}" for name in names)
    metric_list = ", ".join(f"{{m{i}:String}}" for i in range(len(spec.metrics)))
    if spec.mode == "latest":
        per_series = "argMax(val, s) AS v"
        having = ""
    elif spec.mode == "increase":
        per_series = "max(val) - min(val) AS v"
        having = ""
    else:  # rate: counter resets included, at least two samples
        per_series = (
            "arraySort(groupArray((s, val))) AS points, "
            "arrayMap(p -> p.2, points) AS vs, "
            "arraySum(arrayMap((d, x) -> if(d < 0, x, d), arrayDifference(vs), vs)) AS increase, "
            "tupleElement(points[-1], 1) - tupleElement(points[1], 1) AS seconds, "
            "increase / seconds AS v"
        )
        having = " HAVING count() >= 2 AND seconds > 0"
    statement = (
        "SELECT k, series, any(g_mname) AS mname, any(g_state) AS state, "  # noqa: S608 - fixed fragments
        f"any(g_puid) AS puid{any_labels}, {per_series} FROM ("
        f" SELECT toFloat64({spec.value}) AS val,"
        f" concat(MetricName, '|', {spec.series}) AS series,"
        " MetricName AS g_mname, Attributes['state'] AS g_state,"
        f" ResourceAttributes['k8s.pod.uid'] AS g_puid{carried},"
        " toInt64(toUnixTimestamp(TimeUnix)) AS s, "
        + _slots(spec.window)
        + f" FROM __DATABASE__.otel_metrics_{spec.table}"
        f" WHERE MetricName IN ({metric_list})"
        " AND ResourceAttributes['tracesis.source'] = 'otel-metrics'"
        " AND ResourceAttributes['k8s.cluster.name'] = {cluster:String}"
        f"{_SCOPE_FILTER[spec.scope]}{spec.where}"
        " AND TimeUnix > toDateTime({start:UInt32} - {" + spec.window + ":UInt32})"
        " AND TimeUnix <= toDateTime({end:UInt32})"
        ") WHERE k <= {steps:UInt32} GROUP BY k, series" + having
    )
    if spec.running_only or spec.present_only:
        pods = (
            _PODS.replace("__SLOTS__", _slots("fresh"))
            .replace("__SCOPE__", _SCOPE_FILTER[spec.scope])
            .replace("__PHASE__", " WHERE v = 2" if spec.running_only else "")
        )
        statement = f"SELECT * FROM ({statement}) WHERE (k, puid) IN ({pods})"  # noqa: S608
    if spec.pod_reduce:
        statement = (
            f"SELECT k, puid, any(mname) AS mname, any(state) AS state{any_again},"  # noqa: S608
            f" {spec.pod_reduce}(v) AS v FROM ({statement}) GROUP BY k, puid"
        )
    grouped = f"SELECT k{keep}, {spec.aggregate} AS v FROM ({statement}) GROUP BY k{keep}"  # noqa: S608
    final_filter = " WHERE isFinite(v)" + (" AND v != 0" if spec.drop_zero else "")
    group_by = f" GROUP BY {', '.join(names)}" if names else ""
    selected = ", ".join(names) + ", " if names else ""
    return (
        f"SELECT {selected}groupArray((k, v)) AS pts FROM ({grouped}){final_filter}{group_by}"  # noqa: S608
        " LIMIT {max_rows:UInt16}"
    )


def _resource(
    scope: str, metric: str, labels: tuple[tuple[str, str], ...], **options: Any
) -> _Spec:
    """A per-container gauge (or counter), each container once."""
    return _Spec(
        scope=scope,
        table="gauge",
        metrics=(metric,),
        series=_POD_CONTAINER,
        labels=labels,
        **options,
    )


_NS_NODE = (("namespace", _NAMESPACE), ("node", _NODE))
_BY_POD = (("pod", _POD),)
_BY_NODE = (("node", _NODE),)
_NODE_CPU = f"concat({_NODE}, '/', Attributes['cpu'])"
_NODE_FS = f"concat({_NODE}, '/', Attributes['device'], '/', Attributes['state'])"

_SPECS: dict[str, _Spec] = {
    # cluster screen: namespaces, by node
    "cluster.namespace-cpu-limit.v1": _resource(
        "cluster", "k8s.container.cpu_limit", _NS_NODE, running_only=True
    ),
    "cluster.namespace-cpu-requested.v1": _resource(
        "cluster", "k8s.container.cpu_request", _NS_NODE, running_only=True
    ),
    "cluster.namespace-memory-limit.v1": _resource(
        "cluster", "k8s.container.memory_limit", _NS_NODE, running_only=True
    ),
    "cluster.namespace-memory-requested.v1": _resource(
        "cluster", "k8s.container.memory_request", _NS_NODE, running_only=True
    ),
    "cluster.namespace-memory-usage.v1": _resource(
        "cluster", "container.memory.working_set", _NS_NODE
    ),
    "cluster.namespace-cpu-usage.v1": _Spec(
        scope="cluster",
        table="sum",
        metrics=("container.cpu.time",),
        series=_POD_CONTAINER,
        labels=_NS_NODE,
        mode="rate",
        window="window",
    ),
    # Per pod the most restarted container, as the PromQL's max by pod.
    "cluster.namespace-restarts-24h.v1": _resource(
        "cluster",
        "k8s.container.restarts",
        _NS_NODE,
        mode="increase",
        window="day",
        pod_reduce="max",
        present_only=True,
    ),
    "cluster.namespace-running-pods.v1": _Spec(
        scope="cluster",
        table="gauge",
        metrics=("k8s.pod.phase",),
        series="ResourceAttributes['k8s.pod.uid']",
        labels=_NS_NODE,
        value="if(Value = 2, 1, 0)",
        drop_zero=True,
    ),
    # cluster screen: nodes
    "cluster.node-cpu-allocatable.v1": _Spec(
        scope="cluster",
        table="gauge",
        metrics=("k8s.node.allocatable_cpu",),
        series=_NODE,
        labels=_BY_NODE,
    ),
    "cluster.node-memory-allocatable.v1": _Spec(
        scope="cluster",
        table="gauge",
        metrics=("k8s.node.allocatable_memory",),
        series=_NODE,
        labels=_BY_NODE,
    ),
    "cluster.node-pods-allocatable.v1": _Spec(
        scope="cluster",
        table="gauge",
        metrics=("k8s.node.allocatable_pods",),
        series=_NODE,
        labels=_BY_NODE,
    ),
    "cluster.node-cpu-cores.v1": _Spec(
        scope="cluster",
        table="sum",
        metrics=("system.cpu.time",),
        series=_NODE_CPU,
        labels=_BY_NODE,
        value="1",
        where=" AND Attributes['state'] = 'idle'",
    ),
    "cluster.node-cpu-utilization.v1": _Spec(
        scope="cluster",
        table="sum",
        metrics=("system.cpu.time",),
        series=_NODE_CPU,
        labels=_BY_NODE,
        mode="rate",
        window="window",
        where=" AND Attributes['state'] = 'idle'",
        aggregate="greatest(0, 1 - avg(v))",
    ),
    "cluster.node-load5.v1": _Spec(
        scope="cluster",
        table="gauge",
        metrics=("system.cpu.load_average.5m",),
        series=_NODE,
        labels=_BY_NODE,
        aggregate="max(v)",
    ),
    "cluster.node-memory-utilization.v1": _Spec(
        scope="cluster",
        table="sum",
        metrics=("system.linux.memory.available", "system.memory.limit"),
        series=_NODE,
        labels=_BY_NODE,
        aggregate=(
            "1 - sumIf(v, mname = 'system.linux.memory.available')"
            " / nullIf(sumIf(v, mname = 'system.memory.limit'), 0)"
        ),
    ),
    "cluster.node-root-disk-size.v1": _Spec(
        scope="cluster",
        table="sum",
        metrics=("system.filesystem.usage",),
        series=_NODE_FS,
        labels=_BY_NODE,
        where=" AND Attributes['mountpoint'] = '/'",
    ),
    "cluster.node-root-disk-used.v1": _Spec(
        scope="cluster",
        table="sum",
        metrics=("system.filesystem.usage",),
        series=_NODE_FS,
        labels=_BY_NODE,
        where=" AND Attributes['mountpoint'] = '/'",
        aggregate="1 - sumIf(v, state = 'free') / nullIf(sum(v), 0)",
    ),
    # namespace screen: pods
    "namespace.pod-cpu-limit.v1": _resource(
        "namespace", "k8s.container.cpu_limit", _BY_POD, running_only=True
    ),
    "namespace.pod-cpu-requested.v1": _resource(
        "namespace", "k8s.container.cpu_request", _BY_POD, running_only=True
    ),
    "namespace.pod-memory-limit.v1": _resource(
        "namespace", "k8s.container.memory_limit", _BY_POD, running_only=True
    ),
    "namespace.pod-memory-usage.v1": _resource(
        "namespace", "container.memory.working_set", _BY_POD
    ),
    "namespace.pod-cpu-usage.v1": _Spec(
        scope="namespace",
        table="sum",
        metrics=("container.cpu.time",),
        series=_POD_CONTAINER,
        labels=(("pod", _POD), ("node", _NODE)),
        mode="rate",
        window="window",
    ),
    "namespace.pod-restarts-24h.v1": _resource(
        "namespace", "k8s.container.restarts", _BY_POD, mode="increase", window="day"
    ),
    # workload signals
    "workload.cpu-throttling.v1": _Spec(
        scope="workload",
        table="sum",
        metrics=(_THROTTLED, _PERIODS),
        series=_POD_CONTAINER,
        mode="rate",
        window="window",
        aggregate=(
            f"sumIf(v, mname = '{_THROTTLED}') / greatest(sumIf(v, mname = '{_PERIODS}'), 1)"
        ),
    ),
    # Any pod of the workload reported within the lookback (drake:workload:up).
    "workload.telemetry-freshness.v1": _Spec(
        scope="workload",
        table="gauge",
        metrics=("k8s.pod.phase",),
        series="ResourceAttributes['k8s.pod.uid']",
        value="1",
        aggregate="max(v)",
    ),
}


@dataclass(frozen=True)
class OtelMetricsSwitch:
    """Which clusters and templates are answered from OpenTelemetry. Both
    empty by default: nothing changes until both name something."""

    clusters: frozenset[str]
    templates: frozenset[str]

    @classmethod
    def from_settings(cls, clusters: list[str], templates: list[str]) -> OtelMetricsSwitch:
        return cls(frozenset(clusters), frozenset(t for t in templates if t in SUPPORTED_TEMPLATES))

    def serves(self, template_key: str, source_values: dict[str, str]) -> bool:
        if template_key not in self.templates:
            return False
        if source_values.get("cluster_ref") not in self.clusters:
            return False
        spec = _SPECS.get(template_key)
        if spec is not None and spec.scope != "workload":
            return True
        # A workload is named by its kind's attribute; a kind the metrics
        # cannot name stays with Prometheus.
        return source_values.get("workload_kind") in _KIND


def build(
    template_key: str,
    source_values: dict[str, str],
    *,
    database: str,
    start: int,
    end: int,
    step: int,
    window_seconds: int,
) -> tuple[str, dict[str, str], int]:
    """(statement, parameters, number of steps) for one template."""
    steps = max(0, (end - start) // step)
    parameters = {
        "start": str(start),
        "end": str(end),
        "step": str(step),
        "steps": str(steps),
        "cluster": source_values["cluster_ref"],
        "lookback": str(STALENESS_SECONDS),
        "fresh": str(FRESH_SECONDS),
        "window": str(window_seconds),
        "max_rows": str(MAX_ROWS),
    }
    if "namespace" in source_values:
        parameters["namespace"] = source_values["namespace"]
    spec = _SPECS.get(template_key)
    if spec is not None:
        name_attr = ""
        if spec.scope == "workload":
            name_attr = _KIND[source_values["workload_kind"]][2]
            parameters["workload"] = source_values["workload_name"]
        if spec.window == "day":
            parameters["day"] = str(_DAY)
        for i, metric in enumerate(spec.metrics):
            parameters[f"m{i}"] = metric
        statement = (
            _spec_statement(spec)
            .replace("__NAME_ATTR__", name_attr)
            .replace("__DATABASE__", safe_identifier(database))
        )
        return statement, parameters, steps
    desired, ready, name_attr = _KIND[source_values["workload_kind"]]
    parameters["workload"] = source_values["workload_name"]
    if template_key == "workload.restarts-delta.v1":
        template = _RESTARTS
    elif template_key == "workload.cpu-usage.v1":
        template = _CPU_RATE
    elif template_key in _CONTAINER_SUM:
        template = _CONTAINER_LATEST
        parameters["metric"] = _CONTAINER_SUM[template_key]
    elif template_key in ("workload.replicas-desired.v1", "workload.replicas-ready.v1"):
        template = _LATEST
        parameters["metric"] = desired if template_key.endswith("desired.v1") else ready
    else:
        raise ValueError("template not answered from OpenTelemetry")
    statement = template.replace("__NAME_ATTR__", name_attr).replace(
        "__DATABASE__", safe_identifier(database)
    )
    return statement, parameters, steps


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ProviderContractError("provider_malformed_response")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ProviderContractError("provider_malformed_response") from error
    return number


def to_matrix(
    template_key: str,
    rows: list[dict[str, Any]],
    *,
    start: int,
    step: int,
    steps: int,
) -> RangeQueryResult:
    """Step slots -> one Prometheus matrix entry. A replica count carries
    forward for up to the staleness window (the other statements answer each
    step themselves); a step with nothing current has no point, exactly as a
    Prometheus range query leaves it out."""
    spec = _SPECS.get(template_key)
    if spec is not None:
        return _series_matrix(spec, rows, start=start, step=step, steps=steps)
    slots: dict[int, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderContractError("provider_malformed_response")
        slot = int(_number(row.get("k")))
        value = _number(row.get("v"))
        if math.isnan(value) or math.isinf(value):
            continue
        slots[slot] = value
    values: list[list[Any]] = []
    if template_key in _EXACT_STEPS:
        for k in range(steps + 1):
            if k in slots:
                values.append([start + k * step, str(slots[k])])
    else:
        carry: tuple[int, float] | None = None
        for k in range(steps + 1):
            if k in slots:
                carry = (k, slots[k])
            if carry is not None and (k - carry[0]) * step <= STALENESS_SECONDS:
                values.append([start + k * step, str(carry[1])])
    if not values:
        return RangeQueryResult(result=[])
    return RangeQueryResult(result=[{"metric": {}, "values": values}])


def _series_matrix(
    spec: _Spec, rows: list[dict[str, Any]], *, start: int, step: int, steps: int
) -> RangeQueryResult:
    """One row per series: its labels and its (step, value) points."""
    names = [name for name, _ in spec.labels]
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("pts"), list):
            raise ProviderContractError("provider_malformed_response")
        points: dict[int, float] = {}
        for pair in row["pts"]:
            if not isinstance(pair, list | tuple) or len(pair) != 2:
                raise ProviderContractError("provider_malformed_response")
            slot = int(_number(pair[0]))
            value = _number(pair[1])
            if 0 <= slot <= steps and not (math.isnan(value) or math.isinf(value)):
                points[slot] = value
        if points:
            result.append(
                {
                    "metric": {name: str(row.get(name, "")) for name in names},
                    "values": [[start + k * step, str(points[k])] for k in sorted(points)],
                }
            )
    return RangeQueryResult(result=result)


class OtelMetricsReader:
    """Answers the switched templates from the store."""

    def __init__(self, store: ClickHouseStore, switch: OtelMetricsSwitch) -> None:
        self._store = store
        self.switch = switch

    def serves(self, template_key: str, source_values: dict[str, str]) -> bool:
        return self._store.connector is not None and self.switch.serves(template_key, source_values)

    async def query_range(
        self,
        template_key: str,
        source_values: dict[str, str],
        *,
        start: int,
        end: int,
        step_seconds: int,
        window_seconds: int,
        timeout_seconds: float,
        correlation_id: str,
    ) -> RangeQueryResult:
        connector = self._store.connector
        assert connector is not None
        statement, parameters, steps = build(
            template_key,
            source_values,
            database=connector.database,
            start=start,
            end=end,
            step=step_seconds,
            window_seconds=window_seconds,
        )
        # The store's failures in the broker's own terms, so a failed read
        # serves last-good exactly as a failed Prometheus read does.
        try:
            result = await self._store.query(
                statement,
                parameters,
                max_rows=MAX_ROWS,
                timeout_seconds=min(timeout_seconds, connector.timeout_seconds),
                correlation_id=correlation_id,
            )
        except StoreUnavailableError as error:
            raise ProviderUnavailableError(error.code) from error
        except StoreContractError as error:
            raise ProviderContractError(error.code) from error
        return to_matrix(template_key, result.rows, start=start, step=step_seconds, steps=steps)
