"""Protection Center endpoints. Read-only, by design.

There is no endpoint here that starts a backup, downloads or deletes an
artifact, triggers a restore, edits a storage URL, or applies retention.
Drake reports on protection; a control plane that can also destroy backups
needs a different authorization story than "can view protection".
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.db import get_engine
from drake_api.protection import repository as repo
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.protection")

router = APIRouter(prefix="/v1", tags=["protection"])

_NOT_FOUND = "not found"


def _found(value: Any) -> Any:
    """Anything the caller may not see answers exactly as an unknown id does."""
    if value is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return value


@router.get("/protection/summary")
async def protection_summary(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Counts across the caller's visible policies.

    Computed from the same scope-filtered set as the list, so the summary
    cannot hint at a policy the list will not show.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        return await repo.summary(connection, auth.principal)


@router.get("/protection/filters")
async def protection_filters(_auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    """The accepted filter vocabulary — static, so it enumerates nothing."""
    return {
        "backup_states": sorted(repo.BACKUP_STATES),
        "recoverability_states": sorted(repo.RECOVERABILITY_STATES),
        "overall_states": sorted(repo.OVERALL_STATES),
        "offsite_states": sorted(repo.OFFSITE_STATES),
        "windows": sorted(repo.WINDOWS),
    }


@router.get("/protection/policies")
async def list_policies(
    request: Request,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    store_key: str | None = Query(default=None, max_length=128),
    connector_key: str | None = Query(default=None, max_length=128),
    backup_state: str | None = Query(default=None, max_length=16),
    recoverability_state: str | None = Query(default=None, max_length=16),
    offsite_state: str | None = Query(default=None, max_length=16),
    window: str | None = Query(default=None, max_length=8),
    limit: int = Query(default=repo.DEFAULT_PAGE_SIZE, ge=1, le=repo.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        try:
            return await repo.list_policies(
                connection,
                auth.principal,
                project_id=project_id,
                environment_id=environment_id,
                store_key=store_key,
                connector_key=connector_key,
                backup_state=backup_state,
                recoverability_state=recoverability_state,
                offsite_state=offsite_state,
                window=window,
                limit=limit,
                offset=offset,
            )
        except repo.FilterError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/protection/policies/{policy_id}")
async def get_policy(
    request: Request, policy_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        found: dict[str, Any] = _found(await repo.get_policy(connection, auth.principal, policy_id))
    return found


@router.get("/protection/policies/{policy_id}/runs")
async def policy_runs(
    request: Request, policy_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        runs = _found(await repo.policy_runs(connection, auth.principal, policy_id))
    return {"runs": runs}


@router.get("/protection/policies/{policy_id}/drills")
async def policy_drills(
    request: Request, policy_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        drills = _found(await repo.policy_drills(connection, auth.principal, policy_id))
    return {"drills": drills}


@router.get("/protection/policies/{policy_id}/incidents")
async def policy_incidents(
    request: Request, policy_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        incidents = await _found(
            await repo.policy_incidents(connection, auth.principal, policy_id)
        )
    return {"incidents": incidents}


@router.get("/protection/runs/{run_id}")
async def get_run(
    request: Request, run_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        found: dict[str, Any] = _found(await repo.get_run(connection, auth.principal, run_id))
    return found


@router.get("/protection/artifacts/{artifact_id}")
async def get_artifact(
    request: Request, artifact_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Artifact evidence: size, checksum prefix, integrity, offsite copies.

    Never a download link, a path, or a filename — an artifact endpoint
    that could hand back a location would make Drake a way to reach the
    backups it is only supposed to report on.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        artifact = await repo.get_artifact(connection, auth.principal, artifact_id)
        found: dict[str, Any] = _found(artifact)
    return found


@router.get("/protection/restore-drills/{drill_id}")
async def get_drill(
    request: Request, drill_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        found: dict[str, Any] = _found(await repo.get_drill(connection, auth.principal, drill_id))
    return found
