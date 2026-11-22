"""Development agents: the map, a slice of commits, aliases, connecting Claude Code."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from drake_api.ai_agents.repository import _visibility
from drake_api.audit.service import AuditEventData, record_audit_event_in
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.dev_activity import repository as repo
from drake_api.settings import Settings

router = APIRouter(prefix="/v1/agents/dev", tags=["agents"])

_WINDOW = "^(7d|30d|90d)$"
_AGENT = "^(claude_code|copilot|codex|cursor|devin|other_ai|none)$"


def _loop(request: Request) -> Any:
    return getattr(request.app.state, "dev_activity", None)


@router.get("/map")
async def dev_map(
    request: Request,
    window: str = Query("30d", pattern=_WINDOW),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """People, the projects and services they changed, and with which agent."""
    settings: Settings = request.app.state.settings
    loop = _loop(request)
    async with get_engine(settings).connect() as connection:
        return await repo.dev_map(
            connection,
            auth.principal,
            window=window,
            reads_commits=bool(loop and loop.reads_commits),
            reads_usage=bool(loop and loop.reads_usage),
        )


@router.get("/commits")
async def dev_commits(
    request: Request,
    window: str = Query("30d", pattern=_WINDOW),
    person: str | None = Query(None, max_length=200),
    project: uuid.UUID | None = Query(None),
    service: str | None = Query(None, max_length=200),
    agent: str | None = Query(None, pattern=_AGENT),
    limit: int = Query(200, ge=1, le=500),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The commits behind one arrow, person, project or service."""
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        return await repo.dev_commits(
            connection,
            auth.principal,
            window=window,
            person=person,
            project=project,
            service=service,
            agent=agent,
            limit=limit,
        )


@router.get("/connect")
async def connect(request: Request, auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    """How a developer points Claude Code at this deployment.

    The door's token is a Secret the operator hands out: it is never part of
    an answer, here or on the Agents screen.
    """
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        project_ids, _ = await _visibility(connection, auth.principal)
        keys = [
            str(r[0])
            for r in (
                await connection.execute(
                    text(
                        "SELECT project_key FROM projects WHERE id = ANY(:ids) "
                        "AND lifecycle = 'active' ORDER BY project_key"
                    ),
                    {"ids": project_ids or [uuid.UUID(int=0)]},
                )
            ).all()
        ]
    endpoint = (
        f"{settings.resolved_public_origin()}/otlp" if settings.agent_external_ingest else None
    )
    loop = _loop(request)
    return {
        "endpoint": endpoint,
        "collecting": bool(loop and loop.reads_usage),
        "projects": keys,
        "protocol": "http/protobuf",
        "content_capture": "dropped",
    }


class AliasSet(BaseModel):
    kind: str = Field(pattern="^(github|claude)$")
    alias: str = Field(min_length=1, max_length=200, pattern=r"^[^@\s]+$")
    person_key: str = Field(min_length=1, max_length=100, pattern=r"^[^@\s]+$")
    display_name: str | None = Field(default=None, max_length=120, pattern=r"^[^@]*$")


def _audit(auth: AuthContext, action: str, target: str, meta: dict[str, Any]) -> AuditEventData:
    return AuditEventData(
        actor_type="user",
        actor_id=auth.session.identity_id,
        action=action,
        result="success",
        target_type="dev_person_alias",
        target_id=target,
        correlation_id=correlation_id_var.get(),
        metadata=meta,
    )


@router.get("/aliases")
async def list_aliases(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        if not await repo.may_manage_aliases(connection, auth.principal):
            raise HTTPException(status_code=404, detail="not found")
        return {"items": await repo.list_aliases(connection)}


@router.put("/aliases")
async def set_alias(
    request: Request, payload: AliasSet, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """Say that a login (or a Claude Code identity) belongs to a person."""
    settings: Settings = request.app.state.settings
    async with get_engine(settings).begin() as connection:
        if not await repo.may_manage_aliases(connection, auth.principal):
            raise HTTPException(status_code=404, detail="not found")
        await repo.set_alias(
            connection,
            kind=payload.kind,
            alias=payload.alias,
            person_key=payload.person_key,
            display_name=payload.display_name,
            actor=uuid.UUID(auth.session.identity_id),
        )
        await record_audit_event_in(
            connection,
            _audit(
                auth,
                "dev.alias.set",
                f"{payload.kind}:{payload.alias}",
                {"person_key": payload.person_key, "display_name": payload.display_name},
            ),
        )
        return {"items": await repo.list_aliases(connection)}


@router.delete("/aliases")
async def delete_alias(
    request: Request,
    kind: str = Query(pattern="^(github|claude)$"),
    alias: str = Query(min_length=1, max_length=200),
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).begin() as connection:
        if not await repo.may_manage_aliases(connection, auth.principal):
            raise HTTPException(status_code=404, detail="not found")
        if not await repo.delete_alias(connection, kind=kind, alias=alias):
            raise HTTPException(status_code=404, detail="not found")
        await record_audit_event_in(
            connection, _audit(auth, "dev.alias.delete", f"{kind}:{alias}", {})
        )
        return {"items": await repo.list_aliases(connection)}
