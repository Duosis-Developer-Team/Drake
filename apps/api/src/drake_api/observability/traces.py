"""Trace reads: reviewed statements, typed parameters, a safe envelope.

Spans come from OBI (ADR-0029) or from an application's own OpenTelemetry
SDK. Either way a service writes one span per request it handled
(`SpanKind = 'Server'`), its in-process steps as children and its outgoing
calls as client spans.

A request is every server span, with or without a parent. OBI propagates
no context, so its server spans are always roots. An SDK does: when
core-service calls auth-service, auth's server span has core's client span
as its parent, in another service. Counting only roots would make every
called service look idle. Within one process an instrumented framework
writes one server span per request and marks its own steps `Internal`, so
counting server spans counts each request once.

Three reads answer the questions an operator asks of a service, in order:

- **Routes** — which endpoints, how often, how many failed, how slow (p50,
  p95). One row per route, so a burst of `/orders/123`, `/orders/124`
  collapses into `GET /orders/*`.
- **Requests** — the recent or the slowest individual requests, each a
  link into its trace.
- **Trace** — every span of one trace that belongs to this service.

What decides whose spans these are is the binding row, never the request,
exactly as for logs (observability.scope). A trace that crosses into
another service is cut at the scope boundary: spans the caller was not
authorized for are not returned, even when they share the trace id.

No query language: a caller picks a window, errors-only or not, recent or
slowest, and a page size. Nothing else reaches the SQL text.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from drake_api.observability.health_checks import (
    health_check_parameter,
    with_health_check_filter,
)
from drake_api.observability.scope import (
    RANGES,
    SCOPE_PREDICATE,
    ScopeError,
    clickhouse_time,
    narrow_window,
    parse_store_time,
    scope_parameters,
)
from drake_api.observability.store import safe_identifier

TraceStatus = Literal["any", "error"]
TraceSort = Literal["recent", "slowest"]

MAX_LIMIT = 100
DEFAULT_LIMIT = 50
MAX_ROUTES = 20
MAX_SPANS = 500

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")

# Every statement below reads one service's spans in one window. A request a
# service handled is a server span (see the module docstring); its
# in-process steps are Internal, so one row is one request. Health checks
# and scrapes are not traffic (observability.health_checks): the routes and
# the request list leave them out, and the totals count them separately.
_ROUTES = """
SELECT
    SpanName AS route,
    count() AS requests,
    countIf(StatusCode = 'Error') AS errors,
    quantileIf(0.5)(Duration, __NOT_LONG_LIVED__) AS p50_ns,
    quantileIf(0.95)(Duration, __NOT_LONG_LIVED__) AS p95_ns
FROM __DATABASE__.otel_traces
WHERE Timestamp >= {start:DateTime64(9, 'UTC')}
  AND Timestamp < {end:DateTime64(9, 'UTC')}
  AND SpanKind = 'Server'
  AND __SCOPE__
  AND __NOT_HEALTH_CHECK__
GROUP BY route
ORDER BY requests DESC
LIMIT {max_routes:UInt8}
"""

_TOTALS = """
SELECT
    countIf(NOT __IS_HEALTH_CHECK__) AS requests,
    countIf(NOT __IS_HEALTH_CHECK__ AND StatusCode = 'Error') AS errors,
    quantileIf(0.95)(Duration, NOT __IS_HEALTH_CHECK__ AND __NOT_LONG_LIVED__) AS p95_ns,
    countIf(__IS_HEALTH_CHECK__) AS health_checks
FROM __DATABASE__.otel_traces
WHERE Timestamp >= {start:DateTime64(9, 'UTC')}
  AND Timestamp < {end:DateTime64(9, 'UTC')}
  AND SpanKind = 'Server'
  AND __SCOPE__
"""

# Method, status and path: the stable HTTP name first, the older one when a
# span carries only that (LEGACY_ATTRIBUTES says why). A legacy target can
# carry a query string, so only the part before `?` is read.
_REQUESTS = """
SELECT
    toString(Timestamp) AS ts,
    TraceId AS trace_id,
    SpanName AS route,
    Duration AS duration_ns,
    StatusCode AS status,
    if(SpanAttributes['http.request.method'] != '',
       SpanAttributes['http.request.method'],
       SpanAttributes['http.method']) AS method,
    if(SpanAttributes['http.response.status_code'] != '',
       SpanAttributes['http.response.status_code'],
       SpanAttributes['http.status_code']) AS http_status,
    if(SpanAttributes['url.path'] != '',
       SpanAttributes['url.path'],
       splitByChar('?', SpanAttributes['http.target'])[1]) AS path,
    ResourceAttributes['k8s.pod.name'] AS pod
