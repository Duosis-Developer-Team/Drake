"""An environment at a glance: the custom dashboard's reads (ADR-0033).

A dashboard looks at every service of one environment at once: traffic,
errors and latency per hour, how long requests took, which status codes
they got, which routes carried them, who called whom, what they asked of
their databases, and what the logs were made of. The per-service reads
(observability.timeline, .traces, .patterns) each answer for one binding;
these statements answer for many in one pass each.

Scope works as for the cross-service signal read (observability.signals):
the caller supplies binding ids, the authorized binding rows supply every
label, and nothing in the SQL comes from the request. Unlike that read,
some of these statements aggregate across workloads (the latency
histogram, the log patterns), so a group cannot be dropped afterwards.
Every statement therefore narrows *in SQL* to the exact (cluster,
namespace, kind, workload) of the authorized bindings, and a call or a
database span counts only when every workload it names is one of them.
Another team's workload sharing a namespace never contributes a number.

Requests are server spans without health checks and scrapes, and
long-lived connections stay out of latency percentiles, exactly as in the
per-service reads (observability.health_checks).
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from drake_api.observability.health_checks import (
    LONG_LIVED_SECONDS,
    health_check_parameter,
    with_health_check_filter,
)
from drake_api.observability.patterns import _REPLACEMENTS, _shape
from drake_api.observability.scope import RANGES, ScopeError, clickhouse_time
from drake_api.observability.signals import workload_key
from drake_api.observability.store import safe_identifier
from drake_api.observability.timeline import BUCKET_SECONDS

# Fewer than the signal read allows: the traffic statement returns one row
# per binding per bucket, every range has at most 30 buckets
# (observability.timeline), and 32 x 30 stays under the reader profile's
# 1000-row ceiling (see observability.signals.MAX_GROUPS). A unit test pins it.
MAX_BINDINGS = 32
MAX_ROWS = 1000
MAX_ROUTES = 200
MAX_CALLS = 200
MAX_DB_OPERATIONS = 200
MAX_LOG_PATTERNS = 20
PATTERN_CHARS = 160

# Latency classes: a third of a decade each, from 1 ms. Class 0 holds
# everything under ~2.15 ms, class 12 everything from 10 s up. Class 9
# starts at 1 s, where a request starts to feel slow.
LATENCY_CLASSES = 13
SLOW_CLASS = 9


def latency_bounds() -> list[dict[str, float | None]]:
    """[lo, hi) in milliseconds for each class; the last is open-ended."""
    out: list[dict[str, float | None]] = []
    for index in range(LATENCY_CLASSES):
        lo = 0.0 if index == 0 else round(10 ** (index / 3), 3)
        hi = None if index == LATENCY_CLASSES - 1 else round(10 ** ((index + 1) / 3), 3)
        out.append({"lo_ms": lo, "hi_ms": hi})
    return out


# 'cluster|namespace|kind|name' for a span or log row, how a row is matched
# to a binding. A row no workload name is attached to gets '...|-|' and
# matches no binding.
_WK = (
    "concat(ResourceAttributes['k8s.cluster.name'], '|', "
    "ResourceAttributes['k8s.namespace.name'], '|', "
    "multiIf(ResourceAttributes['k8s.deployment.name'] != '', "
    "concat('deployment|', ResourceAttributes['k8s.deployment.name']), "
    "ResourceAttributes['k8s.statefulset.name'] != '', "
    "concat('statefulset|', ResourceAttributes['k8s.statefulset.name']), "
    "ResourceAttributes['k8s.daemonset.name'] != '', "
    "concat('daemonset|', ResourceAttributes['k8s.daemonset.name']), '-|'))"
)

# Every statement reads only the authorized workloads' rows in the window.
_NARROW = """Timestamp >= {start:DateTime64(9, 'UTC')}
  AND Timestamp < {end:DateTime64(9, 'UTC')}
  AND has({clusters:Array(String)}, ResourceAttributes['k8s.cluster.name'])
  AND has({namespaces:Array(String)}, ResourceAttributes['k8s.namespace.name'])
  AND has({workloads:Array(String)}, __WK__)"""


_BUCKET = "toUnixTimestamp(toStartOfInterval(Timestamp, INTERVAL {bucket:UInt32} SECOND))"
_CLASS = "toUInt8(least(12, greatest(0, floor(log10(greatest(Duration, 1) / 1e6) * 3))))"
_STATUS = (
    "if(SpanAttributes['http.response.status_code'] != '', "
    "SpanAttributes['http.response.status_code'], SpanAttributes['http.status_code'])"
)

_TRAFFIC = """
SELECT
    __WK__ AS wk,
    __BUCKET__ AS t,
    count() AS requests,
    countIf(StatusCode = 'Error') AS errors,
    quantileIf(0.95)(Duration, __NOT_LONG_LIVED__) AS p95_ns
