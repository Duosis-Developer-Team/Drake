"""Deployment endpoints.

Read-only by design. There is no endpoint here that deploys, rolls back,
restarts or scales anything: Drake observes Kubernetes, and a control
plane that can also mutate it needs a different authorization story than
"can read deployments".

Nothing in a response carries a raw manifest, an annotation dump, a query,
a credential or a URL Drake did not compose from configured parts.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.db import get_engine
from drake_api.deployments import repository as repo
from drake_api.deployments.model import WORKLOAD_KINDS
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.deployments")

router = APIRouter(prefix="/v1", tags=["deployments"])

_NOT_FOUND = "not found"


def _base_url(request: Request) -> str:
    settings: Settings = request.app.state.settings
    return settings.workflow_run_base_url


@router.get("/deployments")
async def list_deployments(
    request: Request,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    environment_service_id: uuid.UUID | None = None,
    cluster_id: uuid.UUID | None = None,
    workload_kind: str | None = Query(default=None, max_length=32),
    rollout_state: str | None = Query(default=None, max_length=16),
    evidence_state: str | None = Query(default=None, max_length=16),
    started_within: str | None = Query(default=None, max_length=8),
    limit: int = Query(default=repo.DEFAULT_PAGE_SIZE, ge=1, le=repo.MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Deployments in scope, most recent rollout first."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        try:
            return await repo.list_deployments(
                connection,
                auth.principal,
                project_id=project_id,
                environment_id=environment_id,
                environment_service_id=environment_service_id,
                cluster_id=cluster_id,
                workload_kind=workload_kind,
                rollout_state=rollout_state,
                evidence_state=evidence_state,
                started_within=started_within,
                limit=limit,
                cursor=cursor,
                workflow_base_url=_base_url(request),
            )
        except repo.FilterError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/deployments/filters")
async def deployment_filters(_auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    """The values the list endpoint accepts.

    Static vocabulary only — no project or cluster lists, which would make
    this a way to enumerate what someone cannot see.
    """
    return {
        "workload_kinds": sorted(WORKLOAD_KINDS),
        "rollout_states": sorted(repo.ROLLOUT_STATES),
        "evidence_states": sorted(repo.EVIDENCE_STATES),
        "started_within": sorted(repo.OPENED_WINDOWS),
    }


@router.get("/deployments/{deployment_id}")
async def get_deployment(
    request: Request, deployment_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        deployment = await repo.get_deployment(
            connection, auth.principal, deployment_id, _base_url(request)
        )
    if deployment is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return deployment


@router.get("/deployments/{deployment_id}/revisions")
async def revision_timeline(
    request: Request, deployment_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        timeline = await repo.revision_timeline(connection, auth.principal, deployment_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"revisions": timeline}


@router.get("/deployments/{deployment_id}/incidents")
async def related_incidents(
    request: Request, deployment_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Incidents that opened shortly after this rollout.

    Overlap in time, not causation. The response says nothing about why,
    because two timestamps cannot support that claim.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        incidents = await repo.related_incidents(connection, auth.principal, deployment_id)
    if incidents is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"incidents": incidents, "correlation_only": True}
