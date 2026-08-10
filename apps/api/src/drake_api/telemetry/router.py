"""Telemetry API surface.

- ``GET /v1/metrics/catalog`` — bounded cursor pagination over the metric
  registry (registry metadata only; no tenant data).
- ``GET /v1/dashboard-templates/{template_key}`` — one dashboard template.
- ``POST /v1/telemetry/query`` — the Query Broker. ``extra=forbid``
  everywhere: raw PromQL, provider URLs, config refs, label names, regexes,
  and operators have no field to arrive in and 422 if attempted.
"""

import asyncio
import base64
import binascii
import contextlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.telemetry.broker import QueryInput, TelemetryBroker
from drake_api.telemetry.registry import TelemetryRegistry, find_dashboard

router = APIRouter(prefix="/v1", tags=["telemetry"])

_KEY_PATTERN = r"^[a-z0-9]([a-z0-9_.-]{0,62}[a-z0-9])?$"
_PARAM_VALUE = re.compile(r"^[a-z0-9]([a-z0-9_.-]{0,62}[a-z0-9])?$")


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(pattern="^(environment|service|cluster)$")
    id: uuid.UUID


class RangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: datetime = Field(alias="from")
    to: datetime
    step_seconds: int = Field(ge=1, le=86400)

    @field_validator("from_", "to")
    @classmethod
    def utc_contract(cls, value: datetime) -> datetime:
        # The contract is UTC ISO-8601: naive timestamps are ambiguous and
        # refused; aware values (any offset) normalize to the same UTC
        # instant, so DST or host timezones can never change the result.
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware UTC ISO-8601")
        return value.astimezone(UTC)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_key: str = Field(pattern=_KEY_PATTERN, max_length=64)
    scope: ScopeIn
    range: RangeIn
    parameters: dict[str, str] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def bounded_parameters(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 4:
            raise ValueError("too many parameters")
        for name, parameter in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
                raise ValueError("malformed parameter name")
            if not _PARAM_VALUE.fullmatch(parameter):
                raise ValueError("malformed parameter value")
        return value


def _registry(request: Request) -> TelemetryRegistry:
    registry: TelemetryRegistry = request.app.state.telemetry_registry
    return registry


def _broker(request: Request) -> TelemetryBroker:
    broker: TelemetryBroker = request.app.state.telemetry_broker
    return broker


@router.get("/metrics/catalog")
async def metrics_catalog(
    request: Request,
    _auth: AuthContext = Depends(require_auth),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    registry = _registry(request)
    entries = sorted(registry.metrics.values(), key=lambda m: (m.key, m.version))
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            cursor_key, cursor_version = str(decoded[0]), int(decoded[1])
        except (binascii.Error, ValueError, TypeError, IndexError, KeyError) as error:
            raise HTTPException(status_code=422, detail="invalid cursor") from error
        entries = [e for e in entries if (e.key, e.version) > (cursor_key, cursor_version)]
    page = entries[:limit]
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps([page[-1].key, page[-1].version]).encode()).decode()
        if len(entries) > limit and page
        else None
    )
    return {
        "metrics": [
            {
                "key": metric.key,
                "version": metric.version,
                "type": metric.metric_type,
                "unit": metric.unit,
                "source_type": metric.source_type,
                "output_labels": list(metric.allowed_output_labels),
                "profiles": list(metric.profiles),
            }
            for metric in page
        ],
        "next_cursor": next_cursor,
        "registry_hash": registry.content_hash,
    }


@router.get("/dashboard-templates/{template_key}")
async def dashboard_template(
    request: Request,
    template_key: str,
    _auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    if not re.fullmatch(_KEY_PATTERN, template_key):
        raise HTTPException(status_code=422, detail="malformed template key")
    dashboard = find_dashboard(_registry(request), template_key)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"dashboard": dashboard.raw, "registry_hash": _registry(request).content_hash}


