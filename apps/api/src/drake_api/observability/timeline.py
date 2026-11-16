"""One service's telemetry over time: the detail page's timeline strip.

The strip answers "when did it start?" before anyone opens a request or a
log line: per time bucket, how many requests the service handled, how many
failed and how slow the slowest twentieth were, and how many error and
warning lines it wrote. Same store, same scope rules as the other reads
(observability.scope): every label comes from the authorized binding row.

Buckets are fixed per range and aligned to the epoch, so two readers of the
same window see the same buckets, and the newest bucket — the one still
filling — is included and marked as partial rather than read as a drop.
A bucket the store returned nothing for is a measured zero only because
the store answered; the API says which lane answered.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from drake_api.observability.scope import (
    RANGES,
    SCOPE_PREDICATE,
    ScopeError,
    clickhouse_time,
    scope_parameters,
)
from drake_api.observability.store import safe_identifier

# Bucket width per range, chosen so every range has a readable number of
# buckets (30 or 24) and the width divides the day evenly.
BUCKET_SECONDS: dict[str, int] = {"15m": 30, "1h": 150, "6h": 900, "24h": 3600}

MAX_BUCKETS = 64

_TRACES = """
SELECT
    toUnixTimestamp(toStartOfInterval(Timestamp, INTERVAL {bucket:UInt32} SECOND)) AS t,
    count() AS requests,
    countIf(StatusCode = 'Error') AS errors,
    quantile(0.95)(Duration) AS p95_ns
FROM __DATABASE__.otel_traces
WHERE Timestamp >= {start:DateTime64(9, 'UTC')}
  AND Timestamp < {end:DateTime64(9, 'UTC')}
  AND SpanKind = 'Server'
  AND __SCOPE__
GROUP BY t
ORDER BY t
LIMIT {max_buckets:UInt8}
"""

_LOGS = """
SELECT
    toUnixTimestamp(toStartOfInterval(Timestamp, INTERVAL {bucket:UInt32} SECOND)) AS t,
    count() AS lines,
    countIf(SeverityNumber >= 17) AS errors,
    countIf(SeverityNumber >= 13 AND SeverityNumber < 17) AS warnings
FROM __DATABASE__.otel_logs
WHERE Timestamp >= {start:DateTime64(9, 'UTC')}
  AND Timestamp < {end:DateTime64(9, 'UTC')}
  AND __SCOPE__
GROUP BY t
ORDER BY t
LIMIT {max_buckets:UInt8}
"""


class TimelineQueryError(ValueError):
    """A request the timeline refuses to express."""


@dataclass(frozen=True)
class TimelineQuery:
    traces: str
    logs: str
    parameters: dict[str, str]
    max_rows: int
    bucket_seconds: int
    bucket_starts: list[int]
    start: datetime
    end: datetime


def build_timeline_query(
    *, database: str, binding: dict[str, Any], range_key: str, now: datetime
) -> TimelineQuery:
    if range_key not in RANGES:
        raise TimelineQueryError("unsupported range")
    try:
        scope = scope_parameters(binding)
    except ScopeError as error:
        raise TimelineQueryError(str(error)) from error
    bucket = BUCKET_SECONDS[range_key]
    count = RANGES[range_key] // bucket
    now_s = int(now.timestamp())
    current = now_s - now_s % bucket
    starts = [current - bucket * (count - 1 - index) for index in range(count)]
    start = datetime.fromtimestamp(starts[0], UTC)
    end = datetime.fromtimestamp(current + bucket, UTC)
    db = safe_identifier(database)
    scope_sql = SCOPE_PREDICATE.strip()
    parameters = {
        "bucket": str(bucket),
        "start": clickhouse_time(start),
        "end": clickhouse_time(end),
        "max_buckets": str(MAX_BUCKETS),
        **scope,
    }
    return TimelineQuery(
        traces=_TRACES.replace("__DATABASE__", db).replace("__SCOPE__", scope_sql),
        logs=_LOGS.replace("__DATABASE__", db).replace("__SCOPE__", scope_sql),
        parameters=parameters,
        max_rows=MAX_BUCKETS,
        bucket_seconds=bucket,
        bucket_starts=starts,
        start=start,
        end=end,
    )


def _count(value: Any) -> int:
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


def _by_bucket(rows: list[dict[str, Any]], starts: list[int]) -> dict[int, dict[str, Any]]:
    known = set(starts)
    found: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("row")
        bucket = _count(row.get("t"))
        if bucket in known:
            found[bucket] = row
    return found


def normalize_traces(rows: list[dict[str, Any]], query: TimelineQuery) -> list[dict[str, Any]]:
    """One entry per bucket, oldest first. A bucket with no requests has no
    error ratio and no p95 — absence, not a perfect score."""
    found = _by_bucket(rows, query.bucket_starts)
    out = []
    for bucket in query.bucket_starts:
        row = found.get(bucket)
        requests = _count(row.get("requests")) if row else 0
        errors = _count(row.get("errors")) if row else 0
        out.append(
            {
                "requests": requests,
                "errors": errors,
                "error_ratio": round(errors / requests, 4) if requests else None,
                "p95_ms": _ms(row.get("p95_ns")) if row and requests else None,
            }
        )
    return out


def normalize_logs(rows: list[dict[str, Any]], query: TimelineQuery) -> list[dict[str, Any]]:
    found = _by_bucket(rows, query.bucket_starts)
    out = []
    for bucket in query.bucket_starts:
        row = found.get(bucket)
        out.append(
            {
                "lines": _count(row.get("lines")) if row else 0,
                "errors": _count(row.get("errors")) if row else 0,
                "warnings": _count(row.get("warnings")) if row else 0,
            }
        )
    return out


def bucket_frame(query: TimelineQuery, now: datetime) -> list[dict[str, Any]]:
    """The buckets themselves: start, end and whether the bucket is still
    filling (the newest one), so a short last bar is not read as a drop."""
    width = timedelta(seconds=query.bucket_seconds)
    frame = []
    for bucket in query.bucket_starts:
        start = datetime.fromtimestamp(bucket, UTC)
        frame.append(
            {
                "start": start.isoformat(),
                "end": (start + width).isoformat(),
                "partial": start + width > now,
            }
        )
    return frame
