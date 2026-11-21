"""Agents: the inventory, one agent, and how to connect one."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from drake_api.ai_agents import repository as repo
from drake_api.ai_agents import runs as run_views
from drake_api.ai_agents.discovery import identity_of
from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.observability.store import ClickHouseStore, StoreContractError, StoreUnavailableError
from drake_api.settings import Settings

logger = logging.getLogger(__name__)
_NOT_FOUND = "not found"

router = APIRouter(prefix="/v1", tags=["agents"])


@router.get("/agents")
async def list_agents(
    request: Request,
    window: str = Query("24h", pattern="^(24h|7d)$"),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        body = await repo.list_agents(connection, auth.principal, window=window)
    return {**body, "discovering": bool(getattr(request.app.state, "agent_discovery", None))}


@router.get("/agents/connect")
async def connect(request: Request, auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    """Where to send traces. Endpoints only: the external door's token is a
    Secret the operator hands out, never part of an answer."""
    settings: Settings = request.app.state.settings
    external = None
    if settings.agent_external_ingest:
        external = f"{settings.resolved_public_origin()}/otlp"
    return {
        "cluster_endpoint": settings.agent_cluster_endpoint,
        "external_endpoint": external,
        "protocol": "http/protobuf",
        "content_capture": "dropped",
    }


@router.get("/agents/topology")
async def agent_topology(
    request: Request,
    window: str = Query("24h", pattern="^(24h|7d)$"),
    focus: uuid.UUID | None = Query(None),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Agents and what they reach: models, tools by server, other agents."""
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        body = await repo.topology(connection, auth.principal, window=window, focus=focus)
    if body is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return body


@router.get("/agents/{agent_id}")
async def get_agent(
    request: Request,
    agent_id: uuid.UUID,
    window: str = Query("7d", pattern="^(24h|7d)$"),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        agent = await repo.get_agent(connection, auth.principal, agent_id, window=window)
    if agent is None:
        raise HTTPException(status_code=404, detail="not found")
    return agent


def _run_store(request: Request) -> ClickHouseStore | None:
    settings: Settings = request.app.state.settings
    store: ClickHouseStore | None = getattr(request.app.state, "observability_store", None)
    if not settings.observability_traces_enabled or store is None or store.connector is None:
        return None
    return store


async def _read(store: ClickHouseStore, statement: run_views.RunStatement) -> list[dict[str, Any]]:
    assert store.connector is not None
    result = await store.query(
        statement.sql,
        statement.parameters,
        max_rows=statement.max_rows,
        timeout_seconds=store.connector.timeout_seconds,
        correlation_id=correlation_id_var.get() or "",
    )
    return result.rows if isinstance(result.rows, list) else []


def _unavailable(error: Exception) -> str:
    if isinstance(error, StoreUnavailableError | StoreContractError):
        return error.code
    return "store_malformed_row"


@router.get("/agents/{agent_id}/runs")
async def agent_runs(
    request: Request,
    agent_id: uuid.UUID,
    window: str = Query("24h", pattern="^(24h|7d)$"),
    status: str = Query("any", pattern="^(any|error)$"),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The agent's latest runs, newest first, read from the trace store."""
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        agent = await repo.agent_for_runs(connection, auth.principal, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    base = {"agent_id": str(agent_id), "window": window, "status": status}
    store = _run_store(request)
    if store is None or store.connector is None:
        return {**base, "state": "not_configured", "runs": []}
    statement = run_views.build_runs(
        database=store.connector.database,
        agent=agent,
        window=repo.WINDOWS[window],
        now=datetime.now(UTC),
        failed_only=status == "error",
    )
    try:
        runs = run_views.normalize_runs(await _read(store, statement))
    except (StoreUnavailableError, StoreContractError, ValueError) as error:
        logger.warning("agent runs unavailable", extra={"code": _unavailable(error)})
        return {**base, "state": "unavailable", "reason": _unavailable(error), "runs": []}
    return {**base, "state": "ok", "runs": runs}


@router.get("/agents/{agent_id}/runs/{trace_id}")
async def agent_run(
    request: Request,
    agent_id: uuid.UUID,
    trace_id: str,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """One run, step by step. A trace this agent took no part in is a 404 —
    the same answer as a trace that does not exist."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        agent = await repo.agent_for_runs(connection, auth.principal, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    store = _run_store(request)
    if store is None or store.connector is None:
        return {"agent_id": str(agent_id), "trace_id": trace_id, "state": "not_configured"}
    try:
        statement = run_views.build_run(
            database=store.connector.database, trace_id=trace_id, now=datetime.now(UTC)
        )
    except run_views.RunQueryError as error:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from error
    try:
        rows = await _read(store, statement)
    except (StoreUnavailableError, StoreContractError, ValueError) as error:
        logger.warning("agent run unavailable", extra={"code": _unavailable(error)})
        return {
            "agent_id": str(agent_id),
            "trace_id": trace_id,
            "state": "unavailable",
            "reason": _unavailable(error),
        }
    keys = sorted({identity_of(row).key for row in rows if run_views.as_int(row.get("genai"))})
    async with engine.connect() as connection:
        visible = await repo.visible_agents_by_key(connection, auth.principal, keys)
    run = run_views.assemble_run(
        rows, trace_id=statement.parameters["trace_id"], agent=agent, visible_agents=visible
    )
    if run is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {
        "agent_id": str(agent_id),
        "trace_id": run.trace_id,
        "state": "ok",
        "started_at": run.started_at,
        "duration_ms": run.duration_ms,
        "hidden_spans": run.hidden,
        "truncated": len(rows) >= run_views.MAX_RUN_SPANS,
        "spans": run.spans,
    }
