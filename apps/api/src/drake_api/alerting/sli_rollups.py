"""Five-minute request counts per binding, kept for objectives (migration 0031).

The trace store keeps 72 hours; an objective is judged over up to 30 days.
This writer reads the closed five-minute buckets from the store — server
spans, health checks left out, the same "requests" every other screen
counts — and keeps only numbers: requests, failures, how many had a
measurable duration and how many of those took longer than each fixed
threshold. No span, attribute or route is copied.

Every bucket the store answered for is written, a quiet one as zeros. A
bucket that is missing therefore means "not observed" (the store did not
answer, or the binding did not exist yet), which is what lets a window say
`partial` instead of quietly averaging over a gap.

Late spans: the newest buckets are read again on every run, so a span
ingested a minute late still lands in its bucket.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.alert_rules.telemetry import MAX_ROWS, _count, _sql, bindings_on
from drake_api.observability.health_checks import LONG_LIVED_SECONDS, health_check_parameter
from drake_api.observability.overview import _array, _key
from drake_api.observability.scope import ScopeError, clickhouse_time
from drake_api.observability.store import ClickHouseStore

logger = logging.getLogger("drake_api.alerting.sli_rollups")

BUCKET_SECONDS = 300
THRESHOLDS_MS: tuple[int, ...] = (100, 250, 500, 1000, 2500, 5000)
# Spans arrive a little after they end; a bucket is read once it is this old.
SETTLE = timedelta(minutes=2)
# Rewritten every run, for spans ingested late.
REREAD_BUCKETS = 3
# What the trace store still holds, less an hour of margin.
BACKFILL = timedelta(hours=71)
# The longest objective window (30 days) and a little more.
RETENTION = timedelta(days=35)
LEASE_KEY = "sli:rollup:cycle"
# Buckets per statement: bindings x buckets stays under the reader's row cap.
MAX_BUCKETS_PER_READ = 36

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# Fixed fragments only; every value is a typed parameter.
_STATEMENT = (
    "SELECT __WK__ AS wk,"  # noqa: S608
    " toUnixTimestamp(toStartOfInterval(Timestamp, INTERVAL 300 SECOND)) AS t,"
    " count() AS requests,"
    " countIf(StatusCode = 'Error') AS errors,"
    " countIf(__NOT_LONG_LIVED__) AS timed"
    + "".join(
        f", countIf(__NOT_LONG_LIVED__ AND Duration > {{over_{ms}:UInt64}}) AS over_{ms}ms"
        for ms in THRESHOLDS_MS
    )
    + " FROM __DATABASE__.otel_traces"
    " WHERE __NARROW__ AND SpanKind = 'Server' AND __NOT_HEALTH_CHECK__"
    " GROUP BY wk, t"
    " LIMIT {max_rows:UInt16}"
)

COUNT_COLUMNS: tuple[str, ...] = (
    "requests",
    "errors",
    "timed",
    *(f"over_{ms}ms" for ms in THRESHOLDS_MS),
)


def align(moment: datetime) -> datetime:
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - epoch % BUCKET_SECONDS, tz=UTC)


def statement(database: str) -> str:
    return _sql(_STATEMENT, database)


def parameters(
    bindings: list[dict[str, Any]], start: datetime, end: datetime
) -> tuple[dict[str, str], dict[str, str]]:
    """(query parameters, binding id -> workload key). Bindings the store
    cannot name are left out."""
    keys: dict[str, str] = {}
    named: list[dict[str, Any]] = []
    for binding in bindings:
        try:
            keys[str(binding["id"])] = _key(binding)
        except (ScopeError, KeyError):
            continue
        named.append(binding)
    split = [k.split("|") for k in keys.values()]
    values = {
        "start": clickhouse_time(start),
        "end": clickhouse_time(end),
        "clusters": _array(sorted({s[0] for s in split})),
        "namespaces": _array(sorted({s[1] for s in split})),
        "workloads": _array(sorted(set(keys.values()))),
        "health_checks": health_check_parameter(named),
        "long_lived_ns": str(LONG_LIVED_SECONDS * 1_000_000_000),
        "max_rows": str(MAX_ROWS),
        **{f"over_{ms}": str(ms * 1_000_000) for ms in THRESHOLDS_MS},
    }
    return values, keys


def buckets_between(start: datetime, end: datetime) -> list[datetime]:
    out = []
    moment = align(start)
    while moment < end:
        out.append(moment)
        moment += timedelta(seconds=BUCKET_SECONDS)
    return out


def rows_to_counts(
    keys: dict[str, str], rows: list[dict[str, Any]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Every (binding, bucket) of the read, zero where the store had nothing."""
    by: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        by[(str(row.get("wk")), int(_count(row.get("t"))))] = row
    out: list[dict[str, Any]] = []
    for binding_id, wk in keys.items():
        for bucket in buckets_between(start, end):
            hit = by.get((wk, int(bucket.timestamp())))
            counts = {c: int(_count(hit.get(c))) if hit else 0 for c in COUNT_COLUMNS}
            # The store's counts are one statement, so they agree; the
            # clamp only keeps a malformed row from breaking the constraint.
            counts["errors"] = min(counts["errors"], counts["requests"])
            counts["timed"] = min(counts["timed"], counts["requests"])
            out.append({"binding_id": uuid.UUID(binding_id), "bucket_start": bucket, **counts})
    return out