FROM __DATABASE__.otel_traces
WHERE __NARROW__
  AND SpanKind = 'Server'
  AND __NOT_HEALTH_CHECK__
GROUP BY wk, t
ORDER BY wk, t
LIMIT {max_rows:UInt16}
"""

_LATENCY = """
SELECT
    __BUCKET__ AS t,
    __CLASS__ AS class,
    count() AS requests
FROM __DATABASE__.otel_traces
WHERE __NARROW__
  AND SpanKind = 'Server'
  AND __NOT_HEALTH_CHECK__
  AND __NOT_LONG_LIVED__
GROUP BY t, class
ORDER BY t, class
LIMIT {max_rows:UInt16}
"""

_CODES = """
SELECT
    __WK__ AS wk,
    __STATUS__ AS code,
    count() AS requests
FROM __DATABASE__.otel_traces
WHERE __NARROW__
  AND SpanKind = 'Server'
  AND __NOT_HEALTH_CHECK__
GROUP BY wk, code
ORDER BY requests DESC
LIMIT {max_rows:UInt16}
"""

_ROUTES = """
SELECT
    __WK__ AS wk,
    SpanName AS route,
    count() AS requests,
    countIf(StatusCode = 'Error') AS errors,
    countIf(startsWith(__STATUS__, '4')) AS client_errors,
    countIf(NOT __NOT_LONG_LIVED__) AS long_lived,
    quantileIf(0.5)(Duration, __NOT_LONG_LIVED__) AS p50_ns,
    quantileIf(0.95)(Duration, __NOT_LONG_LIVED__) AS p95_ns,
    quantileIf(0.99)(Duration, __NOT_LONG_LIVED__) AS p99_ns
FROM __DATABASE__.otel_traces
WHERE __NARROW__
  AND SpanKind = 'Server'
  AND __NOT_HEALTH_CHECK__
GROUP BY wk, route
ORDER BY requests DESC
LIMIT {max_routes:UInt16}
"""

# A call is a server span whose parent is a span of another bound workload
# in the same trace. Both sides are narrowed to the authorized workloads
# before the join, so the join only ever sees this environment's spans in
# this window.
_CALLS = """
SELECT
    p.wk AS caller,
    c.wk AS callee,
    count() AS calls,
    countIf(c.StatusCode = 'Error') AS errors,
    quantileIf(0.95)(c.Duration, c.Duration < {long_lived_ns:UInt64}) AS p95_ns
FROM (
    SELECT TraceId, ParentSpanId, StatusCode, Duration, __WK__ AS wk
    FROM __DATABASE__.otel_traces
    WHERE __NARROW__
      AND SpanKind = 'Server'
      AND ParentSpanId != ''
) AS c
INNER JOIN (
    SELECT TraceId, SpanId, __WK__ AS wk
    FROM __DATABASE__.otel_traces
    WHERE __NARROW__
) AS p
    ON c.TraceId = p.TraceId AND c.ParentSpanId = p.SpanId
