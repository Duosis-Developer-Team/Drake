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

import logging
import uuid
from datetime import UTC, datetime
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
from drake_api.observability.store import ClickHouseStore, StoreContractError, StoreUnavailableError
from drake_api.service_health import bindings as binding_repo
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.observability")

router = APIRouter(prefix="/v1", tags=["observability"])

LOGS_PERMISSION = "logs.view"
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