# A fixed column list; values are bound parameters.
_UPSERT = (
    "INSERT INTO sli_rollups (binding_id, bucket_start, "  # noqa: S608
    + ", ".join(COUNT_COLUMNS)
    + ", written_at) VALUES (:binding_id, :bucket_start, "
    + ", ".join(f":{c}" for c in COUNT_COLUMNS)
    + ", now()) ON CONFLICT (binding_id, bucket_start) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in COUNT_COLUMNS)
    + ", written_at = now()"
)


@dataclass
class RollupReport:
    lease_acquired: bool = False
    bindings: int = 0
    reads: int = 0
    failed_reads: int = 0
    written: int = 0


async def _active_bindings(connection: AsyncConnection) -> list[uuid.UUID]:
    rows = (
        await connection.execute(
            text(
                "SELECT id FROM service_workload_bindings "
                "WHERE lifecycle = 'active' AND resolved_resource_uid IS NOT NULL ORDER BY id"
            )
        )
    ).all()
    return [uuid.UUID(str(r[0])) for r in rows]


async def _start(connection: AsyncConnection, ids: list[uuid.UUID], end: datetime) -> datetime:
    """From where this run reads: the oldest of each binding's next bucket
    (less a few for late spans), never further back than the store holds."""
    floor = align(end - BACKFILL)
    rows = (
        await connection.execute(
            text(
                "SELECT binding_id, max(bucket_start) FROM sli_rollups "
                "WHERE binding_id = ANY(:ids) GROUP BY binding_id"
            ),
            {"ids": ids},
        )
    ).all()
    last = {uuid.UUID(str(r[0])): r[1] for r in rows}
    reread = timedelta(seconds=BUCKET_SECONDS * REREAD_BUCKETS)
    starts = [
        max(floor, last[i] - reread + timedelta(seconds=BUCKET_SECONDS)) if i in last else floor
        for i in ids
    ]
    return min(starts) if starts else end


