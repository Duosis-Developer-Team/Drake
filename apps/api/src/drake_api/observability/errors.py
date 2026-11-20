"""Error tracking: what went wrong in an environment, grouped (ADR-0036).

Three kinds of issue, each read in one statement over the environment's
bound workloads, narrowed in SQL exactly as the overview read is
(observability.overview):

- `exception`: a span event named `exception`, grouped by service, the
  exception type and the shape of its message. This is what an SDK
  records when code raises.
- `failed_request`: a request (server span, not a health check) that
  failed: status Error or an HTTP 5xx, grouped by service, endpoint and
  code.
- `error_log`: a log line at ERROR or above, grouped by service and the
  shape of the line (numbers, ids and addresses masked as in log
  patterns).

Each issue carries its count, when it was first and last seen in the
window, a count per bucket for its trend, and one sample: message, stack
trace, trace id, endpoints. Nothing is stored: an issue is what the store
says now, so "first seen" means first seen in this window, and the screen
says so.
"""

import hashlib
import math
from typing import Any

from drake_api.observability.health_checks import with_health_check_filter
from drake_api.observability.overview import _BUCKET, _NARROW, _WK, OverviewQuery
from drake_api.observability.patterns import _shape
from drake_api.observability.scope import parse_store_time
from drake_api.observability.store import safe_identifier

MAX_ISSUES = 100
MESSAGE_CHARS = 300
STACK_CHARS = 4000
TITLE_CHARS = 160
KINDS = ("exception", "failed_request", "error_log")

_STATUS = (
    "if(SpanAttributes['http.response.status_code'] != '', "
    "SpanAttributes['http.response.status_code'], SpanAttributes['http.status_code'])"
)

_EXCEPTIONS = """
SELECT
    wk,
    etype,
    shape,
    sum(c) AS n,
    min(first_at) AS first_seen,
    max(last_at) AS last_seen,
    any(message) AS message,
    any(stack) AS stacktrace,
    any(trace_id) AS trace_id,
    groupUniqArrayArray(3)(routes) AS routes,
    groupArray((t, c)) AS trend
FROM (
    SELECT
        __WK__ AS wk,
        attrs['exception.type'] AS etype,
        left(__EXC_SHAPE__, {title_chars:UInt32}) AS shape,
        __BUCKET__ AS t,
        count() AS c,
        min(Timestamp) AS first_at,
        max(Timestamp) AS last_at,
        any(left(attrs['exception.message'], {message_chars:UInt32})) AS message,
        any(left(attrs['exception.stacktrace'], {stack_chars:UInt32})) AS stack,
        any(TraceId) AS trace_id,
        groupUniqArray(3)(SpanName) AS routes
    FROM __DATABASE__.otel_traces
    ARRAY JOIN Events.Name AS event_name, Events.Attributes AS attrs
    WHERE __NARROW__
      AND event_name = 'exception'
    GROUP BY wk, etype, shape, t
)
GROUP BY wk, etype, shape
ORDER BY n DESC
LIMIT {max_issues:UInt16}
"""

_FAILED = """
SELECT
    wk,
    route,
    code,
    sum(c) AS n,
    min(first_at) AS first_seen,
    max(last_at) AS last_seen,
    any(message) AS message,
    any(trace_id) AS trace_id,
    groupArray((t, c)) AS trend
FROM (
    SELECT
        __WK__ AS wk,
        SpanName AS route,
        if(startsWith(__STATUS__, '5'), __STATUS__, 'error') AS code,
        __BUCKET__ AS t,
        count() AS c,
        min(Timestamp) AS first_at,
        max(Timestamp) AS last_at,
        any(left(StatusMessage, {message_chars:UInt32})) AS message,
        any(TraceId) AS trace_id
    FROM __DATABASE__.otel_traces
    WHERE __NARROW__
      AND SpanKind = 'Server'
      AND __NOT_HEALTH_CHECK__
      AND (StatusCode = 'Error' OR startsWith(__STATUS__, '5'))
    GROUP BY wk, route, code, t
)
GROUP BY wk, route, code
ORDER BY n DESC
LIMIT {max_issues:UInt16}
"""

_LOGS = """
SELECT
    wk,
    shape,
    sum(c) AS n,
    min(first_at) AS first_seen,
    max(last_at) AS last_seen,
    max(severity) AS severity_number,
    any(message) AS message,
    any(trace_id) AS trace_id,
    groupArray((t, c)) AS trend
FROM (
    SELECT
        __WK__ AS wk,
        left(__LOG_SHAPE__, {title_chars:UInt32}) AS shape,
        __BUCKET__ AS t,
        count() AS c,
        min(Timestamp) AS first_at,
        max(Timestamp) AS last_at,
        max(SeverityNumber) AS severity,
        any(left(Body, {message_chars:UInt32})) AS message,
        any(TraceId) AS trace_id
    FROM __DATABASE__.otel_logs
    WHERE __NARROW__
      AND SeverityNumber >= 17
    GROUP BY wk, shape, t
)
GROUP BY wk, shape
ORDER BY n DESC
LIMIT {max_issues:UInt16}
"""


