"""Agents: the inventory, one agent, and how to connect one."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from drake_api.ai_agents import repository as repo
from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.db import get_engine
from drake_api.settings import Settings

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