WHERE p.wk != c.wk
GROUP BY caller, callee
ORDER BY calls DESC
LIMIT {max_calls:UInt16}
"""

# What a bound workload asked of a database: client spans that name a
# database system. The database itself is named by what the span says, not
# by another binding, so it is only ever a label here.
_DATABASES = """
SELECT
    __WK__ AS wk,
    if(SpanAttributes['db.system'] != '', SpanAttributes['db.system'],
       SpanAttributes['db.system.name']) AS system,
    if(SpanAttributes['db.name'] != '', SpanAttributes['db.name'],
       SpanAttributes['db.namespace']) AS name,
    SpanName AS operation,
    count() AS calls,
    countIf(StatusCode = 'Error') AS errors,
    quantile(0.95)(Duration) AS p95_ns
FROM __DATABASE__.otel_traces
WHERE __NARROW__
  AND SpanKind = 'Client'
  AND (SpanAttributes['db.system'] != '' OR SpanAttributes['db.system.name'] != '')
GROUP BY wk, system, name, operation
ORDER BY calls DESC
LIMIT {max_db:UInt16}
"""

_LOG_LINES = """
SELECT
    __BUCKET__ AS t,
    count() AS lines,
    countIf(SeverityNumber >= 17) AS errors,
    countIf(SeverityNumber >= 13 AND SeverityNumber < 17) AS warnings
FROM __DATABASE__.otel_logs
WHERE __NARROW__
GROUP BY t
ORDER BY t
LIMIT {max_rows:UInt16}
"""

_LOG_PATTERNS = """
SELECT
    pattern,
    count() AS lines,
    max(SeverityNumber) AS severity_number,
    argMax(SeverityText, SeverityNumber) AS severity_text,
    uniqExact(wk) AS workloads
