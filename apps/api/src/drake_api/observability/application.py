"""Golden signals from traces, for services that publish no HTTP metrics.

A service's health has an Application section: request rate, error rate
and p95 latency. Today those come only from Prometheus, and only when the
application exports HTTP metrics itself — which most services here do not,
so the section reads "not configured" almost everywhere. The kernel-level
traces Drake already stores (OBI, ADR-0029) carry the same three numbers
for every request a service handled, without the application doing
anything.

This module reads them for the health policy's window. It is a fallback:
the orchestrator asks only when the preset's metrics answered nothing, so a
service that does publish metrics keeps them.

What it refuses to do is call an unobserved service healthy. A workload
with no server span in the last day is not being traced at all (its
namespace or node is not instrumented), and "0 requests" there would be
read as a quiet, healthy service. That case returns None and the section
stays honestly "not configured". A workload that has been traced recently
but served nothing in the window is genuinely idle and says so: zero
requests, no error rate, no p95.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from drake_api.observability.health_checks import (
    health_check_parameter,
    with_health_check_filter,
)
from drake_api.observability.scope import (
    SCOPE_PREDICATE,
    ScopeError,
    clickhouse_time,
    scope_parameters,
)
from drake_api.observability.store import (
    ClickHouseStore,
    StoreContractError,
    StoreUnavailableError,
    safe_identifier,
)

logger = logging.getLogger("drake_api.observability.application")

# How far back "this workload is traced at all" looks.
SEEN_LOOKBACK = timedelta(hours=24)
# Below this many requests in the window, a ratio and a p95 are noise: one
# failed request out of ten would read as a 10% error rate and page someone.
# Same floor as the "where is the problem?" board. The rate is still shown.
MIN_REQUESTS_TO_JUDGE = 20

_STATEMENT = """
SELECT
    count() AS seen,
    countIf(Timestamp >= {window_start:DateTime64(9, 'UTC')} AND __NOT_HEALTH_CHECK__) AS requests,
    countIf(Timestamp >= {window_start:DateTime64(9, 'UTC')} AND __NOT_HEALTH_CHECK__
            AND StatusCode = 'Error') AS errors,
    quantileIf(0.95)(Duration,
                     Timestamp >= {window_start:DateTime64(9, 'UTC')} AND __NOT_HEALTH_CHECK__
                     AND __NOT_LONG_LIVED__)
        AS p95_ns
FROM __DATABASE__.otel_traces
WHERE Timestamp >= {seen_start:DateTime64(9, 'UTC')}
  AND Timestamp < {end:DateTime64(9, 'UTC')}
  AND SpanKind = 'Server'
  AND __SCOPE__
"""


@dataclass(frozen=True)
class TraceGoldenSignals:
    """What the traces say for the window. `None` fields mean "not
    measurable here" (no requests), never zero."""

    requests: int
    request_rate: float
    error_ratio: float | None
    latency_p95_seconds: float | None


@dataclass(frozen=True)
class ApplicationQuery:
    statement: str
    parameters: dict[str, str]


def build_application_query(
    *, database: str, context: dict[str, Any], window_seconds: int, now: datetime
) -> ApplicationQuery:
    """`context` is the orchestrator's binding context (cluster_ref,
    namespace, workload kind and name). Raises ScopeError for a kind the
    store cannot name."""
    binding = {
        "namespace": context["namespace"],
        "workload_kind": context["workload_kind"],
        "workload_name": context["workload_name"],
        "cluster": {"cluster_ref": context["cluster_ref"]},
        "http_probes": context.get("http_probes") or [],
    }
    scope = scope_parameters(binding)
    # `seen` counts health checks too: a probe answered is proof the
    # workload is traced. The rate, errors and p95 do not.
    statement = with_health_check_filter(
        _STATEMENT.replace("__DATABASE__", safe_identifier(database)).replace(
            "__SCOPE__", SCOPE_PREDICATE.strip()
        )
    )
    return ApplicationQuery(
        statement=statement,
        parameters={
            "window_start": clickhouse_time(now - timedelta(seconds=window_seconds)),
            "seen_start": clickhouse_time(now - SEEN_LOOKBACK),
            "end": clickhouse_time(now),
            "health_checks": health_check_parameter([binding]),
            **scope,
        },
    )


def _count(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("count")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError("count")


def normalize_application(
    rows: list[dict[str, Any]], window_seconds: int
) -> TraceGoldenSignals | None:
    """None when the workload is not traced at all; otherwise the window's
    numbers, idle included."""
    if not rows:
        return None
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("rows")
    row = rows[0]
    if _count(row.get("seen")) == 0:
        return None
    requests = _count(row.get("requests"))
    errors = _count(row.get("errors"))
    p95: float | None = None
    judged = requests >= MIN_REQUESTS_TO_JUDGE
    if judged:
        raw = row.get("p95_ns")
        if raw is not None:
            number = float(raw)
            if not (math.isnan(number) or math.isinf(number)):
                p95 = number / 1_000_000_000
    return TraceGoldenSignals(
        requests=requests,
        request_rate=requests / window_seconds,
        error_ratio=(errors / requests) if judged else None,
        latency_p95_seconds=p95,
    )


__all__ = [
    "MIN_REQUESTS_TO_JUDGE",
    "SEEN_LOOKBACK",
    "ApplicationQuery",
    "ScopeError",
    "TraceGoldenReader",
    "TraceGoldenSignals",
    "build_application_query",
    "normalize_application",
]


class TraceGoldenReader:
    """The orchestrator's fallback source for golden signals.

    Returns None — and so changes nothing — when traces are off, no store is
    configured, the workload is not traced, or the store did not answer. A
    trace read can only ever fill an empty Application section; it never
    turns a reading into "unavailable" or "partial", because it is optional.
    """

    def __init__(self, store: ClickHouseStore, *, enabled: bool) -> None:
        self._store = store
        self._enabled = enabled

    async def __call__(
        self, context: dict[str, Any], window_seconds: int, now: datetime
    ) -> TraceGoldenSignals | None:
        connector = self._store.connector
        if not self._enabled or connector is None:
            return None
        try:
            query = build_application_query(
                database=connector.database, context=context, window_seconds=window_seconds, now=now
            )
        except (ScopeError, StoreContractError):
            return None
        try:
            result = await self._store.query(
                query.statement,
                query.parameters,
                max_rows=1,
                timeout_seconds=connector.timeout_seconds,
                correlation_id="health-golden-signals",
            )
            return normalize_application(result.rows, window_seconds)
        except (StoreUnavailableError, StoreContractError, ValueError) as error:
            code = getattr(error, "code", "store_malformed_row")
            logger.warning("trace golden signals unavailable", extra={"code": code})
            return None
