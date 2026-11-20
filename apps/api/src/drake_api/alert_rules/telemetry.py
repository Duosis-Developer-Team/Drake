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

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.alert_rules.model import LOG_SIGNALS, STORED_TELEMETRY, TRACE_SIGNALS
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
        values: dict[str, float | None] = dict.fromkeys(STORED_TELEMETRY)
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
    async with engine.connect() as connection:
        return await bindings_on(connection, ids)


async def bindings_on(connection: AsyncConnection, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """The labels the statements narrow by, for these bindings only."""
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
        values, _ = await self.sample_all(engine, binding_ids, now, [])
        return values

    async def sample_all(
        self,
        engine: AsyncEngine,
        binding_ids: list[uuid.UUID],
        now: datetime,
        filters: list["RuleFilter"],
    ) -> tuple[dict[str, dict[str, float | None]], dict[str, dict[str, float | None]]]:
        """(binding id → stored signal values, binding id → rule id → the
        value of that rule's filtered signal). Two statements for the page,
        and two more when any rule on it filters by text or endpoint."""
        connector = self._store.connector
        if connector is None or not (self._logs or self._traces) or not binding_ids:
            return {}, {}
        bindings = await _bindings(engine, binding_ids)
        if not bindings:
            return {}, {}
        statements, parameters, keys = build_statements(
            database=connector.database, bindings=bindings, now=now
        )
        if not keys:
            return {}, {}
        timeout = connector.timeout_seconds
        log_rows = await self._rows(statements["logs"], parameters, timeout) if self._logs else None
        trace_rows = (
            await self._rows(statements["traces"], parameters, timeout) if self._traces else None
        )
        values = summarize(keys, log_rows, trace_rows)
        rule_values: dict[str, dict[str, float | None]] = {}
        if filters:
            plan = build_filtered(
                database=connector.database,
                bindings=bindings,
                filters=filters,
                start=now - WINDOW,
                end=now,
                bucket_seconds=None,
            )
            f_logs = (
                await self._rows(plan.logs, plan.parameters, timeout)
                if plan.logs and self._logs
                else None
            )
            f_traces = (
                await self._rows(plan.traces, plan.parameters, timeout)
                if plan.traces and self._traces
                else None
            )
            rule_values = summarize_filtered(plan, f_logs, f_traces)
        return values, rule_values

    async def routes(
        self, connection: AsyncConnection, binding_ids: list[uuid.UUID], now: datetime
    ) -> list[str]:
        """Endpoints these bindings served in the last day, busiest first."""
        connector = self._store.connector
        if connector is None or not self._traces or not binding_ids:
            return []
        bindings = await bindings_on(connection, binding_ids)
        if not bindings:
            return []
        _, parameters, keys = build_statements(
            database=connector.database, bindings=bindings, now=now
        )
        if not keys:
            return []
        parameters = {**parameters, "start": clickhouse_time(now - SERIES_SPAN)}
        statement = _sql(_ROUTES, connector.database)
        rows = await self._rows(statement, parameters, connector.timeout_seconds)
        return [str(r.get("route")) for r in rows or [] if r.get("route")]

    async def series(
        self,
        connection: AsyncConnection,
        binding_ids: list[uuid.UUID],
        rule: "RuleFilter",
        now: datetime,
        redis: Any = None,
    ) -> dict[str, list[tuple[datetime, float | None]]] | None:
        """A filtered rule's signal per binding over the last day, in
        SERIES_BUCKET buckets: its history and its preview. None when the
        store cannot be read at all."""
        connector = self._store.connector
        enabled = self._logs if rule.signal in LOG_SIGNALS else self._traces
        if connector is None or not enabled:
            return None
        cache_key = _series_key(rule, binding_ids, now)
        if redis is not None:
            try:
                raw = await redis.get(cache_key)
                if raw:
                    cached = json.loads(raw)
                    return {
                        b: [(datetime.fromisoformat(t), v) for t, v in points]
                        for b, points in cached.items()
                    }
            except Exception:
                logger.debug("alert rules: series cache unreadable")
        bindings = await bindings_on(connection, binding_ids[:MAX_SERIES_BINDINGS])
        if not bindings:
            return {}
        end = now - timedelta(seconds=int(now.timestamp()) % SERIES_BUCKET)
        start = end - SERIES_SPAN
        out: dict[str, list[tuple[datetime, float | None]]] = {}
        gate = asyncio.Semaphore(3)

        async def one(chunk: list[dict[str, Any]]) -> None:
            plan = build_filtered(
                database=connector.database,
                bindings=chunk,
                filters=[rule],
                start=start,
                end=end,
                bucket_seconds=SERIES_BUCKET,
            )
            statement = plan.logs if rule.signal in LOG_SIGNALS else plan.traces
            if statement is None:
                return
            async with gate:
                rows = await self._rows(statement, plan.parameters, connector.timeout_seconds)
            if rows is None:
                return
            out.update(series_from_rows(plan, rule, rows, start, end))

        chunks = [bindings[i : i + SERIES_CHUNK] for i in range(0, len(bindings), SERIES_CHUNK)]
        await asyncio.gather(*(one(c) for c in chunks))
        if redis is not None and out:
            with contextlib.suppress(Exception):
                await redis.set(
                    cache_key,
                    json.dumps({b: [(t.isoformat(), v) for t, v in pts] for b, pts in out.items()}),
                    ex=SERIES_CACHE_SECONDS,
                )
        return out


# --- rules that filter: one text, or one endpoint (ADR-0035) ---------------

# Filters judged in one page's statements. More distinct texts or
# endpoints than this on one page are judged next cycle's way: not at all,
# and logged. A unit test pins it under the reader's column budget.
MAX_FILTERS = 20
# A filtered rule's history and preview: the last day in five-minute
# buckets, three bindings per statement (3 x 288 rows under the reader's
# 1000-row ceiling), at most this many bindings.
SERIES_BUCKET = 300
SERIES_SPAN = timedelta(hours=24)
SERIES_CHUNK = 3
MAX_SERIES_BINDINGS = 24
SERIES_CACHE_SECONDS = 120

# The endpoints a single-endpoint rule can name.
MAX_ROUTE_CHOICES = 100
_ROUTES = """
SELECT SpanName AS route, count() AS requests
FROM __DATABASE__.otel_traces
WHERE __NARROW__
  AND SpanKind = 'Server'
  AND __NOT_HEALTH_CHECK__
GROUP BY route
ORDER BY requests DESC
LIMIT 100
"""

_LOG_FLOOR = {"error_logs": 17, "fatal_logs": 21, "matching_logs": 0}


@dataclass(frozen=True)
class RuleFilter:
    rule_id: str
    signal: str
    log_contains: str | None
    route: str | None


@dataclass(frozen=True)
class FilteredPlan:
    logs: str | None
    traces: str | None
    parameters: dict[str, str]
    keys: dict[str, str]  # binding id -> workload key
    columns: dict[tuple[str, str], str]  # (signal, text) -> column
    filters: list[RuleFilter]


def _series_key(rule: RuleFilter, binding_ids: list[uuid.UUID], now: datetime) -> str:
    identity = {
        "signal": rule.signal,
        "text": rule.log_contains,
        "route": rule.route,
        "bindings": sorted(str(b) for b in binding_ids),
        "minute": int(now.timestamp()) // 60,
    }
    return "alert-rules:series:" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def build_filtered(
    *,
    database: str,
    bindings: list[dict[str, Any]],
    filters: list[RuleFilter],
    start: datetime,
    end: datetime,
    bucket_seconds: int | None,
) -> FilteredPlan:
    """Statements for every text and endpoint these filters name, over
    these bindings. Live (no bucket) groups by workload; a series groups by
    workload and bucket. Texts and endpoints travel as typed parameters."""
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
        "start": clickhouse_time(start),
        "end": clickhouse_time(end),
        "clusters": _array(sorted({x[0] for x in split})),
        "namespaces": _array(sorted({x[1] for x in split})),
        "workloads": _array(sorted(set(keys.values()))),
        "health_checks": health_check_parameter(named),
        "long_lived_ns": str(LONG_LIVED_SECONDS * 1_000_000_000),
        "slow_ns": str(SLOW_NS),
        "max_rows": str(MAX_ROWS),
    }
    bucket = (
        ", toUnixTimestamp(toStartOfInterval(Timestamp, INTERVAL "
        + str(int(bucket_seconds))
        + " SECOND)) AS t"
        if bucket_seconds
        else ""
    )
    group = "wk, t" if bucket_seconds else "wk"
    columns: dict[tuple[str, str], str] = {}
    conditions: list[str] = []
    routes: list[str] = []
    for f in filters:
        if f.signal in LOG_SIGNALS and f.log_contains is not None:
            pair = (f.signal, f.log_contains)
            if pair in columns or len(columns) >= MAX_FILTERS:
                continue
            index = len(columns)
            columns[pair] = f"c{index}"
            parameters[f"text{index}"] = f.log_contains
            floor = _LOG_FLOOR[f.signal]
            severity = f"SeverityNumber >= {floor} AND " if floor else ""
            conditions.append(
                f"countIf({severity}positionCaseInsensitiveUTF8(Body, {{text{index}:String}}) > 0)"
                f" AS c{index}"
            )
        elif f.signal in TRACE_SIGNALS and f.route is not None:
            if f.route not in routes and len(routes) < MAX_FILTERS:
                routes.append(f.route)
    logs = None
    if conditions:
        logs = _sql(
            "SELECT __WK__ AS wk"  # noqa: S608 - fixed fragments; values are typed parameters
            + bucket
            + ", "
            + ", ".join(conditions)
            + " FROM __DATABASE__.otel_logs WHERE __NARROW__ GROUP BY "
            + group
            + " LIMIT {max_rows:UInt16}",
            database,
        )
    traces = None
    if routes:
        parameters["routes"] = _array(sorted(routes))
        traces = _sql(
            "SELECT __WK__ AS wk" + bucket + ", SpanName AS route,"  # noqa: S608 - fixed fragments; values are typed parameters
            " countIf(__NOT_LONG_LIVED__) AS timed,"
            " countIf(__NOT_LONG_LIVED__ AND Duration >= {slow_ns:UInt64}) AS slow,"
            " quantileIf(0.95)(Duration, __NOT_LONG_LIVED__) AS p95_ns"
            " FROM __DATABASE__.otel_traces WHERE __NARROW__ AND SpanKind = 'Server'"
            " AND __NOT_HEALTH_CHECK__ AND has({routes:Array(String)}, SpanName)"
            " GROUP BY " + group + ", route LIMIT {max_rows:UInt16}",
            database,
        )
    return FilteredPlan(logs, traces, parameters, keys, columns, list(filters))


