"""Service health and workload binding endpoints.

The API answers with decisions, not with material to decide from: a caller
receives a status and reason codes, never a query, a threshold to apply, or
a datasource credential. That keeps one judgement in one place and stops
two clients disagreeing about what "healthy" means.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.service_health import bindings as binding_repo
from drake_api.service_health.policy import get_policy, policy_keys
from drake_api.service_health.presets import DEFAULT_PRESET_KEY, describe_presets, preset_keys
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.service_health")

router = APIRouter(prefix="/v1", tags=["service-health"])

# Uniform for anything the caller may not see, exactly as the catalog does:
# a 403 would confirm the resource exists.
_NOT_FOUND = "not found"


class BindingCreate(BaseModel):
    environment_service_id: uuid.UUID
    cluster_id: uuid.UUID
    namespace: str = Field(min_length=1, max_length=63)
    workload_kind: str = Field(min_length=1, max_length=32)
    workload_name: str = Field(min_length=1, max_length=253)
    preset_key: str = Field(default=DEFAULT_PRESET_KEY, max_length=64)
    health_policy_key: str = Field(default="default.v1", max_length=64)
    integration_id: uuid.UUID | None = None


class BindingLifecycle(BaseModel):
    lifecycle: str = Field(pattern="^(active|disabled)$")
    expected_revision: int | None = None


@router.get("/service-health/presets")
async def list_presets(auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    """What a binding may be configured with.

    Template keys and descriptions only. The expressions behind them stay
    on the server; a caller has no reason to see one and no way to supply
    one.
    """
    return {
        "presets": describe_presets(),
        "policies": [
            {
                "key": key,
                "title": get_policy(key).title,
                "max_telemetry_age_seconds": get_policy(key).max_telemetry_age_seconds,
                "window_seconds": get_policy(key).window_seconds,
            }
            for key in policy_keys()
        ],
        "workload_kinds": sorted(binding_repo.WORKLOAD_KINDS),
    }


@router.get("/service-health/bindings")
async def list_bindings(
    request: Request,
    environment_service_id: uuid.UUID | None = None,
    limit: int = Query(default=binding_repo.DEFAULT_PAGE_SIZE, ge=1, le=binding_repo.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        return await binding_repo.list_bindings(
            connection,
            auth.principal,
            environment_service_id=environment_service_id,
            limit=limit,
            offset=offset,
        )


@router.get("/service-health/bindings/{binding_id}")
async def get_binding(
    request: Request, binding_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        binding = await binding_repo.get_binding(connection, auth.principal, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return binding


@router.post("/service-health/bindings", status_code=201)
async def create_binding(
    request: Request, payload: BindingCreate, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    target = binding_repo.BindingTarget(
        environment_service_id=payload.environment_service_id,
        cluster_id=payload.cluster_id,
        namespace=payload.namespace,
        workload_kind=payload.workload_kind,
        workload_name=payload.workload_name,
        preset_key=payload.preset_key,
        health_policy_key=payload.health_policy_key,
        integration_id=payload.integration_id,
    )

    async with engine.begin() as connection:
        try:
            created = await binding_repo.create_binding(
                connection,
                auth.principal,
                target,
                preset_keys(),
                uuid.UUID(auth.session.identity_id),
            )
        except binding_repo.BindingError as error:
            if error.code == "not_found":
                raise HTTPException(status_code=404, detail=_NOT_FOUND) from error
            status = 409 if error.code == "duplicate_binding" else 422
            raise HTTPException(status_code=status, detail=str(error)) from error

    # Audited only after the write actually happened.
    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="service_health.binding.create",
            result="success",
            target_type="service_workload_binding",
            target_id=created["id"],
            correlation_id=correlation_id_var.get(),
            metadata={
                "namespace": payload.namespace,
                "workload_kind": payload.workload_kind,
                "workload_name": payload.workload_name,
                "preset_key": payload.preset_key,
                "health_policy_key": payload.health_policy_key,
                "resolved": created["resolved"],
            },
        ),
    )
    return created


@router.post("/service-health/bindings/{binding_id}/lifecycle")
async def set_binding_lifecycle(
    request: Request,
    binding_id: uuid.UUID,
    payload: BindingLifecycle,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        try:
            result = await binding_repo.set_lifecycle(
                connection,
                auth.principal,
                binding_id,
                payload.lifecycle,
                payload.expected_revision,
                uuid.UUID(auth.session.identity_id),
            )
        except binding_repo.BindingError as error:
            if error.code == "not_found":
                raise HTTPException(status_code=404, detail=_NOT_FOUND) from error
            status = 409 if error.code == "revision_conflict" else 422
            raise HTTPException(status_code=status, detail=str(error)) from error

    # A no-op is not a change, so it produces no audit entry.
    if result["changed"]:
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action=f"service_health.binding.{payload.lifecycle}",
                result="success",
                target_type="service_workload_binding",
                target_id=str(binding_id),
                correlation_id=correlation_id_var.get(),
                metadata={"lifecycle": payload.lifecycle},
            ),
        )
    return result


@router.post("/service-health/bindings/{binding_id}/resolve")
async def resolve_binding(
    request: Request, binding_id: uuid.UUID, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """Re-check the bound workload against cluster inventory.

    A workload that is not found leaves the binding in place and reports
    `unresolved`: an agent that has not reported yet is not a reason to
    discard an operator's configuration.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        visible = await binding_repo.get_binding(connection, auth.principal, binding_id)
    if visible is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    async with engine.begin() as connection:
        result = await binding_repo.refresh_resolution(connection, binding_id)
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return result
