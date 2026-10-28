"""The log query: one reviewed statement, typed parameters, a safe envelope.

There is no log query language in Drake, by the same reasoning as ADR-0004:
a caller chooses a window, a minimum severity, an optional plain-text term
and a page size. Everything that decides WHICH logs those are — cluster,
namespace, workload — comes from the binding row the caller was authorized
for, never from the request.

The term is a substring match (`positionCaseInsensitive`), sent as a typed
parameter. It is not a pattern: no regex, no wildcard, no operator, so it
cannot be shaped into an expensive scan beyond the window and the server's
own execution ceiling.

Absence is reported as absence. An empty page says "no log lines matched in
this window", which is a different statement from "this service logs
nothing" and from "the store could not be read"; the router keeps the three
apart (ADR-0011).
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from drake_api.logging import redact
from drake_api.observability.store import safe_identifier

# Fixed windows, exactly like the service-health series: no request can ask
# for a month of logs.
LOG_RANGES: dict[str, int] = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400}
DEFAULT_LOG_RANGE = "1h"

Severity = Literal["any", "debug", "info", "warn", "error", "fatal"]

# OpenTelemetry severity numbers: each name is the start of its band.
# `any` includes records with no severity at all (number 0), which is what a
# plain container log line carries.
_SEVERITY_FLOOR: dict[str, int] = {
    "any": 0,
    "debug": 5,
    "info": 9,
    "warn": 13,
    "error": 17,
    "fatal": 21,
}

MAX_LIMIT = 200
DEFAULT_LIMIT = 100
MAX_TERM_LENGTH = 100
# What a displayed body is cut to. The store already bounds bodies at
# ingest; this bounds what one API page can carry.
MAX_BODY_CHARS = 4096

# Printable text only. Control characters have no place in a search term and
# would only make a log line look like something it is not.
_TERM = re.compile(r"^[^\x00-\x1f\x7f]*$")

# The resource attribute that names a workload of each bound kind. These are
# the OpenTelemetry semantic-convention keys the k8sattributes processor
# writes, so a binding's kind decides the key server-side.
_WORKLOAD_ATTRIBUTE: dict[str, str] = {
    "Deployment": "k8s.deployment.name",
    "StatefulSet": "k8s.statefulset.name",
    "DaemonSet": "k8s.daemonset.name",
}

_STATEMENT = """
SELECT
    toString(Timestamp) AS ts,
    SeverityText AS severity_text,
    SeverityNumber AS severity_number,
    substring(Body, 1, {{max_body:UInt32}}) AS body,
    TraceId AS trace_id,
    SpanId AS span_id,
    ResourceAttributes['k8s.pod.name'] AS pod,
    ResourceAttributes['k8s.container.name'] AS container
FROM {database}.otel_logs
WHERE Timestamp >= {{start:DateTime64(9, 'UTC')}}
  AND Timestamp < {{end:DateTime64(9, 'UTC')}}
  AND ResourceAttributes['k8s.cluster.name'] = {{cluster:String}}
  AND ResourceAttributes['k8s.namespace.name'] = {{namespace:String}}
  AND ResourceAttributes[{{workload_attribute:String}}] = {{workload:String}}
  AND SeverityNumber >= {{min_severity:UInt8}}
  AND ({{term:String}} = '' OR positionCaseInsensitive(Body, {{term:String}}) > 0)
ORDER BY Timestamp DESC
LIMIT {{limit:UInt16}}
"""


class LogQueryError(ValueError):
    """The request cannot become a log query (bounded, safe message)."""


@dataclass(frozen=True, slots=True)
class LogQuery:
    statement: str
    parameters: dict[str, str]
    limit: int
    start: datetime
    end: datetime


def _clickhouse_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def build_log_query(
    *,
    database: str,
    binding: dict[str, Any],
    range_key: str,
    severity: str,
    term: str,
    limit: int,
    before: datetime | None,
    now: datetime,
) -> LogQuery:
    """Compile a log read for one authorized binding.

    `binding` is the row `service_health.bindings.get_binding` returned for
    this caller. Nothing here re-checks visibility; the caller must have.
    """
    if range_key not in LOG_RANGES:
        raise LogQueryError("unsupported range")
    if severity not in _SEVERITY_FLOOR:
        raise LogQueryError("unsupported severity")
    if not 1 <= limit <= MAX_LIMIT:
        raise LogQueryError(f"limit must be between 1 and {MAX_LIMIT}")
    if len(term) > MAX_TERM_LENGTH or not _TERM.match(term):
        raise LogQueryError("unsupported search term")
    attribute = _WORKLOAD_ATTRIBUTE.get(str(binding.get("workload_kind")))
    if attribute is None:
        raise LogQueryError("unsupported workload kind")

    end = now
    start = now - timedelta(seconds=LOG_RANGES[range_key])
    if before is not None:
        # A page cursor narrows the window from above; it can never move it
        # outside the range the caller chose.
        before = before.astimezone(UTC)
        if before <= start:
            raise LogQueryError("cursor is outside the selected range")
        # The cursor is the previous page's last timestamp truncated to the
        # microsecond. One microsecond past it keeps every line that shares
        # that microsecond, so the boundary repeats rather than skips.
        end = min(end, before + timedelta(microseconds=1))

    statement = _STATEMENT.format(database=safe_identifier(database))
    parameters = {
        "max_body": str(MAX_BODY_CHARS),
        "start": _clickhouse_time(start),
        "end": _clickhouse_time(end),
        "cluster": str(binding["cluster"]["cluster_ref"]),
        "namespace": str(binding["namespace"]),
        "workload_attribute": attribute,
        "workload": str(binding["workload_name"]),
        "min_severity": str(_SEVERITY_FLOOR[severity]),
        "term": term.strip(),
        "limit": str(limit),
    }
    return LogQuery(statement=statement, parameters=parameters, limit=limit, start=start, end=end)


def _parse_store_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp")
    # ClickHouse renders DateTime64(9) as `YYYY-MM-DD HH:MM:SS.fffffffff`;
    # Python keeps microseconds, which is all a page cursor needs.
    head, _, fraction = value.partition(".")
    parsed = datetime.strptime(head, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    if fraction:
        parsed = parsed.replace(microsecond=int(fraction[:6].ljust(6, "0")))
    return parsed


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Store rows → the API's log entry shape. Refuses anything unexpected."""
    entries: list[dict[str, Any]] = []
    for row in rows:
        timestamp = _parse_store_time(row.get("ts"))
        body = row.get("body")
        if not isinstance(body, str):
            raise ValueError("body")
        severity_number = row.get("severity_number")
        if not isinstance(severity_number, int):
            raise ValueError("severity_number")
        entries.append(
            {
                "timestamp": timestamp.isoformat(),
                "severity": str(row.get("severity_text") or "") or None,
                "severity_number": severity_number,
                # The collector redacts before storage; this is the second
                # pass, so an ingest path that missed it is still covered.
                "body": redact(body),
                "trace_id": str(row.get("trace_id") or "") or None,
                "span_id": str(row.get("span_id") or "") or None,
                "pod": str(row.get("pod") or "") or None,
                "container": str(row.get("container") or "") or None,
            }
        )
    return entries


def next_cursor(entries: list[dict[str, Any]], limit: int) -> str | None:
    """The timestamp to pass as `before` for the next page, if there may be one.

    Timestamp-only, so lines sharing the boundary microsecond repeat on the
    next page (see `build_log_query`). That is chosen over the alternative:
    a repeated line is visible, a silently skipped one is not.
    """
    if len(entries) < limit or not entries:
        return None
    return str(entries[-1]["timestamp"])
