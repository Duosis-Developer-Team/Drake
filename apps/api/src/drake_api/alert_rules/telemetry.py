"""What the logs and traces said about each service in the last minutes
(ADR-0034): the numbers log- and trace-based alert rules are judged on.

The incident runner takes one sample per binding per cycle (ADR-0031). The
health verdict supplies the metric signals. This module supplies the rest,
read from the store in two statements per page of bindings, never one per
binding:

- `error_logs`: log lines at ERROR or above in the window.
- `fatal_logs`: log lines at FATAL in the window.
- `slow_requests`: requests (server spans, without health checks and
  long-lived connections) that took one second or longer.
- `slowest_route_p95_seconds`: the p95 of the slowest route that served at
  least `MIN_ROUTE_REQUESTS` requests, so one stray request is not a route.

Scope is the binding rows': the statements narrow in SQL to their exact
(cluster, namespace, kind, workload), as the overview read does
(observability.overview). A store that does not answer gives no values, and
a missing value moves no rule: no data is neither a breach nor a recovery.
"""

import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.alert_rules.model import TELEMETRY_SIGNALS
from drake_api.observability.health_checks import (
    LONG_LIVED_SECONDS,
    health_check_parameter,
    with_health_check_filter,
    with_http_probes,
)
from drake_api.observability.overview import _NARROW, _WK, _array, _key
from drake_api.observability.scope import WORKLOAD_ATTRIBUTE, ScopeError, clickhouse_time
from drake_api.observability.store import ClickHouseStore, safe_identifier

logger = logging.getLogger("drake_api.alert_rules.telemetry")

# Each sample looks back this far. A minute-apart runner sees every line in
# five samples, so ingestion delay cannot hide one, and a rule that fires
# on a single error line goes back to normal five minutes after the last.
WINDOW = timedelta(minutes=5)
MIN_ROUTE_REQUESTS = 5
SLOW_NS = 1_000_000_000
# One row per workload. The reader profile refuses more than 1000.
MAX_ROWS = 1000


_LOGS = """
SELECT
    __WK__ AS wk,
    countIf(SeverityNumber >= 17) AS errors,
    countIf(SeverityNumber >= 21) AS fatal
FROM __DATABASE__.otel_logs
WHERE __NARROW__
GROUP BY wk
LIMIT {max_rows:UInt16}
"""

_TRACES = """
SELECT
    wk,
    sum(slow) AS slow,
    countIf(timed >= {min_route:UInt32}) AS routes,
    maxIf(p95_ns, timed >= {min_route:UInt32}) AS route_p95_ns
FROM (
    SELECT
        __WK__ AS wk,
        SpanName AS route,
        countIf(__NOT_LONG_LIVED__) AS timed,
        countIf(__NOT_LONG_LIVED__ AND Duration >= {slow_ns:UInt64}) AS slow,
        quantileIf(0.95)(Duration, __NOT_LONG_LIVED__) AS p95_ns
    FROM __DATABASE__.otel_traces
    WHERE __NARROW__
      AND SpanKind = 'Server'
      AND __NOT_HEALTH_CHECK__
    GROUP BY wk, route
)
GROUP BY wk
LIMIT {max_rows:UInt16}
"""


def _sql(template: str, database: str) -> str:
    filled = (
        template.replace("__NARROW__", _NARROW)
        .replace("__WK__", _WK)
        .replace("__DATABASE__", safe_identifier(database))
    )
    return with_health_check_filter(filled)