def _route_value(signal: str, row: dict[str, Any] | None) -> float | None:
    if signal == "slow_requests":
        return _count(row.get("slow")) if row else 0.0
    # The endpoint's own p95: one timed request is enough for a named one.
    if row is None or _count(row.get("timed")) < 1:
        return None
    return _seconds(row.get("p95_ns"))


def summarize_filtered(
    plan: FilteredPlan,
    log_rows: list[dict[str, Any]] | None,
    trace_rows: list[dict[str, Any]] | None,
) -> dict[str, dict[str, float | None]]:
    """binding id → rule id → value, for every filter the plan judged. A
    store that did not answer leaves its rules unknown."""
    logs_by_wk = {str(r.get("wk")): r for r in log_rows or []}
    traces_by = {(str(r.get("wk")), str(r.get("route"))): r for r in trace_rows or []}
    out: dict[str, dict[str, float | None]] = {}
    for binding_id, wk in plan.keys.items():
        values: dict[str, float | None] = {}
        for f in plan.filters:
            if f.signal in LOG_SIGNALS and f.log_contains is not None:
                column = plan.columns.get((f.signal, f.log_contains))
                if column is None or log_rows is None:
                    values[f.rule_id] = None
                    continue
                row = logs_by_wk.get(wk)
                values[f.rule_id] = _count(row.get(column)) if row else 0.0
            elif f.signal in TRACE_SIGNALS and f.route is not None:
                if trace_rows is None:
                    values[f.rule_id] = None
                    continue
                values[f.rule_id] = _route_value(f.signal, traces_by.get((wk, f.route)))
        out[binding_id] = values
    return out


def series_from_rows(
    plan: FilteredPlan,
    rule: RuleFilter,
    rows: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> dict[str, list[tuple[datetime, float | None]]]:
    """Every bucket of [start, end) for every binding of the plan, filled:
    counts are 0 where nothing arrived, an endpoint's p95 is unknown."""
    by: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        if rule.signal in TRACE_SIGNALS and str(row.get("route")) != rule.route:
            continue
        by[(str(row.get("wk")), int(_count(row.get("t"))))] = row
    column = plan.columns.get((rule.signal, rule.log_contains or ""))
    buckets = range(int(start.timestamp()), int(end.timestamp()), SERIES_BUCKET)
    out: dict[str, list[tuple[datetime, float | None]]] = {}
    for binding_id, wk in plan.keys.items():
        points: list[tuple[datetime, float | None]] = []
        for t in buckets:
            hit = by.get((wk, t))
            at = datetime.fromtimestamp(t, start.tzinfo)
            if rule.signal in LOG_SIGNALS:
                points.append((at, _count(hit.get(column)) if hit and column else 0.0))
            else:
                points.append((at, _route_value(rule.signal, hit)))
        out[binding_id] = points
    return out
