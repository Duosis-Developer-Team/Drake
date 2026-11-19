"""Dashboard endpoints (ADR-0033).

- `GET  /v1/dashboards`: every environment the caller may see, with its dashboard
- `GET  /v1/dashboards/{environment_id}`: one environment's dashboard (the default
  layout at version 0 when none was saved)
- `PUT  /v1/dashboards/{environment_id}`: save a new version
- `GET  /v1/dashboards/{environment_id}/versions`: saved versions, newest first

There is no delete. Restoring an old version is a save of its layout.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from drake_api.audit.service import AuditEventData, record_audit_event_in
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.dashboards import repository as repo
from drake_api.dashboards.model import MAX_NOTE, MAX_TITLE, WIDGETS
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["dashboards"])

_STATUS = {"not_found": 404, "version_conflict": 409, "invalid_dashboard": 422}


class WidgetSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=40)
    size: str = Field(min_length=1, max_length=2)
    hidden: bool = False


class DashboardSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    layout: list[WidgetSlot] = Field(max_length=len(WIDGETS))
    note: str | None = Field(default=None, max_length=MAX_NOTE)
    expected_version: int = Field(ge=0)


def _fail(error: repo.DashboardError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(error.code, 422), detail=str(error))


@router.get("/dashboards")
async def list_dashboards(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        return await repo.list_dashboards(connection, auth.principal)


@router.get("/dashboards/{environment_id}")
async def get_dashboard(
    request: Request, environment_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        try:
            return await repo.get_dashboard(connection, auth.principal, environment_id)
        except repo.DashboardError as error:
            raise _fail(error) from error


@router.put("/dashboards/{environment_id}")
async def save_dashboard(
    request: Request,
    environment_id: uuid.UUID,
    payload: DashboardSave,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    actor = uuid.UUID(auth.session.identity_id)
    async with get_engine(settings).begin() as connection:
        try:
            saved = await repo.save_dashboard(
                connection, auth.principal, environment_id, payload.model_dump(), actor
            )
        except repo.DashboardError as error:
            raise _fail(error) from error
        await record_audit_event_in(
            connection,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action="dashboard.save",
                result="success",
                target_type="dashboard",
                target_id=str(saved["id"]),
                correlation_id=correlation_id_var.get(),
                metadata={
                    "environment_id": str(environment_id),
                    "version": saved["version"],
                    "shown": [w["key"] for w in saved["layout"] if not w["hidden"]],
                },
            ),
        )
    return saved


@router.get("/dashboards/{environment_id}/versions")
async def dashboard_versions(
    request: Request, environment_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        try:
            return await repo.list_versions(connection, auth.principal, environment_id)
        except repo.DashboardError as error:
            raise _fail(error) from error
