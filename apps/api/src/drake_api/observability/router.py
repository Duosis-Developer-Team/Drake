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

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.observability.logs import (
    DEFAULT_LIMIT,
    DEFAULT_LOG_RANGE,
    LogQueryError,
    build_log_query,
    next_cursor,
    normalize_rows,
)
from drake_api.observability.scope import DEFAULT_RANGE, RANGES, WORKLOAD_ATTRIBUTE
from drake_api.observability.signals import (
    MAX_BINDINGS,
    build_signal_query,
    summarize_logs,
    summarize_traces,
)
from drake_api.observability.store import ClickHouseStore, StoreContractError, StoreUnavailableError
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
    async with get_engine(settings).connect() as connection:
        log_bindings = await binding_repo.get_bindings(
            connection, auth.principal, ids, permission=LOGS_PERMISSION
        )
        trace_bindings = await binding_repo.get_bindings(
            connection, auth.principal, ids, permission=TRACES_PERMISSION
        )

    # Kinds the store cannot name are left out rather than matched on a
    # guess (the schema allows only these today; this keeps it explicit).
    log_bindings = [b for b in log_bindings if b["workload_kind"] in WORKLOAD_ATTRIBUTE]
    trace_bindings = [b for b in trace_bindings if b["workload_kind"] in WORKLOAD_ATTRIBUTE]
    now = datetime.now(UTC)

    store = _store(request)
    (logs_section, logs), (traces_section, traces) = await asyncio.gather(
        _signal_section(
            store,
            enabled=settings.observability_logs_enabled,
            kind="logs",
            bindings=log_bindings,
            range_key=range_key,
            now=now,
        ),
        _signal_section(
            store,
            enabled=settings.observability_traces_enabled,
            kind="traces",
            bindings=trace_bindings,
            range_key=range_key,
            now=now,
        ),
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