def build_statements(
    *, database: str, bindings: list[dict[str, Any]], now: datetime
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """(logs statement, traces statement, parameters) and the workload key
    of each binding id. Bindings the store cannot name are left out."""
    keys: dict[str, str] = {}
    named: list[dict[str, Any]] = []
    for binding in bindings:
        try:
            keys[str(binding["id"])] = _key(binding)
        except (ScopeError, KeyError):
            continue
        named.append(binding)
    split = [k.split("|") for k in keys.values()]
    parameters = {
        "start": clickhouse_time(now - WINDOW),
        "end": clickhouse_time(now),
        "clusters": _array(sorted({s[0] for s in split})),
        "namespaces": _array(sorted({s[1] for s in split})),
        "workloads": _array(sorted(set(keys.values()))),
        "health_checks": health_check_parameter(named),
        "long_lived_ns": str(LONG_LIVED_SECONDS * 1_000_000_000),
        "min_route": str(MIN_ROUTE_REQUESTS),
        "slow_ns": str(SLOW_NS),
        "max_rows": str(MAX_ROWS),
    }
    statements = {"logs": _sql(_LOGS, database), "traces": _sql(_TRACES, database)}
    return statements, parameters, keys


def _count(value: Any) -> float:
    # ClickHouse's JSON format quotes 64-bit integers.
    if isinstance(value, bool):
        raise ValueError("count")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return float(int(value))
    raise ValueError("count")


def _seconds(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number / 1_000_000_000, 4)


def summarize(
    keys: dict[str, str],
    log_rows: list[dict[str, Any]] | None,
    trace_rows: list[dict[str, Any]] | None,
) -> dict[str, dict[str, float | None]]:
    """binding id → telemetry signal values. `None` rows mean that store
    did not answer: every value it would have given is unknown. A binding
    with no row in an answer had nothing in the window."""
    by_wk_logs = {str(r.get("wk")): r for r in log_rows or []}
    by_wk_traces = {str(r.get("wk")): r for r in trace_rows or []}
    out: dict[str, dict[str, float | None]] = {}
    for binding_id, wk in keys.items():
        values: dict[str, float | None] = dict.fromkeys(TELEMETRY_SIGNALS)
        if log_rows is not None:
            row = by_wk_logs.get(wk)
            values["error_logs"] = _count(row.get("errors")) if row else 0.0
            values["fatal_logs"] = _count(row.get("fatal")) if row else 0.0
        if trace_rows is not None:
            row = by_wk_traces.get(wk)
            values["slow_requests"] = _count(row.get("slow")) if row else 0.0
            routes = _count(row.get("routes")) if row else 0.0
            values["slowest_route_p95_seconds"] = (
                _seconds(row.get("route_p95_ns")) if row and routes > 0 else None
            )
        out[binding_id] = values
    return out


async def _bindings(engine: AsyncEngine, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """The labels the statements narrow by, for these bindings only."""
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT b.id, c.cluster_ref, c.id, b.namespace, b.workload_kind,
                           b.workload_name, b.resolved_resource_uid
                    FROM service_workload_bindings b
                    JOIN clusters c ON c.id = b.cluster_id
                    WHERE b.id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
        ).all()
        bindings = [
            {
                "id": str(row[0]),
                "cluster": {"cluster_ref": row[1], "id": str(row[2])},
                "namespace": row[3],
                "workload_kind": row[4],
                "workload_name": row[5],
                "resolution": {"resource_uid": row[6]},
            }
            for row in rows
            if row[4] in WORKLOAD_ATTRIBUTE
        ]
        return await with_http_probes(connection, bindings)


class TelemetrySampler:
    """Reads one page of bindings' telemetry signals. Off (returns nothing)
    when no store is configured or both signals are switched off."""

    def __init__(self, store: ClickHouseStore, *, logs_enabled: bool, traces_enabled: bool) -> None:
        self._store = store
        self._logs = logs_enabled
        self._traces = traces_enabled

    async def _rows(
        self, statement: str, parameters: dict[str, str], timeout: float
    ) -> list[dict[str, Any]] | None:
        try:
            result = await self._store.query(
                statement,
                parameters,
                max_rows=MAX_ROWS,
                timeout_seconds=timeout,
                correlation_id="alert-rules-telemetry",
            )
        except Exception:
            # Unknown, not zero: a rule must not resolve because the store
            # was unreachable this minute.
            logger.warning("alert rules: telemetry read failed")
            return None
        rows = result.rows
        return rows if isinstance(rows, list) else None

    async def sample(
        self, engine: AsyncEngine, binding_ids: list[uuid.UUID], now: datetime
    ) -> dict[str, dict[str, float | None]]:
        connector = self._store.connector
        if connector is None or not (self._logs or self._traces) or not binding_ids:
            return {}
        bindings = await _bindings(engine, binding_ids)
        if not bindings:
            return {}
        statements, parameters, keys = build_statements(
            database=connector.database, bindings=bindings, now=now
        )
        if not keys:
            return {}
        timeout = connector.timeout_seconds
        log_rows = await self._rows(statements["logs"], parameters, timeout) if self._logs else None
        trace_rows = (
            await self._rows(statements["traces"], parameters, timeout) if self._traces else None
        )
        return summarize(keys, log_rows, trace_rows)