FROM __DATABASE__.otel_traces
WHERE Timestamp >= {start:DateTime64(9, 'UTC')}
  AND Timestamp < {end:DateTime64(9, 'UTC')}
  AND SpanKind = 'Server'
  AND __SCOPE__
  AND __NOT_HEALTH_CHECK__
  AND ({errors_only:UInt8} = 0 OR StatusCode = 'Error')
ORDER BY __ORDER__
LIMIT {limit:UInt16}
"""

_ORDER: dict[str, str] = {"recent": "Timestamp DESC", "slowest": "Duration DESC, Timestamp DESC"}

_SPANS = """
SELECT
    toString(Timestamp) AS ts,
    SpanId AS span_id,
    ParentSpanId AS parent_span_id,
    SpanName AS name,
    SpanKind AS kind,
    Duration AS duration_ns,
    StatusCode AS status,
    StatusMessage AS status_message,
    SpanAttributes AS attributes,
    ResourceAttributes['k8s.pod.name'] AS pod
FROM __DATABASE__.otel_traces
WHERE TraceId = {trace_id:String}
  AND __SCOPE__
ORDER BY Timestamp ASC
LIMIT {max_spans:UInt16}
"""

# Span attributes worth showing. An allowlist, so a new attribute OBI starts
# emitting does not appear in Drake until someone has looked at it.
SHOWN_ATTRIBUTES = (
    "http.request.method",
    "http.route",
    "http.response.status_code",
    "url.path",
    "url.scheme",
    "server.address",
    "server.port",
    "network.protocol.version",
    "error.type",
    "db.system.name",
    "db.operation.name",
    "db.collection.name",
    "db.query.summary",
    "rpc.system",
    "rpc.method",
    "rpc.grpc.status_code",
    "messaging.system",
    "messaging.destination.name",
    # The tool an MCP server ran: one of the server's own catalog, so a
    # bounded name, never an argument or a result.
    "mcp.tool.name",
)

# The same facts under the names OpenTelemetry used before its HTTP and
# database conventions were declared stable. OBI writes the stable names.
# An application's SDK may write the older ones: Python's instrumentation
# still does by default, and switching it (OTEL_SEMCONV_STABILITY_OPT_IN)
# is an OTEL_* variable a co-installed Datadog tracer could also act on,
# so Hermes leaves it unset. Drake reads either and shows the stable name.
# Only facts already allowlisted above are mapped. A raw statement
# (`db.statement`) is deliberately not one of them.
LEGACY_ATTRIBUTES: dict[str, str] = {
    "http.request.method": "http.method",
    "http.response.status_code": "http.status_code",
    "url.path": "http.target",
    "url.scheme": "http.scheme",
    "server.port": "net.host.port",
    "network.protocol.version": "http.flavor",
    "db.system.name": "db.system",
    "db.operation.name": "db.operation",
    "db.collection.name": "db.sql.table",
}


def _shown_attributes(attributes: dict[str, Any]) -> dict[str, str]:
    """The allowlisted attributes of a span, stable names first."""
    shown: dict[str, str] = {}
    for key in SHOWN_ATTRIBUTES:
        value = attributes.get(key)
        if not value and key in LEGACY_ATTRIBUTES:
            value = attributes.get(LEGACY_ATTRIBUTES[key])
            if value and key == "url.path":
                # `http.target` is the path AND the query string.
                value = str(value).split("?", 1)[0].split("#", 1)[0]
        if value:
            shown[key] = str(value)
    return shown


class TraceQueryError(ValueError):
    """The request cannot become a trace query (bounded, safe message)."""


@dataclass(frozen=True, slots=True)
class Statement:
    sql: str
    parameters: dict[str, str]
    max_rows: int


@dataclass(frozen=True, slots=True)
class TraceListQuery:
    routes: Statement
    totals: Statement
    requests: Statement
    start: datetime
    end: datetime


def _sql(template: str, database: str) -> str:
    return with_health_check_filter(
        template.replace("__DATABASE__", safe_identifier(database)).replace(
            "__SCOPE__", SCOPE_PREDICATE.strip()
        )
    )


def _scope(binding: dict[str, Any]) -> dict[str, str]:
    try:
        return scope_parameters(binding)
    except ScopeError as error:
        raise TraceQueryError(str(error)) from error


def build_trace_list(
    *,
    database: str,
    binding: dict[str, Any],
    range_key: str,
    status: str,
    sort: str,
    limit: int,
    now: datetime,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> TraceListQuery:
    """The route summary, the totals and one page of requests for a binding.

    `binding` is the row `service_health.bindings.get_binding` returned for
    this caller; visibility has already been decided.
    """
    if range_key not in RANGES:
        raise TraceQueryError("unsupported range")
    if status not in ("any", "error"):
        raise TraceQueryError("unsupported status")
    if sort not in _ORDER:
        raise TraceQueryError("unsupported sort")
    if not 1 <= limit <= MAX_LIMIT:
        raise TraceQueryError(f"limit must be between 1 and {MAX_LIMIT}")

    scope = _scope(binding)
    end = now
    start = now - timedelta(seconds=RANGES[range_key])
    try:
        start, end = narrow_window(start, end, window_start, window_end)
    except ValueError as error:
        raise TraceQueryError(str(error)) from error
    window = {
        "start": clickhouse_time(start),
        "end": clickhouse_time(end),
        "health_checks": health_check_parameter([binding]),
        **scope,
    }
    return TraceListQuery(
        routes=Statement(
            _sql(_ROUTES, database), {**window, "max_routes": str(MAX_ROUTES)}, MAX_ROUTES
        ),
        totals=Statement(_sql(_TOTALS, database), window, 1),
        requests=Statement(
            _sql(_REQUESTS, database).replace("__ORDER__", _ORDER[sort]),
            {**window, "errors_only": "1" if status == "error" else "0", "limit": str(limit)},
            limit,
        ),
        start=start,
        end=end,
    )


def build_trace_detail(*, database: str, binding: dict[str, Any], trace_id: str) -> Statement:
    trace_id = trace_id.lower()
    if not _TRACE_ID.match(trace_id) or trace_id == "0" * 32:
        raise TraceQueryError("not a trace id")
    return Statement(
        _sql(_SPANS, database),
        {"trace_id": trace_id, "max_spans": str(MAX_SPANS), **_scope(binding)},
        MAX_SPANS,
    )


def _ms(value: Any) -> float | None:
    """Nanoseconds (UInt64, which ClickHouse's JSON renders as a string, or a
    float quantile) to milliseconds."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("duration") from error
    # A quantile over no rows is NaN: no measurement, not a duration.
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number / 1e6, 3)


