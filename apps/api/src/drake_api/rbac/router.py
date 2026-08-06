"""RBAC API: permission catalog, roles, grants.

Contract for mutations:
- ``Idempotency-Key`` header required. Idempotency is TRANSACTIONAL in
  PostgreSQL: the claim, the domain mutation, the audit row, and the stored
  response commit together. Replays return the stored response; the same key
  with a different payload/precondition is a stable ``409
  idempotency_conflict``. Redis holds no authority here.
- ``If-Match`` (role version ETag) required on role updates: missing → 428,
  stale → 412.
- CSRF + Origin checks apply (cookie-authenticated).

Denial semantics: 401 unauthenticated; 403 missing global capability;
consistent 404 for anything outside the caller's scope (anti-enumeration).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Self

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.rbac.idempotency import (
    IdempotencyConflictError,
    IdempotencyGuard,
    request_fingerprint,
)
from drake_api.rbac.service import (
    AuthorizationDeniedError,
    InvariantViolationError,
    PreconditionFailedError,
    RbacService,
    ScopedResourceHiddenError,
)
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.rbac")

router = APIRouter(prefix="/v1", tags=["rbac"])


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)


class RoleUpdate(BaseModel):
    description: str = Field(max_length=500)


class RolePermissionsSet(BaseModel):
    permissions: list[str] = Field(max_length=100)


class GrantCreate(BaseModel):
    role_id: uuid.UUID
    scope_id: uuid.UUID
    identity_id: uuid.UUID | None = None
    group_mapping_id: uuid.UUID | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @model_validator(mode="after")
    def _validity_interval(self) -> Self:
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be after valid_from")
        return self


def _map_service_errors(error: Exception) -> HTTPException:
    if isinstance(error, AuthorizationDeniedError):
        return HTTPException(status_code=403, detail="not permitted")
    if isinstance(error, ScopedResourceHiddenError):
        return HTTPException(status_code=404, detail="not found")
    if isinstance(error, PreconditionFailedError):
        return HTTPException(status_code=412, detail="stale precondition (If-Match)")
    if isinstance(error, InvariantViolationError):
        return HTTPException(status_code=409, detail=error.code)
    raise error


def _role_etag(version: int) -> str:
    return f'W/"role-{version}"'


def _parse_if_match(if_match: str | None) -> int:
    if not if_match:
        raise HTTPException(status_code=428, detail="If-Match header required")
    try:
        return int(if_match.strip().removeprefix('W/"role-').removesuffix('"'))
    except ValueError as error:
        raise HTTPException(status_code=428, detail="If-Match header malformed") from error


def _require_idempotency_key(idempotency_key: str | None) -> str:
    if not idempotency_key or not (8 <= len(idempotency_key) <= 200):
        raise HTTPException(status_code=428, detail="Idempotency-Key header required")
    return idempotency_key


async def _audit_denial(request: Request, auth: AuthContext, action: str) -> None:
    """Best-effort denial audit; a broken audit store never hides the denial."""
    settings: Settings = request.app.state.settings
    try:
        await record_audit_event(
            get_engine(settings),
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action=action,
                result="denied",
                correlation_id=correlation_id_var.get(),
            ),
        )
    except Exception:
        logger.warning("denial audit write did not persist")


Mutation = Callable[[RbacService], Awaitable[tuple[int, dict[str, Any]]]]


async def _idempotent_mutation(
    request: Request,
    auth: AuthContext,
    *,
    operation: str,
    idempotency_key: str,
    fingerprint: str,
    mutate: Mutation,
) -> JSONResponse:
    """Run one RBAC mutation with transactional idempotency.

    Claim, mutation, audit, and stored response commit in ONE PostgreSQL
    transaction; any failure rolls everything back, leaving the key
    retryable. Committed keys replay their stored response.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        async with engine.begin() as connection:
            guard = IdempotencyGuard(
                connection,
                auth.session.identity_id,
                operation,
                idempotency_key,
                fingerprint,
            )
            replay = await guard.claim()
            if replay is not None:
                status_code, body = replay
                return JSONResponse(status_code=status_code, content=body)

            service = RbacService(connection)
            status_code, body = await mutate(service)
            await guard.complete(status_code, body)
        return JSONResponse(status_code=status_code, content=body)
    except IdempotencyConflictError:
        raise HTTPException(status_code=409, detail="idempotency_conflict") from None
    except (
        AuthorizationDeniedError,
        ScopedResourceHiddenError,
        PreconditionFailedError,
        InvariantViolationError,
    ) as error:
        if isinstance(error, AuthorizationDeniedError):
            await _audit_denial(request, auth, error.audit_action)
        raise _map_service_errors(error) from error