FROM (
    SELECT
        SeverityNumber,
        SeverityText,
        __WK__ AS wk,
        left(__SHAPE__, {pattern_chars:UInt32}) AS pattern
    FROM __DATABASE__.otel_logs
    WHERE __NARROW__
)
GROUP BY pattern
ORDER BY lines DESC
LIMIT {max_patterns:UInt8}
"""


class OverviewQueryError(ValueError):
    """A request the overview refuses to express."""


@dataclass(frozen=True)
class OverviewQuery:
    statements: dict[str, tuple[str, int]]  # name -> (sql, max rows)
    parameters: dict[str, str]
    bucket_seconds: int
    bucket_starts: list[int]
    keys: dict[str, str]  # 'cluster|namespace|kind|name' -> binding id
    start: datetime
    end: datetime


def _array(values: list[str]) -> str:
    quoted = (v.replace("\\", "\\\\").replace("'", "\\'") for v in values)
    return "[" + ",".join(f"'{v}'" for v in quoted) + "]"


def _key(binding: dict[str, Any]) -> str:
    parts = workload_key(binding)
    if any("|" in part for part in parts):
        raise ScopeError("unsupported name")
    return "|".join(parts)


TRACE_STATEMENTS = ("traffic", "latency", "codes", "routes", "calls", "databases")
LOG_STATEMENTS = ("log_lines", "log_patterns")


def build_overview_query(
    *, database: str, bindings: list[dict[str, Any]], range_key: str, now: datetime
) -> OverviewQuery:
    if range_key not in RANGES:
        raise OverviewQueryError("unsupported range")
    if not bindings:
        raise OverviewQueryError("no bindings")
    if len(bindings) > MAX_BINDINGS:
        raise OverviewQueryError("too many bindings")
    try:
        keys = {_key(b): str(b["id"]) for b in bindings}
    except ScopeError as error:
        raise OverviewQueryError(str(error)) from error

    bucket = BUCKET_SECONDS[range_key]
    count = RANGES[range_key] // bucket
    now_s = int(now.timestamp())
    current = now_s - now_s % bucket
    starts = [current - bucket * (count - 1 - index) for index in range(count)]
    start = datetime.fromtimestamp(starts[0], UTC)
    end = datetime.fromtimestamp(current + bucket, UTC)

    split = [k.split("|") for k in keys]
    parameters = {
        "start": clickhouse_time(start),
        "end": clickhouse_time(end),
        "bucket": str(bucket),
        "clusters": _array(sorted({s[0] for s in split})),
        "namespaces": _array(sorted({s[1] for s in split})),
        "workloads": _array(sorted(keys)),
        "health_checks": health_check_parameter(bindings),
        "long_lived_ns": str(LONG_LIVED_SECONDS * 1_000_000_000),
        "max_rows": str(MAX_ROWS),
        "max_routes": str(MAX_ROUTES),
        "max_calls": str(MAX_CALLS),
        "max_db": str(MAX_DB_OPERATIONS),
        "max_patterns": str(MAX_LOG_PATTERNS),
        "pattern_chars": str(PATTERN_CHARS),
    }
    parameters.update({name: pattern.replace("\\", "\\\\") for name, pattern, _ in _REPLACEMENTS})

    db = safe_identifier(database)

    def sql(template: str) -> str:
        filled = (
            template.replace("__NARROW__", _NARROW)
            .replace("__WK__", _WK)
            .replace("__BUCKET__", _BUCKET)
            .replace("__CLASS__", _CLASS)
            .replace("__STATUS__", _STATUS)
            .replace("__SHAPE__", _shape("Body"))
            .replace("__DATABASE__", db)
        )
        return with_health_check_filter(filled)

    statements = {
        "traffic": (sql(_TRAFFIC), MAX_ROWS),
        "latency": (sql(_LATENCY), MAX_ROWS),
        "codes": (sql(_CODES), MAX_ROWS),
        "routes": (sql(_ROUTES), MAX_ROUTES),
        "calls": (sql(_CALLS), MAX_CALLS),
        "databases": (sql(_DATABASES), MAX_DB_OPERATIONS),
        "log_lines": (sql(_LOG_LINES), MAX_ROWS),
        "log_patterns": (sql(_LOG_PATTERNS), MAX_LOG_PATTERNS),
    }
    return OverviewQuery(
        statements=statements,
        parameters=parameters,
        bucket_seconds=bucket,
        bucket_starts=starts,
        keys=keys,
        start=start,
        end=end,
    )


# ---------------------------------------------------------------- normalize


def _count(value: Any) -> int:
    # ClickHouse's JSON format quotes 64-bit integers.
    if isinstance(value, bool):
        raise ValueError("count")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError("count")


def _ms(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number / 1_000_000, 3)


def _rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("row")
    return rows


def normalize_traffic(rows: list[dict[str, Any]], query: OverviewQuery) -> list[dict[str, Any]]:
    """One entry per requested binding, in request order, each with every
    bucket of the window: a bucket the store returned nothing for is zero
    requests and no p95, because the store answered."""
    index = {t: i for i, t in enumerate(query.bucket_starts)}
    out: dict[str, list[dict[str, Any]]] = {
        bid: [{"t": t, "requests": 0, "errors": 0, "p95_ms": None} for t in query.bucket_starts]
        for bid in query.keys.values()
    }
    for row in _rows(rows):
        bid = query.keys.get(str(row.get("wk")))
        slot = index.get(_count(row.get("t")))
        if bid is None or slot is None:
            continue
        requests = _count(row.get("requests"))
        out[bid][slot] = {
            "t": query.bucket_starts[slot],
            "requests": requests,
            "errors": _count(row.get("errors")),
            "p95_ms": _ms(row.get("p95_ns")) if requests else None,
        }
    return [{"binding_id": bid, "buckets": buckets} for bid, buckets in out.items()]


def normalize_latency(rows: list[dict[str, Any]], query: OverviewQuery) -> dict[str, Any]:
    index = {t: i for i, t in enumerate(query.bucket_starts)}
    cells = [[0] * LATENCY_CLASSES for _ in query.bucket_starts]
    for row in _rows(rows):
        slot = index.get(_count(row.get("t")))
        klass = _count(row.get("class"))
        if slot is None or not 0 <= klass < LATENCY_CLASSES:
            continue
        cells[slot][klass] += _count(row.get("requests"))
    totals = [sum(column[k] for column in cells) for k in range(LATENCY_CLASSES)]
    return {
        "classes": latency_bounds(),
        "slow_from_class": SLOW_CLASS,
        "cells": cells,
        "totals": totals,
    }


def normalize_codes(rows: list[dict[str, Any]], query: OverviewQuery) -> list[dict[str, Any]]:
    out: dict[str, dict[str, int]] = {bid: {} for bid in query.keys.values()}
    for row in _rows(rows):
        bid = query.keys.get(str(row.get("wk")))
        if bid is None:
            continue
        code = str(row.get("code") or "")
        code = code if code.isdigit() and len(code) == 3 else "unknown"
        out[bid][code] = out[bid].get(code, 0) + _count(row.get("requests"))
    return [{"binding_id": bid, "codes": codes} for bid, codes in out.items()]


def normalize_routes(rows: list[dict[str, Any]], query: OverviewQuery) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _rows(rows):
        bid = query.keys.get(str(row.get("wk")))
        if bid is None:
            continue
        requests = _count(row.get("requests"))
        long_lived = _count(row.get("long_lived"))
        out.append(
            {
                "binding_id": bid,
                "route": str(row.get("route") or ""),
                "requests": requests,
                "errors": _count(row.get("errors")),
                "client_errors": _count(row.get("client_errors")),
                "long_lived": long_lived,
                # Every request of the route was a long-lived connection:
                # there is no request latency to show, only that.
                "p50_ms": None if long_lived == requests else _ms(row.get("p50_ns")),
                "p95_ms": None if long_lived == requests else _ms(row.get("p95_ns")),
                "p99_ms": None if long_lived == requests else _ms(row.get("p99_ns")),
            }
        )
    return out


def normalize_calls(rows: list[dict[str, Any]], query: OverviewQuery) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _rows(rows):
        caller = query.keys.get(str(row.get("caller")))
        callee = query.keys.get(str(row.get("callee")))
        if caller is None or callee is None or caller == callee:
            continue
        out.append(
            {
                "from_binding_id": caller,
                "to_binding_id": callee,
                "calls": _count(row.get("calls")),
                "errors": _count(row.get("errors")),
                "p95_ms": _ms(row.get("p95_ns")),
            }
        )
    return out


def normalize_databases(rows: list[dict[str, Any]], query: OverviewQuery) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _rows(rows):
        bid = query.keys.get(str(row.get("wk")))
        if bid is None:
            continue
        out.append(
            {
                "binding_id": bid,
                "system": str(row.get("system") or "") or None,
                "database": str(row.get("name") or "") or None,
                "operation": str(row.get("operation") or ""),
                "calls": _count(row.get("calls")),
                "errors": _count(row.get("errors")),
                "p95_ms": _ms(row.get("p95_ns")),
            }
        )
    return out


def normalize_log_lines(rows: list[dict[str, Any]], query: OverviewQuery) -> list[dict[str, Any]]:
    index = {t: i for i, t in enumerate(query.bucket_starts)}
    out = [{"t": t, "lines": 0, "errors": 0, "warnings": 0} for t in query.bucket_starts]
    for row in _rows(rows):
        slot = index.get(_count(row.get("t")))
        if slot is None:
            continue
        out[slot] = {
            "t": query.bucket_starts[slot],
            "lines": _count(row.get("lines")),
            "errors": _count(row.get("errors")),
            "warnings": _count(row.get("warnings")),
        }
    return out


def normalize_log_patterns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _rows(rows):
        out.append(
            {
                "pattern": str(row.get("pattern") or ""),
                "lines": _count(row.get("lines")),
                "severity_number": _count(row.get("severity_number")),
                "severity_text": str(row.get("severity_text") or "") or None,
                "workloads": _count(row.get("workloads")),
            }
        )
    return out


def window_of(query: OverviewQuery) -> dict[str, str]:
    return {"start": query.start.isoformat(), "end": query.end.isoformat()}
