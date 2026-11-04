"""Error signals across many services at once: "where is the problem?"

The per-service log and trace reads answer questions about one binding. A
list screen needs the same few numbers for every row — how many error and
warning log lines, how many requests, how many of them failed, how slow —
without one round trip per service. These two statements read every
requested binding's numbers in one pass each, grouped by the resource
attributes a binding names a workload with (observability.scope).

Scope works exactly as for the single-service reads: the caller supplies
binding ids, the authorized binding rows supply every label, and the SQL
only narrows by the clusters and namespaces of those rows. A group that
matches no authorized binding (another workload sharing a namespace) is
dropped here and never leaves the API.

Error counts from logs are as good as the severity on the line: a plain
text line has no severity (number 0) and is counted in `lines` only. That
is reported, not guessed at — the UI says "error lines", not "errors".
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from drake_api.observability.scope import (
    RANGES,
    ScopeError,
    clickhouse_time,
    parse_store_time,
)
from drake_api.observability.store import safe_identifier

MAX_BINDINGS = 100
# One row per (cluster, namespace, workload) seen in the window. Bounded so
# a namespace with thousands of short-lived workloads cannot blow the page.
MAX_GROUPS = 2000

# OpenTelemetry severity bands (observability.logs uses the same floors).
_WARN = 13
_ERROR = 17

_GROUP_COLUMNS = """
    ResourceAttributes['k8s.cluster.name'] AS cluster,
    ResourceAttributes['k8s.namespace.name'] AS namespace,
    ResourceAttributes['k8s.deployment.name'] AS deployment,
    ResourceAttributes['k8s.statefulset.name'] AS statefulset,
    ResourceAttributes['k8s.daemonset.name'] AS daemonset,
"""

_NARROW = """
WHERE Timestamp >= {start:DateTime64(9, 'UTC')}
  AND Timestamp < {end:DateTime64(9, 'UTC')}
  AND has({clusters:Array(String)}, ResourceAttributes['k8s.cluster.name'])
  AND has({namespaces:Array(String)}, ResourceAttributes['k8s.namespace.name'])
"""

_LOGS = (
    "SELECT"
    + _GROUP_COLUMNS
    + """
    count() AS lines,
    countIf(SeverityNumber >= {error_floor:UInt8}) AS errors,
    countIf(SeverityNumber >= {warn_floor:UInt8}
            AND SeverityNumber < {error_floor:UInt8}) AS warnings,
    max(Timestamp) AS newest
FROM __DATABASE__.otel_logs
"""
    + _NARROW
    + """
GROUP BY cluster, namespace, deployment, statefulset, daemonset
LIMIT {max_groups:UInt16}
"""
)

# Requests are root server spans, as in observability.traces.
_TRACES = (
    "SELECT"
    + _GROUP_COLUMNS
    + """
    count() AS requests,
    countIf(StatusCode = 'Error') AS errors,
    quantile(0.95)(Duration) AS p95_ns,
    max(Timestamp) AS newest
FROM __DATABASE__.otel_traces
"""
    + _NARROW
    + """
  AND SpanKind = 'Server' AND ParentSpanId = ''