# ------------------------------------------------------------------ catalog
@router.get("/permissions")
async def list_permissions(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        service = RbacService(connection)
        grants = await service.effective_grants(auth.principal)
        if not any(g.permission == "rbac.manage" for g in grants):
            await _audit_denial(request, auth, "rbac.catalog.denied")
            raise HTTPException(status_code=403, detail="not permitted")
        rows = (
            await connection.execute(
                text("SELECT key, description, catalog_version FROM permissions ORDER BY key")
            )
        ).all()
    return {
        "permissions": [
            {"key": row[0], "description": row[1], "catalog_version": row[2]} for row in rows
        ]
    }


# -------------------------------------------------------------------- roles
@router.get("/roles")
async def list_roles(request: Request, auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        service = RbacService(connection)
        grants = await service.effective_grants(auth.principal)
        if not any(g.permission == "rbac.manage" for g in grants):
            await _audit_denial(request, auth, "rbac.roles.denied")
            raise HTTPException(status_code=403, detail="not permitted")
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT r.id, r.name, r.description, r.is_system, r.status, r.version,
                           coalesce(array_agg(rp.permission_key ORDER BY rp.permission_key)
                                    FILTER (WHERE rp.permission_key IS NOT NULL), '{}')
                    FROM roles r
                    LEFT JOIN role_permissions rp ON rp.role_id = r.id
                    GROUP BY r.id
                    ORDER BY r.is_system DESC, r.name
                    """
                )
            )
        ).all()
    return {
        "roles": [
            {
                "id": str(row[0]),
                "name": row[1],
                "description": row[2],
                "is_system": row[3],
                "status": row[4],
                "version": row[5],
                "permissions": list(row[6]),
                "etag": _role_etag(row[5]),
            }
            for row in rows
        ]
    }


@router.post("/roles", status_code=201)
async def create_role(
    request: Request,
    body: RoleCreate,
    auth: AuthContext = Depends(require_csrf),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    key = _require_idempotency_key(idempotency_key)
    fingerprint = request_fingerprint({}, body.model_dump(mode="json"))

    async def mutate(service: RbacService) -> tuple[int, dict[str, Any]]:
        result = await service.create_role(
            auth.principal, body.name, body.description, correlation_id_var.get()
        )
        return 201, {"id": str(result["id"]), "version": result["version"]}

    return await _idempotent_mutation(
        request,
        auth,
        operation="rbac.roles.create",
        idempotency_key=key,
        fingerprint=fingerprint,
        mutate=mutate,
    )


@router.put("/roles/{role_id}")
async def update_role(
    request: Request,
    role_id: uuid.UUID,
    body: RoleUpdate,
    auth: AuthContext = Depends(require_csrf),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    key = _require_idempotency_key(idempotency_key)
    expected_version = _parse_if_match(if_match)
    fingerprint = request_fingerprint(
        {"role_id": str(role_id)}, body.model_dump(mode="json"), precondition=if_match
    )

    async def mutate(service: RbacService) -> tuple[int, dict[str, Any]]:
        result = await service.update_role(
            auth.principal,
            role_id,
            expected_version,
            body.description,
            correlation_id_var.get(),
        )
        return 200, {"id": str(result["id"]), "version": result["version"]}

    response = await _idempotent_mutation(
        request,
        auth,
        operation="rbac.roles.update",
        idempotency_key=key,
        fingerprint=fingerprint,
        mutate=mutate,
    )
    return response


@router.post("/roles/{role_id}/archive")
async def archive_role(
    request: Request,
    role_id: uuid.UUID,
    auth: AuthContext = Depends(require_csrf),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    key = _require_idempotency_key(idempotency_key)
    expected_version = _parse_if_match(if_match)
    fingerprint = request_fingerprint({"role_id": str(role_id)}, None, precondition=if_match)

    async def mutate(service: RbacService) -> tuple[int, dict[str, Any]]:
        await service.archive_role(
            auth.principal, role_id, expected_version, correlation_id_var.get()
        )
        return 200, {"id": str(role_id), "status": "archived"}

    return await _idempotent_mutation(
        request,
        auth,
        operation="rbac.roles.archive",
        idempotency_key=key,
        fingerprint=fingerprint,
        mutate=mutate,
    )


@router.put("/roles/{role_id}/permissions")
async def set_role_permissions(
    request: Request,
    role_id: uuid.UUID,
    body: RolePermissionsSet,
    auth: AuthContext = Depends(require_csrf),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    key = _require_idempotency_key(idempotency_key)
    expected_version = _parse_if_match(if_match)
    fingerprint = request_fingerprint(
        {"role_id": str(role_id)}, body.model_dump(mode="json"), precondition=if_match
    )

    async def mutate(service: RbacService) -> tuple[int, dict[str, Any]]:
        result = await service.set_role_permissions(
            auth.principal,
            role_id,
            expected_version,
            body.permissions,
            correlation_id_var.get(),
        )
        return 200, {"id": str(result["id"]), "version": result["version"]}

    return await _idempotent_mutation(
        request,
        auth,
        operation="rbac.roles.permissions",
        idempotency_key=key,
        fingerprint=fingerprint,
        mutate=mutate,
    )


# ------------------------------------------------------------------- grants
@router.get("/grants")
async def list_grants(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Grants visible to the caller: only those whose scope is covered by the
    caller's ``rbac.manage`` authority. Unauthorized scopes are simply absent."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        service = RbacService(connection)
        grants = await service.effective_grants(auth.principal)
        manage_scopes = [g.scope_id for g in grants if g.permission == "rbac.manage"]
        if not manage_scopes:
            await _audit_denial(request, auth, "rbac.grants.denied")
            raise HTTPException(status_code=403, detail="not permitted")
        rows = (
            await connection.execute(
                text(
                    """
                    WITH RECURSIVE visible AS (
                        SELECT id FROM scopes WHERE id = ANY(:roots)
                        UNION
                        SELECT s.id FROM scopes s JOIN visible v ON s.parent_id = v.id
                    )
                    SELECT g.id, g.identity_id, i.display_name, g.group_mapping_id,
                           gm.display_name, r.name, r.id, s.scope_type, s.external_ref,
                           g.valid_from, g.valid_to, g.revoked_at
                    FROM grants g
                    JOIN roles r ON r.id = g.role_id
                    JOIN scopes s ON s.id = g.scope_id
                    LEFT JOIN identities i ON i.id = g.identity_id
                    LEFT JOIN group_mappings gm ON gm.id = g.group_mapping_id
                    WHERE g.scope_id IN (SELECT id FROM visible)
                    ORDER BY g.created_at DESC
                    LIMIT 200
                    """
                ),
                {"roots": manage_scopes},
            )
        ).all()
    return {
        "grants": [
            {
                "id": str(row[0]),
                "identity_id": str(row[1]) if row[1] else None,
                "identity_display": row[2],
                "group_mapping_id": str(row[3]) if row[3] else None,
                "group_display": row[4],
                "role_name": row[5],
                "role_id": str(row[6]),
                "scope_type": row[7],
                "scope_ref": row[8],
                "valid_from": row[9].isoformat(),
                "valid_to": row[10].isoformat() if row[10] else None,
                "revoked_at": row[11].isoformat() if row[11] else None,
            }
            for row in rows
        ]
    }


@router.post("/grants", status_code=201)
async def create_grant(
    request: Request,
    body: GrantCreate,
    auth: AuthContext = Depends(require_csrf),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    key = _require_idempotency_key(idempotency_key)
    fingerprint = request_fingerprint({}, body.model_dump(mode="json"))

    async def mutate(service: RbacService) -> tuple[int, dict[str, Any]]:
        grant_id = await service.create_grant(
            auth.principal,
            role_id=body.role_id,
            scope_id=body.scope_id,
            identity_id=body.identity_id,
            group_mapping_id=body.group_mapping_id,
            valid_from=body.valid_from,
            valid_to=body.valid_to,
            correlation_id=correlation_id_var.get(),
        )
        return 201, {"id": str(grant_id)}

    return await _idempotent_mutation(
        request,
        auth,
        operation="rbac.grants.create",
        idempotency_key=key,
        fingerprint=fingerprint,
        mutate=mutate,
    )


@router.delete("/grants/{grant_id}")
async def revoke_grant(
    request: Request,
    grant_id: uuid.UUID,
    auth: AuthContext = Depends(require_csrf),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    key = _require_idempotency_key(idempotency_key)
    fingerprint = request_fingerprint({"grant_id": str(grant_id)}, None)

    async def mutate(service: RbacService) -> tuple[int, dict[str, Any]]:
        await service.revoke_grant(auth.principal, grant_id, correlation_id_var.get())
        return 200, {"id": str(grant_id), "status": "revoked"}

    return await _idempotent_mutation(
        request,
        auth,
        operation="rbac.grants.revoke",
        idempotency_key=key,
        fingerprint=fingerprint,
        mutate=mutate,
    )