# Strong references for broker tasks still cleaning up while their
# endpoint task is being torn down (a repeated endpoint cancellation can
# interrupt the awaited cleanup; without a reference the running task could
# be garbage-collected before its finally releases leases).
_CLEANUP_TASKS: set[asyncio.Task[Any]] = set()


async def _watch_disconnect(request: Request) -> None:
    """Event-driven http.disconnect watcher (no busy polling).

    The request body is fully consumed before the handler runs, so the next
    ASGI receive() resolves only when the client connection closes.
    """
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


@router.post("/telemetry/query")
async def telemetry_query(
    request: Request,
    body: QueryRequest,
    auth: AuthContext = Depends(require_csrf),
) -> Any:
    broker = _broker(request)
    # The broker runs as a SUPERVISED task tied to the client connection:
    # if the client disconnects first, the task is cancelled AND awaited —
    # the provider stream closes and both Redis leases are released with
    # their own tokens immediately (see TelemetryBroker.query's finally and
    # ConcurrencyLeases cancellation safety). Cancellation is never recorded
    # as a provider failure and never produces stale fallbacks.
    query_task = asyncio.create_task(
        broker.query(
            auth.principal,
            QueryInput(
                template_key=body.template_key,
                scope_type=body.scope.type,
                scope_id=body.scope.id,
                from_dt=body.range.from_,
                to_dt=body.range.to,
                step_seconds=body.range.step_seconds,
                parameters=body.parameters,
            ),
        )
    )
    disconnect_task = asyncio.create_task(_watch_disconnect(request))
    try:
        await asyncio.wait({query_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED)
        if not query_task.done():
            # Client gone before the query finished. Cleanup happens in the
            # finally below; nobody is listening for this response.
            raise HTTPException(status_code=499, detail="client disconnected")
        return query_task.result()
    finally:
        # BOTH tasks are cancelled and AWAITED on EVERY exit path — including
        # the server runtime cancelling this endpoint task itself on client
        # disconnect (asyncio.wait never cancels its children, and an
        # unreferenced task could be garbage-collected without running its
        # finally, leaking held leases).
        disconnect_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await disconnect_task
        if not query_task.done():
            query_task.cancel()
            # The endpoint's outcome is already decided; the broker task's
            # own finally releases provider resources and leases. The strong
            # reference guarantees that cleanup completes even if a repeated
            # endpoint cancellation interrupts this await.
            _CLEANUP_TASKS.add(query_task)
            query_task.add_done_callback(_CLEANUP_TASKS.discard)
            with contextlib.suppress(BaseException):
                await query_task


# Registered ONLY when settings allow it (local/test + explicit enable):
# when disabled the route does not exist at all, so probing yields the same
# 404 as any unknown path — no existence signal.
internal_router = APIRouter(prefix="/v1", tags=["telemetry-internal"])


@internal_router.get("/internal/metrics")
async def internal_metrics(request: Request) -> Any:
    """Drake's own bounded broker metrics (Prometheus text format).

    Local/test only; a future real Prometheus scrape needs a separate
    internal listener/service/network policy — never the public API app.
    """
    from fastapi.responses import PlainTextResponse
    from sqlalchemy import text as sql_text

    from drake_api.db import get_engine
    from drake_api.protection.metrics import (
        PROTECTION_METRIC_QUERY,
        metric_rows,
        render_protection_metrics,
    )

    body = request.app.state.telemetry_metrics.render()
    # Protection gauges join the same bounded exposition. Their labels are
    # controlled identities only — no artifact id, filename or checksum.
    try:
        engine = get_engine(request.app.state.settings)
        async with engine.connect() as connection:
            rows = (await connection.execute(sql_text(PROTECTION_METRIC_QUERY))).all()
        body += render_protection_metrics(metric_rows(list(rows)))
    except Exception:  # noqa: S110 - metrics must not take the endpoint down
        pass
    return PlainTextResponse(body)
