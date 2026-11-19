"""The environment overview read (ADR-0033).

`GET /v1/service-health/overview?binding_id=…&range=…` answers, for every
requested binding the caller may read, the numbers a custom dashboard
draws: traffic per bucket, the latency histogram, status codes, routes,
calls between the services, database operations and the log make-up, and
what needs attention, found from those numbers (observability.findings).

Visibility is the signal read's (observability.router): trace sections need
`traces.view` and log sections `logs.view`, each at the binding's scope. A
binding the caller may read neither for is absent, as a 404 would be.
Every section carries its own state, because "no store", "store down" and
"nothing happened" must not read alike (ADR-0011).
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.observability.findings import find
from drake_api.observability.health_checks import with_http_probes
from drake_api.observability.overview import (
    LOG_STATEMENTS,
    MAX_BINDINGS,
    TRACE_STATEMENTS,
    OverviewQuery,
    OverviewQueryError,
    build_overview_query,
    normalize_calls,
    normalize_codes,
    normalize_databases,
    normalize_latency,
    normalize_log_lines,
    normalize_log_patterns,
    normalize_routes,
    normalize_traffic,
    window_of,
)
from drake_api.observability.scope import DEFAULT_RANGE, RANGES, WORKLOAD_ATTRIBUTE
from drake_api.observability.store import ClickHouseStore, StoreContractError, StoreUnavailableError
from drake_api.service_health import bindings as binding_repo
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.observability.overview")

router = APIRouter(prefix="/v1", tags=["observability"])

LOGS_PERMISSION = "logs.view"
TRACES_PERMISSION = "traces.view"

_NORMALIZE: dict[str, Callable[[list[dict[str, Any]], OverviewQuery], Any]] = {
    "traffic": normalize_traffic,
    "latency": normalize_latency,
    "codes": normalize_codes,
    "routes": normalize_routes,
    "calls": normalize_calls,
    "databases": normalize_databases,
    "log_lines": normalize_log_lines,
    "log_patterns": normalize_log_patterns,
}


async def _section(
    store: ClickHouseStore,
    *,
    name: str,
    enabled: bool,
    query: OverviewQuery | None,
) -> dict[str, Any]:
    """One statement's answer with its own state."""
    connector = store.connector
    if not enabled or connector is None:
        return {"state": "not_configured"}
    if query is None:
        # Nothing this caller may read for this signal: not a store answer.
        return {"state": "ok", "data": None}
    statement, max_rows = query.statements[name]
    try:
        result = await store.query(
            statement,
            query.parameters,
            max_rows=max_rows,
            timeout_seconds=connector.timeout_seconds,
            correlation_id=correlation_id_var.get() or "",
        )
        return {"state": "ok", "data": _NORMALIZE[name](result.rows, query)}
    except StoreUnavailableError as error:
        logger.warning("overview store unavailable", extra={"code": error.code, "section": name})
        return {"state": "unavailable", "reason": error.code}
    except (StoreContractError, ValueError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        logger.warning("overview store contract failure", extra={"code": code, "section": name})
        return {"state": "unavailable", "reason": code}


@router.get("/service-health/overview")
async def service_overview(
    request: Request,
    binding_id: list[uuid.UUID] = Query(...),
    range_key: str = Query(DEFAULT_RANGE, alias="range", max_length=8),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
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
        trace_bindings = await with_http_probes(connection, trace_bindings)

    log_bindings = [b for b in log_bindings if b["workload_kind"] in WORKLOAD_ATTRIBUTE]
    trace_bindings = [b for b in trace_bindings if b["workload_kind"] in WORKLOAD_ATTRIBUTE]
    now = datetime.now(UTC)
    store = _store(request)
    database = store.connector.database if store.connector else "drake"

    try:
        trace_query = (
            build_overview_query(
                database=database, bindings=trace_bindings, range_key=range_key, now=now
            )
            if trace_bindings
            else None
        )
        log_query = (
            build_overview_query(
                database=database, bindings=log_bindings, range_key=range_key, now=now
            )
            if log_bindings
            else None
        )
    except OverviewQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    names = [*TRACE_STATEMENTS, *LOG_STATEMENTS]
    answers = await asyncio.gather(
        *(
            _section(
                store,
                name=name,
                enabled=(
                    settings.observability_traces_enabled
                    if name in TRACE_STATEMENTS
                    else settings.observability_logs_enabled
                ),
                query=trace_query if name in TRACE_STATEMENTS else log_query,
            )
            for name in names
        )
    )
    sections = dict(zip(names, answers, strict=True))
    frame = trace_query or log_query
    visible = {b["id"] for b in log_bindings} | {b["id"] for b in trace_bindings}
    return {
        "range": range_key,
        "as_of": now.isoformat(),
        "bucket_seconds": frame.bucket_seconds if frame else None,
        "buckets": frame.bucket_starts if frame else [],
        "window": window_of(frame)
        if frame
        else {
            "start": (now - timedelta(seconds=RANGES[range_key])).isoformat(),
            "end": now.isoformat(),
        },
        "bindings": [
            {
                "binding_id": str(i),
                "traces": any(b["id"] == str(i) for b in trace_bindings),
                "logs": any(b["id"] == str(i) for b in log_bindings),
            }
            for i in ids
            if str(i) in visible
        ],
        **sections,
        # Found from the sections above; no further read (observability.findings).
        "findings": find(sections),
    }


def _store(request: Request) -> ClickHouseStore:
    store: ClickHouseStore = request.app.state.observability_store
    return store
