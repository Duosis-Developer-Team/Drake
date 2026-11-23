"""Health signals answered from OpenTelemetry metrics (ADR-0038, phase 3).

The broker runs every health signal as a registry template and expects a
Prometheus range-query matrix back. For the templates listed here, and only
on the clusters and templates the settings switch over, the same matrix is
computed from the OpenTelemetry metrics the `drake-otel-metrics` collector
writes to ClickHouse (`otel_metrics_gauge`). Everything after the matrix is
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

SUPPORTED_TEMPLATES = (
    "workload.replicas-desired.v1",
    "workload.replicas-ready.v1",
    "workload.restarts-delta.v1",
)

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
        return (
            template_key in self.templates
            and source_values.get("cluster_ref") in self.clusters
            and source_values.get("workload_kind") in _KIND
        )


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
    kind = source_values["workload_kind"]
    desired, ready, name_attr = _KIND[kind]
    steps = max(0, (end - start) // step)
    parameters = {
        "start": str(start),
        "end": str(end),
        "step": str(step),
        "steps": str(steps),
        "cluster": source_values["cluster_ref"],
        "namespace": source_values["namespace"],
        "workload": source_values["workload_name"],
        "lookback": str(STALENESS_SECONDS),
        "window": str(window_seconds),
        "max_rows": str(MAX_ROWS),
    }
    if template_key == "workload.restarts-delta.v1":
        template = _RESTARTS
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
    forward for up to the staleness window; a step with nothing current has
    no point, exactly as a Prometheus range query leaves it out."""
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
    if template_key == "workload.restarts-delta.v1":
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