def statements(database: str) -> dict[str, tuple[str, int]]:
    """The three statements, filled with the overview read's own
    fragments: the same narrowing, buckets and masking (observability.overview)."""

    def sql(template: str) -> str:
        filled = (
            template.replace("__NARROW__", _NARROW)
            .replace("__WK__", _WK)
            .replace("__BUCKET__", _BUCKET)
            .replace("__STATUS__", _STATUS)
            .replace("__EXC_SHAPE__", _shape("attrs['exception.message']"))
            .replace("__LOG_SHAPE__", _shape("Body"))
            .replace("__DATABASE__", safe_identifier(database))
        )
        return with_health_check_filter(filled)

    return {
        "exception": (sql(_EXCEPTIONS), MAX_ISSUES),
        "failed_request": (sql(_FAILED), MAX_ISSUES),
        "error_log": (sql(_LOGS), MAX_ISSUES),
    }


def parameters(query: OverviewQuery) -> dict[str, str]:
    return {
        **query.parameters,
        "max_issues": str(MAX_ISSUES),
        "message_chars": str(MESSAGE_CHARS),
        "stack_chars": str(STACK_CHARS),
        "title_chars": str(TITLE_CHARS),
    }


def _count(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("count")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and not math.isnan(value):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError("count")


def _trend(value: Any, buckets: list[int]) -> list[int]:
    """[(t, c), ...] from the store → one count per bucket of the window."""
    counts: dict[int, int] = {}
    for pair in value or []:
        if isinstance(pair, list | tuple) and len(pair) == 2:
            counts[_count(pair[0])] = counts.get(_count(pair[0]), 0) + _count(pair[1])
    return [counts.get(b, 0) for b in buckets]


def fingerprint(kind: str, binding_id: str, *parts: str) -> str:
    """A stable name for an issue: the same failure gets the same id from
    one read to the next, so a link to it keeps meaning the same thing."""
    raw = "\x1f".join([kind, binding_id, *parts])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def normalize(kind: str, rows: list[dict[str, Any]], query: OverviewQuery) -> list[dict[str, Any]]:
    """Store rows → issues. A row whose workload is not one of the
    authorized bindings is dropped (it cannot happen through the narrowed
    statement; this is the second lock)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("row")
        binding_id = query.keys.get(str(row.get("wk")))
        if binding_id is None:
            continue
        issue: dict[str, Any] = {
            "kind": kind,
            "binding_id": binding_id,
            "count": _count(row.get("n")),
            "first_seen": parse_store_time(row.get("first_seen")).isoformat(),
            "last_seen": parse_store_time(row.get("last_seen")).isoformat(),
            "trend": _trend(row.get("trend"), query.bucket_starts),
            "trace_id": _text(row.get("trace_id"), 64) or None,
            "message": _text(row.get("message"), MESSAGE_CHARS) or None,
        }
        if kind == "exception":
            etype = _text(row.get("etype"), 200) or "Exception"
            shape = _text(row.get("shape"), TITLE_CHARS)
            issue.update(
                id=fingerprint(kind, binding_id, etype, shape),
                title=f"{etype}: {shape}" if shape else etype,
                exception_type=etype,
                stacktrace=_text(row.get("stacktrace"), STACK_CHARS) or None,
                routes=[str(r) for r in row.get("routes") or []][:3],
            )
        elif kind == "failed_request":
            route = _text(row.get("route"), 300)
            code = _text(row.get("code"), 8)
            issue.update(
                id=fingerprint(kind, binding_id, route, code),
                title=f"{route} → {code}",
                route=route,
                code=code,
            )
        else:
            shape = _text(row.get("shape"), TITLE_CHARS)
            issue.update(
                id=fingerprint(kind, binding_id, shape),
                title=shape,
                severity_number=_count(row.get("severity_number") or 0),
            )
        out.append(issue)
    return out


def summarize(issues: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind = {kind: 0 for kind in KINDS}
    events = {kind: 0 for kind in KINDS}
    for issue in issues:
        by_kind[issue["kind"]] += 1
        events[issue["kind"]] += issue["count"]
    return {"issues": by_kind, "events": events, "total_issues": len(issues)}
