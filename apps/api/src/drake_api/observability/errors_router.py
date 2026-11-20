"""`GET /v1/service-health/errors`: error tracking for many services
(ADR-0036).

Scope works exactly as for the overview read (observability.overview_router):
the caller names binding ids; only those the caller may read traces of
take part in the exception and failed-request statements, and only those
the caller may read logs of take part in the error-log statement.

At most three statements run per read. The answer is kept in Redis for a
minute, so a screen that is refreshed, or opened by several people, does
not read the store again.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.observability import errors
from drake_api.observability.health_checks import with_http_probes
from drake_api.observability.overview import (
    MAX_BINDINGS,
    OverviewQuery,
    OverviewQueryError,
    build_overview_query,
)
from drake_api.observability.scope import DEFAULT_RANGE, RANGES, WORKLOAD_ATTRIBUTE
from drake_api.observability.store import ClickHouseStore, StoreContractError, StoreUnavailableError
from drake_api.service_health import bindings as binding_repo
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.observability.errors")

router = APIRouter(prefix="/v1", tags=["observability"])

LOGS_PERMISSION = "logs.view"
TRACES_PERMISSION = "traces.view"
CACHE_SECONDS = 60


async def _issues(
    store: ClickHouseStore,
    *,
    kind: str,
    enabled: bool,
    query: OverviewQuery | None,
) -> dict[str, Any]:
    connector = store.connector
    if not enabled or connector is None:
        return {"state": "not_configured"}
    if query is None:
        return {"state": "ok", "data": []}
    statement, max_rows = errors.statements(connector.database)[kind]
    try:
        result = await store.query(
            statement,
            errors.parameters(query),
            max_rows=max_rows,
            timeout_seconds=connector.timeout_seconds,
            correlation_id=correlation_id_var.get() or "",
        )
        return {"state": "ok", "data": errors.normalize(kind, result.rows, query)}
    except StoreUnavailableError as error:
        return {"state": "unavailable", "reason": error.code}
    except (StoreContractError, ValueError, TypeError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        logger.warning("errors store contract failure", extra={"code": code, "kind": kind})
        return {"state": "unavailable", "reason": code}


def _cache_key(range_key: str, trace_ids: list[str], log_ids: list[str]) -> str:
    identity = json.dumps(
        {"range": range_key, "traces": sorted(trace_ids), "logs": sorted(log_ids)}, sort_keys=True
    )
    return "errors:v1:" + hashlib.sha256(identity.encode()).hexdigest()


@router.get("/service-health/errors")
async def service_errors(
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

    redis = request.app.state.telemetry_redis
    key = _cache_key(
        range_key, [str(b["id"]) for b in trace_bindings], [str(b["id"]) for b in log_bindings]
    )
    with contextlib.suppress(Exception):
        raw = await redis.get(key)
        if raw:
            cached = json.loads(raw)
            if isinstance(cached, dict):
                return cached

    store: ClickHouseStore = request.app.state.observability_store
    now = datetime.now(UTC)
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

    exceptions, failed, logs = await asyncio.gather(
        _issues(
            store,
            kind="exception",
            enabled=settings.observability_traces_enabled,
            query=trace_query,
        ),
        _issues(
            store,
            kind="failed_request",
            enabled=settings.observability_traces_enabled,
            query=trace_query,
        ),
        _issues(
            store, kind="error_log", enabled=settings.observability_logs_enabled, query=log_query
        ),
    )
    sections = {"exception": exceptions, "failed_request": failed, "error_log": logs}
    issues = sorted(
        (issue for section in sections.values() for issue in section.get("data") or []),
        key=lambda issue: (issue["last_seen"], issue["count"]),
        reverse=True,
    )
    frame = trace_query or log_query
    body = {
        "range": range_key,
        "as_of": now.isoformat(),
        "bucket_seconds": frame.bucket_seconds if frame else None,
        "buckets": frame.bucket_starts if frame else [],
        "sections": {
            k: {kk: vv for kk, vv in v.items() if kk != "data"} for k, v in sections.items()
        },
        "issues": issues,
        "summary": errors.summarize(issues),
    }
    if any(v.get("state") == "ok" for v in sections.values()):
        with contextlib.suppress(Exception):
            await redis.set(key, json.dumps(body), ex=CACHE_SECONDS)
    return body
