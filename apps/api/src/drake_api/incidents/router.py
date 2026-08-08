"""Incident endpoints.

Reads are reads: no endpoint here evaluates health or opens an incident as
a side effect of being called. Incidents are produced by the evaluation
runner, so a GET can never change what a subsequent GET reports — and no
one can mint an incident by refreshing a page.

The only mutation is acknowledge, and it accepts no free text: there is no
title, no note and no actor field. The actor comes from the authenticated
principal, because a client that can name the acknowledger can name
someone else.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.incidents import repository as incident_repo
from drake_api.incidents.processor import acknowledge_incident
from drake_api.service_health import bindings as binding_repo
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.incidents")

router = APIRouter(prefix="/v1", tags=["incidents"])

# Uniform for anything the caller may not see. A 403 would confirm the
# incident exists, which is the enumeration this whole layer prevents.
_NOT_FOUND = "not found"


class AcknowledgeRequest(BaseModel):
    """Only a version. `extra=forbid` means there is no field for a note,
    a title, or an actor id — none of which a client may supply."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


@router.get("/incidents")
async def list_incidents(
    request: Request,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    environment_service_id: uuid.UUID | None = None,
    state: str | None = Query(default=None, max_length=16),
    severity: str | None = Query(default=None, max_length=16),
    opened_within: str | None = Query(default=None, max_length=8),
    limit: int = Query(
        default=incident_repo.DEFAULT_PAGE_SIZE, ge=1, le=incident_repo.MAX_PAGE_SIZE
    ),
    cursor: str | None = Query(default=None, max_length=512),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Incidents in scope, newest first.

    Every filter is an allowlist value checked in the repository; an
    unknown one is 422 rather than being ignored, so a caller is never
    quietly given a wider result set than they asked for.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        try:
            return await incident_repo.list_incidents(
                connection,
                auth.principal,
                project_id=project_id,
                environment_id=environment_id,
                environment_service_id=environment_service_id,
                state=state,
                severity=severity,
                opened_within=opened_within,
                limit=limit,
                cursor=cursor,
            )
        except incident_repo.FilterError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/incidents/filters")
async def incident_filter_options(auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    """The values the list endpoint accepts.

    Static vocabulary only — no counts and no project/environment lists,
    which would make this a way to enumerate what someone cannot see.
    """
    return {
        "states": sorted(incident_repo.INCIDENT_STATES),
        "severities": sorted(incident_repo.INCIDENT_SEVERITIES),
        "opened_within": sorted(incident_repo.OPENED_WINDOWS),
    }


@router.get("/incidents/{incident_id}")
async def get_incident(
    request: Request, incident_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        incident = await incident_repo.get_incident(connection, auth.principal, incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        # UI gating only. The mutation endpoint re-checks; hiding a button
        # has never been an authorization boundary.
        can_ack = await incident_repo.can_acknowledge(connection, auth.principal, incident_id)
    return {**incident, "can_acknowledge": can_ack}


@router.get("/incidents/{incident_id}/events")
async def incident_events(
    request: Request, incident_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        events = await incident_repo.list_incident_events(
            connection, auth.principal, incident_id
        )
    if events is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"events": events}


@router.post("/incidents/{incident_id}/acknowledge")
async def acknowledge(
    request: Request,
    incident_id: uuid.UUID,
    payload: AcknowledgeRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Acknowledge an open incident.

    Acknowledging says "a human has seen this". It does not close the
    incident and does not stop monitoring: the service is still down, and
    only a real recovery ends that.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)

    async with engine.connect() as connection:
        # Read visibility first, then write authority. Both answer 404 when
        # absent, so neither distinguishes "not yours" from "not there".
        visible = await incident_repo.get_incident(connection, auth.principal, incident_id)
        if visible is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        if not await incident_repo.can_acknowledge(connection, auth.principal, incident_id):
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

    async with engine.begin() as connection:
        result = await acknowledge_incident(
            connection,
            incident_id,
            uuid.UUID(auth.session.identity_id),
            payload.expected_version,
        )

    if result["outcome"] == "not_found":
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if result["outcome"] == "conflict":
        raise HTTPException(
            status_code=409, detail="the incident changed since it was read"
        )

    # Audited only when something actually changed. A safe retry is not a
    # second acknowledgement and does not deserve a second audit row.
    if result["outcome"] == "acknowledged":
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action="incident.acknowledge",
                result="success",
                target_type="incident",
                target_id=str(incident_id),
                correlation_id=correlation_id_var.get(),
                metadata={"severity": visible["severity"]},
            ),
        )
    return {
        "id": result["id"],
        "state": result["state"],
        "version": result["version"],
        "acknowledged_at": result["acknowledged_at"],
        "changed": result["outcome"] == "acknowledged",
    }


@router.get("/service-health/bindings/{binding_id}/incidents")
async def service_incidents(
    request: Request,
    binding_id: uuid.UUID,
    limit: int = Query(default=5, ge=1, le=20),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Recent incidents for one bound service, for the health detail screen."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        binding = await binding_repo.get_binding(connection, auth.principal, binding_id)
        if binding is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        page = await incident_repo.list_incidents(
            connection,
            auth.principal,
            environment_service_id=uuid.UUID(binding["environment_service_id"]),
            limit=limit,
        )
    return {"items": page["items"], "total": page["total"]}


@router.get("/service-health/bindings/{binding_id}/transitions")
async def health_transitions(
    request: Request,
    binding_id: uuid.UUID,
    limit: int = Query(
        default=incident_repo.DEFAULT_TRANSITIONS, ge=1, le=incident_repo.MAX_TRANSITIONS
    ),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Recorded status changes for one binding.

    Only changes: a row per poll would be a metric series, and Drake
    already has somewhere better to keep those.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        binding = await binding_repo.get_binding(connection, auth.principal, binding_id)
        if binding is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        transitions = await incident_repo.list_health_transitions(
            connection, auth.principal, binding_id, limit=limit
        )
    return {"transitions": transitions or []}