def _count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("count") from error


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def normalize_routes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        requests = _count(row.get("requests"))
        errors = _count(row.get("errors"))
        out.append(
            {
                "route": str(row.get("route") or ""),
                "requests": requests,
                "errors": errors,
                "error_ratio": (errors / requests) if requests else None,
                "p50_ms": _ms(row.get("p50_ns")),
                "p95_ms": _ms(row.get("p95_ns")),
            }
        )
    return out


def normalize_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    row = rows[0] if rows else {}
    requests = _count(row.get("requests", 0))
    errors = _count(row.get("errors", 0))
    return {
        "requests": requests,
        "errors": errors,
        "error_ratio": (errors / requests) if requests else None,
        # No requests means no latency, not a latency of zero.
        "p95_ms": _ms(row.get("p95_ns")) if requests else None,
        # Probes and scrapes in the same window, set aside rather than hidden.
        "health_checks": _count(row.get("health_checks", 0)),
    }


def normalize_requests(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        trace_id = str(row.get("trace_id") or "")
        if not _TRACE_ID.match(trace_id):
            raise ValueError("trace_id")
        out.append(
            {
                "timestamp": parse_store_time(row.get("ts")).isoformat(),
                "trace_id": trace_id,
                "route": str(row.get("route") or ""),
                "duration_ms": _ms(row.get("duration_ns")),
                "error": row.get("status") == "Error",
                "method": _text(row.get("method")),
                "http_status": _text(row.get("http_status")),
                "path": _text(row.get("path")),
                "pod": _text(row.get("pod")),
            }
        )
    return out


def normalize_spans(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        attributes = row.get("attributes")
        if not isinstance(attributes, dict):
            raise ValueError("attributes")
        out.append(
            {
                "timestamp": parse_store_time(row.get("ts")).isoformat(),
                "span_id": str(row.get("span_id") or ""),
                "parent_span_id": _text(row.get("parent_span_id")),
                "name": str(row.get("name") or ""),
                "kind": str(row.get("kind") or ""),
                "duration_ms": _ms(row.get("duration_ns")),
                "error": row.get("status") == "Error",
                "status_message": _text(row.get("status_message")),
                "attributes": _shown_attributes(attributes),
                "pod": _text(row.get("pod")),
            }
        )
    return out