async def run_once(
    engine: AsyncEngine,
    store: ClickHouseStore,
    redis: aioredis.Redis,
    *,
    database: str,
    now: datetime | None = None,
    lease_seconds: int = 300,
) -> RollupReport:
    report = RollupReport()
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(LEASE_KEY, token, nx=True, ex=lease_seconds)
    except Exception:
        logger.warning("sli rollups: lease unavailable, skipping")
        return report
    if not acquired:
        return report
    report.lease_acquired = True
    try:
        end = align((now or datetime.now(UTC)) - SETTLE)
        async with engine.connect() as connection:
            ids = await _active_bindings(connection)
            bindings = await bindings_on(connection, ids) if ids else []
            start = await _start(connection, [uuid.UUID(b["id"]) for b in bindings], end)
        report.bindings = len(bindings)
        if not bindings or start >= end:
            return report
        per_read = max(1, min(MAX_BUCKETS_PER_READ, (MAX_ROWS - 10) // max(1, len(bindings))))
        step = timedelta(seconds=BUCKET_SECONDS * per_read)
        sql = statement(database)
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(end, chunk_start + step)
            values, keys = parameters(bindings, chunk_start, chunk_end)
            report.reads += 1
            try:
                result = await store.query(
                    sql,
                    values,
                    max_rows=MAX_ROWS,
                    timeout_seconds=20.0,
                    correlation_id="sli-rollups",
                )
                rows = result.rows if isinstance(result.rows, list) else None
            except Exception:
                rows = None
            if rows is None:
                # Not observed: nothing is written, so the gap stays a gap.
                report.failed_reads += 1
                logger.warning("sli rollups: a store read failed; the buckets stay unobserved")
            else:
                counts = rows_to_counts(keys, rows, chunk_start, chunk_end)
                if counts:
                    async with engine.begin() as connection:
                        await connection.execute(text(_UPSERT), counts)
                    report.written += len(counts)
            chunk_start = chunk_end
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM sli_rollups WHERE bucket_start < :cut"),
                {"cut": end - RETENTION},
            )
    finally:
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, LEASE_KEY, token)
    return report


@dataclass(frozen=True)
class Window:
    requests: int
    errors: int
    timed: int
    over: dict[int, int]
    observed_buckets: int
    expected_buckets: int
    busy_buckets: int
    newest: datetime | None


async def read_window(
    connection: AsyncConnection,
    binding_ids: list[uuid.UUID],
    start: datetime,
    end: datetime,
) -> Window:
    """Sums over [start, end) for these bindings."""
    over_columns = ", ".join(f"coalesce(sum(over_{ms}ms), 0)" for ms in THRESHOLDS_MS)
    row = (
        await connection.execute(
            text(
                "SELECT coalesce(sum(requests), 0), coalesce(sum(errors), 0), "  # noqa: S608 - fixed column list
                f"coalesce(sum(timed), 0), {over_columns}, count(*), "
                "count(*) FILTER (WHERE requests > 0), max(bucket_start) "
                "FROM sli_rollups WHERE binding_id = ANY(:ids) "
                "AND bucket_start >= :start AND bucket_start < :end"
            ),
            {"ids": binding_ids, "start": start, "end": end},
        )
    ).one()
    n = len(THRESHOLDS_MS)
    over = {ms: int(row[3 + i]) for i, ms in enumerate(THRESHOLDS_MS)}
    expected = len(buckets_between(start, end)) * len(binding_ids)
    newest = row[5 + n]
    return Window(
        requests=int(row[0]),
        errors=int(row[1]),
        timed=int(row[2]),
        over=over,
        observed_buckets=int(row[3 + n]),
        expected_buckets=expected,
        busy_buckets=int(row[4 + n]),
        newest=(newest + timedelta(seconds=BUCKET_SECONDS)) if newest is not None else None,
    )


class SliRollupWriter:
    """Lifespan-owned loop, started with the SLO evaluator."""

    def __init__(
        self,
        engine: AsyncEngine,
        store: ClickHouseStore,
        redis: aioredis.Redis,
        *,
        database: str,
        interval_seconds: float = 120.0,
    ) -> None:
        self._engine = engine
        self._store = store
        self._redis = redis
        self._database = database
        self._interval = max(60.0, interval_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="sli-rollups")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await run_once(self._engine, self._store, self._redis, database=self._database)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("sli rollups: cycle failed")
            await asyncio.sleep(self._interval)