GROUP BY cluster, namespace, deployment, statefulset, daemonset
LIMIT {max_groups:UInt16}
"""
)

# The store column each bound kind is named by (observability.scope keys).
_KIND_COLUMN: dict[str, str] = {
    "Deployment": "deployment",
    "StatefulSet": "statefulset",
    "DaemonSet": "daemonset",
}


class SignalQueryError(ValueError):
    """A request the signal reads refuse to express."""


@dataclass(frozen=True)
class SignalQuery:
    logs: str
    traces: str
    parameters: dict[str, str]
    max_rows: int
    start: datetime
    end: datetime


def _array(values: list[str]) -> str:
    """A ClickHouse Array(String) literal for a query parameter. The value
    travels as a typed parameter, so this is parsed as data, never SQL."""
    quoted = (v.replace("\\", "\\\\").replace("'", "\\'") for v in values)
    return "[" + ",".join(f"'{v}'" for v in quoted) + "]"


def workload_key(binding: dict[str, Any]) -> tuple[str, str, str, str]:
    """(cluster, namespace, kind column, workload) — how a store group is
    matched back to a binding. Raises for a kind the store cannot name."""
    column = _KIND_COLUMN.get(str(binding.get("workload_kind")))
    if column is None:
        raise ScopeError("unsupported workload kind")
    return (
        str(binding["cluster"]["cluster_ref"]),
        str(binding["namespace"]),
        column,
        str(binding["workload_name"]),
    )


def build_signal_query(
    *, database: str, bindings: list[dict[str, Any]], range_key: str, now: datetime
) -> SignalQuery:
    if range_key not in RANGES:
        raise SignalQueryError("unsupported range")
    if not bindings:
        raise SignalQueryError("no bindings")
    if len(bindings) > MAX_BINDINGS:
        raise SignalQueryError("too many bindings")
    db = safe_identifier(database)
    keys = [workload_key(b) for b in bindings]
    end = now
    start = now - timedelta(seconds=RANGES[range_key])
    parameters = {
        "start": clickhouse_time(start),
        "end": clickhouse_time(end),
        "clusters": _array(sorted({k[0] for k in keys})),
        "namespaces": _array(sorted({k[1] for k in keys})),
        "error_floor": str(_ERROR),
        "warn_floor": str(_WARN),
        "max_groups": str(MAX_GROUPS),
    }
    return SignalQuery(
        logs=_LOGS.replace("__DATABASE__", db),
        traces=_TRACES.replace("__DATABASE__", db),
        parameters=parameters,
        max_rows=MAX_GROUPS,
        start=start,
        end=end,
    )


def _count(value: Any) -> int:
    # ClickHouse's JSON format quotes 64-bit integers.
    if isinstance(value, bool):
        raise ValueError("count")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError("count")


def _group_key(row: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """Every (cluster, namespace, kind, workload) a store group can stand
    for. A pod carries at most one of the three workload names in practice;
    each present name is offered so the match never depends on that."""
    cluster = str(row.get("cluster") or "")
    namespace = str(row.get("namespace") or "")
    return [
        (cluster, namespace, column, str(row.get(column) or ""))
        for column in ("deployment", "statefulset", "daemonset")
        if row.get(column)
    ]


def _index(
    rows: list[dict[str, Any]], wanted: set[tuple[str, str, str, str]]
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    found: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("row")
        for key in _group_key(row):
            if key in wanted:
                found[key] = row
    return found


def summarize_logs(
    rows: list[dict[str, Any]], bindings: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """binding id → log counts. A binding with no group in the window gets
    zeros and `newest_at: None`: the store answered and nothing arrived."""
    keys = {b["id"]: workload_key(b) for b in bindings}
    found = _index(rows, set(keys.values()))
    out: dict[str, dict[str, Any]] = {}
    for binding_id, key in keys.items():
        row = found.get(key)
        if row is None:
            out[binding_id] = {"lines": 0, "errors": 0, "warnings": 0, "newest_at": None}
            continue
        out[binding_id] = {
            "lines": _count(row.get("lines")),
            "errors": _count(row.get("errors")),
            "warnings": _count(row.get("warnings")),
            "newest_at": parse_store_time(row.get("newest")).isoformat(),
        }
    return out


def _ms(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number / 1_000_000, 3)


def summarize_traces(
    rows: list[dict[str, Any]], bindings: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """binding id → request counts. No requests means no ratio and no p95,
    never a 0% error rate."""
    keys = {b["id"]: workload_key(b) for b in bindings}
    found = _index(rows, set(keys.values()))
    out: dict[str, dict[str, Any]] = {}
    for binding_id, key in keys.items():
        row = found.get(key)
        requests = _count(row.get("requests")) if row else 0
        if row is None or requests == 0:
            out[binding_id] = {
                "requests": 0,
                "errors": 0,
                "error_ratio": None,
                "p95_ms": None,
                "newest_at": None,
            }
            continue
        errors = _count(row.get("errors"))
        out[binding_id] = {
            "requests": requests,
            "errors": errors,
            "error_ratio": round(errors / requests, 4),
            "p95_ms": _ms(row.get("p95_ns")),
            "newest_at": parse_store_time(row.get("newest")).isoformat(),
        }
    return out
