"""Log endpoints (ADR-0029).

One read, attached to the thing access is already granted on: a
service-health binding. The binding says which cluster, namespace and
workload a service runs as, so it is also the only honest answer to "whose
logs are these". A caller supplies a binding id and a few bounded choices;
every label the query matches on comes from the row behind that id.

Visibility is `logs.view` at the binding's service scope, decided by the
same scope-filtered query the rest of service health uses. Anything the
caller may not read is a 404, indistinguishable from absent.

The response always says which of three situations it is in, because they
read alike and mean opposite things (ADR-0011):

    not_configured  Drake has no log store; nothing was queried
    unavailable     the store could not be read just now
    ok              the store answered; `entries` may be empty, and empty
                    means "nothing matched in this window", nothing more
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.observability.health_checks import with_http_probes
from drake_api.observability.logs import (
    DEFAULT_LIMIT,
    DEFAULT_LOG_RANGE,
    LogQueryError,
    build_log_query,
    next_cursor,
    normalize_rows,
)
from drake_api.observability.patterns import build_pattern_query, normalize_patterns
from drake_api.observability.scope import DEFAULT_RANGE, RANGES, WORKLOAD_ATTRIBUTE
from drake_api.observability.signals import (
    MAX_BINDINGS,
    build_signal_query,
    summarize_logs,
    summarize_traces,
)
from drake_api.observability.signals_cache import cached_section
from drake_api.observability.store import ClickHouseStore, StoreContractError, StoreUnavailableError
from drake_api.observability.timeline import (
    TimelineQueryError,
    bucket_frame,
    build_timeline_query,
)
from drake_api.observability.timeline import (
    normalize_logs as normalize_timeline_logs,
)
from drake_api.observability.timeline import (
    normalize_traces as normalize_timeline_traces,
)
from drake_api.observability.traces import (
    DEFAULT_LIMIT as DEFAULT_TRACE_LIMIT,
)
from drake_api.observability.traces import (
    MAX_LIMIT as MAX_TRACE_LIMIT,
)
from drake_api.observability.traces import (
    Statement,
    TraceQueryError,
    build_trace_detail,
    build_trace_list,
    normalize_requests,
    normalize_routes,
    normalize_spans,
    normalize_totals,
)
from drake_api.service_health import bindings as binding_repo
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.observability")

router = APIRouter(prefix="/v1", tags=["observability"])

LOGS_PERMISSION = "logs.view"
TRACES_PERMISSION = "traces.view"
_NOT_FOUND = "not found"


def _store(request: Request) -> ClickHouseStore:
    store: ClickHouseStore = request.app.state.observability_store
    return store


@router.get("/service-health/bindings/{binding_id}/logs")
async def binding_logs(
    request: Request,
    binding_id: uuid.UUID,
    range_key: str = Query(DEFAULT_LOG_RANGE, alias="range", max_length=8),
    severity: str = Query("any", max_length=8),
    q: str = Query("", max_length=100),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=200),
    before: datetime | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        binding = await binding_repo.get_binding(
            connection, auth.principal, binding_id, permission=LOGS_PERMISSION
        )
    if binding is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "binding_id": str(binding_id),
        "range": range_key,
        "as_of": now.isoformat(),
        "entries": [],
        "next_before": None,
    }
    store = _store(request)
    connector = store.connector
    if not settings.observability_logs_enabled or connector is None:
        return {**base, "state": "not_configured"}

    try:
        query = build_log_query(
            database=connector.database,
            binding=binding,
            range_key=range_key,
            severity=severity,
            term=q,
            limit=limit,
            before=before,
            window_start=start,
            window_end=end,
            now=now,
        )
    except LogQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        result = await store.query(
            query.statement,
            query.parameters,
            max_rows=query.limit,
            timeout_seconds=connector.timeout_seconds,
            correlation_id=correlation_id_var.get() or "",
        )
        entries = normalize_rows(result.rows)
    except StoreUnavailableError as error:
        logger.warning("log store unavailable", extra={"code": error.code})
        return {**base, "state": "unavailable", "reason": error.code}
    except (StoreContractError, ValueError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        logger.warning("log store contract failure", extra={"code": code})
        return {**base, "state": "unavailable", "reason": code}

    return {
        **base,
        "state": "ok",
        "window": {"start": query.start.isoformat(), "end": query.end.isoformat()},
        "entries": entries,
        "next_before": next_cursor(entries, query.limit),
    }


async def _visible_binding(
    request: Request, auth: AuthContext, binding_id: uuid.UUID, permission: str
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        binding = await binding_repo.get_binding(
            connection, auth.principal, binding_id, permission=permission
        )
        if binding is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        [binding] = await with_http_probes(connection, [binding])
    return binding


async def _rows(
    store: ClickHouseStore, statement: Statement, timeout: float
) -> list[dict[str, Any]]:
    result = await store.query(
        statement.sql,
        statement.parameters,
        max_rows=statement.max_rows,
        timeout_seconds=timeout,
        correlation_id=correlation_id_var.get() or "",
    )
    return result.rows


@router.get("/service-health/bindings/{binding_id}/traces")
async def binding_traces(
    request: Request,
    binding_id: uuid.UUID,
    range_key: str = Query(DEFAULT_LOG_RANGE, alias="range", max_length=8),
    status: str = Query("any", max_length=8),
    sort: str = Query("recent", max_length=8),
    limit: int = Query(DEFAULT_TRACE_LIMIT, ge=1, le=MAX_TRACE_LIMIT),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Routes, totals and one page of requests for a bound service.

    Same three states as logs — not_configured, unavailable, ok — and the
    same rule that an empty answer is "nothing in this window", not health.
    """
    settings: Settings = request.app.state.settings
    binding = await _visible_binding(request, auth, binding_id, TRACES_PERMISSION)
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "binding_id": str(binding_id),
        "range": range_key,
        "as_of": now.isoformat(),
        "totals": None,
        "routes": [],
        "requests": [],
    }
    store = _store(request)
    connector = store.connector
    if not settings.observability_traces_enabled or connector is None:
        return {**base, "state": "not_configured"}

    try:
        query = build_trace_list(
            database=connector.database,
            binding=binding,
            range_key=range_key,
            status=status,
            sort=sort,
            limit=limit,
            now=now,
            window_start=start,
            window_end=end,
        )
    except TraceQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    timeout = connector.timeout_seconds
    try:
        totals = normalize_totals(await _rows(store, query.totals, timeout))
        routes = normalize_routes(await _rows(store, query.routes, timeout))
        requests = normalize_requests(await _rows(store, query.requests, timeout))
    except StoreUnavailableError as error:
        logger.warning("trace store unavailable", extra={"code": error.code})
        return {**base, "state": "unavailable", "reason": error.code}
    except (StoreContractError, ValueError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        logger.warning("trace store contract failure", extra={"code": code})
        return {**base, "state": "unavailable", "reason": code}

    return {
        **base,
        "state": "ok",
        "window": {"start": query.start.isoformat(), "end": query.end.isoformat()},
        "totals": totals,
        "routes": routes,
        "requests": requests,
    }


@router.get("/service-health/bindings/{binding_id}/traces/{trace_id}")
async def binding_trace(
    request: Request,
    binding_id: uuid.UUID,
    trace_id: str,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The spans of one trace that belong to this service.

    A trace id outside the service's scope, or one that no longer exists
    (spans expire with the store's TTL), is a 404 — the same answer, so a
    trace id cannot be used to probe other services.
    """
    settings: Settings = request.app.state.settings
    binding = await _visible_binding(request, auth, binding_id, TRACES_PERMISSION)
    store = _store(request)
    connector = store.connector
    if not settings.observability_traces_enabled or connector is None:
        return {
            "binding_id": str(binding_id),
            "trace_id": trace_id,
            "state": "not_configured",
            "spans": [],
        }
    try:
        statement = build_trace_detail(
            database=connector.database, binding=binding, trace_id=trace_id
        )
    except TraceQueryError as error:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from error
    try:
        spans = normalize_spans(await _rows(store, statement, connector.timeout_seconds))
    except StoreUnavailableError as error:
        logger.warning("trace store unavailable", extra={"code": error.code})
        return {
            "binding_id": str(binding_id),
            "trace_id": trace_id,
            "state": "unavailable",
            "reason": error.code,
            "spans": [],
        }
    except (StoreContractError, ValueError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        logger.warning("trace store contract failure", extra={"code": code})
        return {
            "binding_id": str(binding_id),
            "trace_id": trace_id,
            "state": "unavailable",
            "reason": code,
            "spans": [],
        }
    if not spans:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {
        "binding_id": str(binding_id),
        "trace_id": statement.parameters["trace_id"],
        "state": "ok",
        "spans": spans,
    }


async def _signal_section(
    store: ClickHouseStore,
    *,
    enabled: bool,
    kind: str,
    bindings: list[dict[str, Any]],
    range_key: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """One signal (logs or traces) for every binding the caller may read it
    for: the section's state, and the per-binding numbers when `ok`."""
    connector = store.connector
    if not enabled or connector is None:
        return {"state": "not_configured"}, {}
    if not bindings:
        # Nothing this caller may read for this signal: not a store answer.
        return {"state": "ok"}, {}
    query = build_signal_query(
        database=connector.database, bindings=bindings, range_key=range_key, now=now
    )
    statement = query.logs if kind == "logs" else query.traces
    try:
        result = await store.query(
            statement,
            query.parameters,
            max_rows=query.max_rows,
            timeout_seconds=connector.timeout_seconds,
            correlation_id=correlation_id_var.get() or "",
        )
        summary = (
            summarize_logs(result.rows, bindings)
            if kind == "logs"
            else summarize_traces(result.rows, bindings)
        )
    except StoreUnavailableError as error:
        logger.warning("signal store unavailable", extra={"code": error.code, "signal": kind})
        return {"state": "unavailable", "reason": error.code}, {}
    except (StoreContractError, ValueError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        logger.warning("signal store contract failure", extra={"code": code, "signal": kind})
        return {"state": "unavailable", "reason": code}, {}
    return {"state": "ok"}, summary


@router.get("/service-health/signals")
async def service_signals(
    request: Request,
    binding_id: list[uuid.UUID] = Query(...),
    range_key: str = Query(DEFAULT_RANGE, alias="range", max_length=8),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Error and request counts for many bound services in one read.

    For each requested binding: its log lines, error and warning lines
    (when the caller has `logs.view` there) and its requests, failed
    requests and p95 (when the caller has `traces.view` there). A binding
    the caller may read neither for is absent, as a 404 would be. Each
    signal carries its own state, because "no store" and "store down" must
    not read as "no errors".
    """
    settings: Settings = request.app.state.settings
    if range_key not in RANGES:
        raise HTTPException(status_code=422, detail="unsupported range")
    ids = list(dict.fromkeys(binding_id))
    if len(ids) > MAX_BINDINGS:
        raise HTTPException(status_code=422, detail="too many bindings")
    log_bindings, trace_bindings = await _signal_bindings(settings, auth.principal, ids)
    (logs_section, logs), (traces_section, traces), now = await _signal_sections(
        settings,
        _store(request),
        request.app.state.telemetry_redis,
        log_bindings,
        trace_bindings,
        range_key,
    )

    visible = {b["id"] for b in log_bindings} | {b["id"] for b in trace_bindings}
    items = [
        {
            "binding_id": str(i),
            "logs": logs.get(str(i)),
            "traces": traces.get(str(i)),
        }
        for i in ids
        if str(i) in visible
    ]
    return {
        "range": range_key,
        "as_of": now.isoformat(),
        "window": {
            "start": (now - timedelta(seconds=RANGES[range_key])).isoformat(),
            "end": now.isoformat(),
        },
        "logs": logs_section,
        "traces": traces_section,
        "items": items,
    }


async def _signal_bindings(
    settings: Settings, principal: Any, ids: list[uuid.UUID]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The bindings of *ids* the caller may read logs and traces for."""
    async with get_engine(settings).connect() as connection:
        log_bindings = await binding_repo.get_bindings(
            connection, principal, ids, permission=LOGS_PERMISSION
        )
        trace_bindings = await binding_repo.get_bindings(
            connection, principal, ids, permission=TRACES_PERMISSION
        )
        trace_bindings = await with_http_probes(connection, trace_bindings)
    # Kinds the store cannot name are left out rather than matched on a
    # guess (the schema allows only these today; this keeps it explicit).
    return (
        [b for b in log_bindings if b["workload_kind"] in WORKLOAD_ATTRIBUTE],
        [b for b in trace_bindings if b["workload_kind"] in WORKLOAD_ATTRIBUTE],
    )


async def _signal_sections(
    settings: Settings,
    store: ClickHouseStore,
    redis: Any,
    log_bindings: list[dict[str, Any]],
    trace_bindings: list[dict[str, Any]],
    range_key: str,
    *,
    refresh: bool = False,
) -> tuple[
    tuple[dict[str, Any], dict[str, dict[str, Any]]],
    tuple[dict[str, Any], dict[str, dict[str, Any]]],
    datetime,
]:
    """Both signals, each from its short cache (signals_cache) or the
    store. The answer is as of the older of the two reads."""

    def compute(kind: str, enabled: bool, bindings: list[dict[str, Any]]) -> Any:
        return lambda: _signal_section(
            store,
            enabled=enabled,
            kind=kind,
            bindings=bindings,
            range_key=range_key,
            now=datetime.now(UTC),
        )

    (logs_section, logs, logs_at), (traces_section, traces, traces_at) = await asyncio.gather(
        cached_section(
            redis,
            compute("logs", settings.observability_logs_enabled, log_bindings),
            kind="logs",
            range_key=range_key,
            bindings=log_bindings,
            refresh=refresh,
        ),
        cached_section(
            redis,
            compute("traces", settings.observability_traces_enabled, trace_bindings),
            kind="traces",
            range_key=range_key,
            bindings=trace_bindings,
            refresh=refresh,
        ),
    )
    as_of = datetime.fromtimestamp(min(logs_at, traces_at), tz=UTC)
    return (logs_section, logs), (traces_section, traces), as_of


async def warm_signals(
    settings: Settings, store: ClickHouseStore, redis: Any, principal: Any
) -> int:
    """Rebuild the default-range signals for every active binding, so the
    first overview, log or trace screen of the day is answered from cache.
    Returns 1 when it ran."""
    async with get_engine(settings).connect() as connection:
        ids = [
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT id FROM service_workload_bindings "
                        "WHERE lifecycle = 'active' ORDER BY id LIMIT :n"
                    ),
                    {"n": MAX_BINDINGS},
                )
            ).all()
        ]
    if not ids:
        return 0
    log_bindings, trace_bindings = await _signal_bindings(settings, principal, ids)
    await _signal_sections(
        settings, store, redis, log_bindings, trace_bindings, DEFAULT_RANGE, refresh=True
    )
    return 1


async def _timeline_lane(
    store: ClickHouseStore,
    *,
    enabled: bool,
    allowed: bool,
    statement: str,
    parameters: dict[str, str],
    max_rows: int,
    normalize: Any,
    kind: str,
) -> dict[str, Any]:
    """One lane of the timeline (requests or log lines) with its own state."""
    connector = store.connector
    if not enabled or connector is None:
        return {"state": "not_configured", "buckets": []}
    if not allowed:
        return {"state": "denied", "buckets": []}
    try:
        result = await store.query(
            statement,
            parameters,
            max_rows=max_rows,
            timeout_seconds=connector.timeout_seconds,
            correlation_id=correlation_id_var.get() or "",
        )
        return {"state": "ok", "buckets": normalize(result.rows)}
    except StoreUnavailableError as error:
        logger.warning("timeline store unavailable", extra={"code": error.code, "lane": kind})
        return {"state": "unavailable", "reason": error.code, "buckets": []}
    except (StoreContractError, ValueError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        logger.warning("timeline store contract failure", extra={"code": code, "lane": kind})
        return {"state": "unavailable", "reason": code, "buckets": []}


@router.get("/service-health/bindings/{binding_id}/timeline")
async def binding_timeline(
    request: Request,
    binding_id: uuid.UUID,
    range_key: str = Query(DEFAULT_RANGE, alias="range", max_length=8),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Requests, failures, p95 and error/warning log lines per time bucket.

    The requests lane needs `traces.view`, the logs lane `logs.view`, each
    at the binding's scope; a caller with neither gets a 404, as for every
    other read here. A lane the caller may not read says `denied` rather
    than showing an empty line.
    """
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        traces_binding = await binding_repo.get_binding(
            connection, auth.principal, binding_id, permission=TRACES_PERMISSION
        )
        logs_binding = await binding_repo.get_binding(
            connection, auth.principal, binding_id, permission=LOGS_PERMISSION
        )
        if traces_binding is not None:
            [traces_binding] = await with_http_probes(connection, [traces_binding])
    binding = traces_binding or logs_binding
    if binding is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    now = datetime.now(UTC)
    store = _store(request)
    connector = store.connector
    try:
        query = build_timeline_query(
            database=connector.database if connector else "drake",
            binding=binding,
            range_key=range_key,
            now=now,
        )
    except TimelineQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    requests_lane, logs_lane = await asyncio.gather(
        _timeline_lane(
            store,
            enabled=settings.observability_traces_enabled,
            allowed=traces_binding is not None,
            statement=query.traces,
            parameters=query.parameters,
            max_rows=query.max_rows,
            normalize=lambda rows: normalize_timeline_traces(rows, query),
            kind="traces",
        ),
        _timeline_lane(
            store,
            enabled=settings.observability_logs_enabled,
            allowed=logs_binding is not None,
            statement=query.logs,
            parameters=query.parameters,
            max_rows=query.max_rows,
            normalize=lambda rows: normalize_timeline_logs(rows, query),
            kind="logs",
        ),
    )
    return {
        "binding_id": str(binding_id),
        "range": range_key,
        "as_of": now.isoformat(),
        "bucket_seconds": query.bucket_seconds,
        "window": {"start": query.start.isoformat(), "end": query.end.isoformat()},
        "buckets": bucket_frame(query, now),
        "requests": requests_lane,
        "logs": logs_lane,
    }


@router.get("/service-health/bindings/{binding_id}/logs/patterns")
async def binding_log_patterns(
    request: Request,
    binding_id: uuid.UUID,
    range_key: str = Query(DEFAULT_LOG_RANGE, alias="range", max_length=8),
    severity: str = Query("any", max_length=8),
    q: str = Query("", max_length=100),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The binding's log lines in the window, grouped by their shape.

    Same visibility (`logs.view`), window, severity floor and term as the
    log read, and the same three states; the grouping covers every matching
    line in the window, not one page of them.
    """
    settings: Settings = request.app.state.settings
    binding = await _visible_binding(request, auth, binding_id, LOGS_PERMISSION)
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "binding_id": str(binding_id),
        "range": range_key,
        "as_of": now.isoformat(),
        "patterns": [],
    }
    store = _store(request)
    connector = store.connector
    if not settings.observability_logs_enabled or connector is None:
        return {**base, "state": "not_configured"}
    try:
        query = build_pattern_query(
            database=connector.database,
            binding=binding,
            range_key=range_key,
            severity=severity,
            term=q,
            now=now,
            window_start=start,
            window_end=end,
        )
    except LogQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        result = await store.query(
            query.statement,
            query.parameters,
            max_rows=query.max_rows,
            timeout_seconds=connector.timeout_seconds,
            correlation_id=correlation_id_var.get() or "",
        )
        patterns = normalize_patterns(result.rows)
    except StoreUnavailableError as error:
        logger.warning("pattern store unavailable", extra={"code": error.code})
        return {**base, "state": "unavailable", "reason": error.code}
    except (StoreContractError, ValueError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        logger.warning("pattern store contract failure", extra={"code": code})
        return {**base, "state": "unavailable", "reason": code}
    return {
        **base,
        "state": "ok",
        "window": {"start": query.start.isoformat(), "end": query.end.isoformat()},
        "patterns": patterns,
    }
