"""RBAC API: permission catalog, roles, grants.

Contract for mutations:
- ``Idempotency-Key`` header required (replayed keys return the stored result).
- ``If-Match`` (role version ETag) required on role updates: missing → 428,
  stale → 412.
- CSRF + Origin checks apply (cookie-authenticated).
- Every mutation and its audit row commit in one transaction (fail-closed).

Denial semantics: 401 unauthenticated; 403 missing global capability;
consistent 404 for anything outside the caller's scope (anti-enumeration).
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
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

_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


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


class _Idempotency:
    """Redis-backed idempotency for mutations (fail-closed when Redis is down)."""

    def __init__(self, settings: Settings) -> None:
        self._client: aioredis.Redis = aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.ready_check_timeout_seconds,
            socket_timeout=settings.ready_check_timeout_seconds,
        )

    async def replay(self, actor_id: str, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._client.get(f"drake:idem:{actor_id}:{key}")
        except Exception as error:
            raise HTTPException(
                status_code=503, detail="idempotency backend unavailable"
            ) from error
        if raw is None:
            return None
        result: dict[str, Any] = json.loads(raw)
        return result

    async def store(self, actor_id: str, key: str, status_code: int, body: dict[str, Any]) -> None:
        try:
            await self._client.set(
                f"drake:idem:{actor_id}:{key}",
                json.dumps({"status_code": status_code, "body": body}),
                ex=_IDEMPOTENCY_TTL_SECONDS,
                nx=True,
            )
        except Exception as error:
            raise HTTPException(
                status_code=503, detail="idempotency backend unavailable"
            ) from error


def get_idempotency(request: Request) -> _Idempotency:
    idem: _Idempotency | None = getattr(request.app.state, "idempotency", None)
    if idem is None:
        idem = _Idempotency(request.app.state.settings)
        request.app.state.idempotency = idem
    return idem


def _require_idempotency_key(idempotency_key: str | None) -> str:
    if not idempotency_key or not (8 <= len(idempotency_key) <= 200):
        raise HTTPException(status_code=428, detail="Idempotency-Key header required")
    return idempotency_key


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
    idem = get_idempotency(request)
    replayed = await idem.replay(auth.session.identity_id, key)
    if replayed is not None:
        return JSONResponse(status_code=replayed["status_code"], content=replayed["body"])

    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        async with engine.begin() as connection:
            service = RbacService(connection)
            result = await service.create_role(
                auth.principal, body.name, body.description, correlation_id_var.get()
            )
    except (
        AuthorizationDeniedError,
        ScopedResourceHiddenError,
        PreconditionFailedError,
        InvariantViolationError,
    ) as error:
        if isinstance(error, AuthorizationDeniedError):
            await _audit_denial(request, auth, error.audit_action)
        raise _map_service_errors(error) from error

    payload = {"id": str(result["id"]), "version": result["version"]}
    await idem.store(auth.session.identity_id, key, 201, payload)
    return JSONResponse(status_code=201, content=payload)


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
    idem = get_idempotency(request)
    replayed = await idem.replay(auth.session.identity_id, key)
    if replayed is not None:
        return JSONResponse(status_code=replayed["status_code"], content=replayed["body"])

    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        async with engine.begin() as connection:
            service = RbacService(connection)
            result = await service.update_role(
                auth.principal,
                role_id,
                expected_version,
                body.description,
                correlation_id_var.get(),
            )
    except (
        AuthorizationDeniedError,
        ScopedResourceHiddenError,
        PreconditionFailedError,
        InvariantViolationError,
    ) as error:
        if isinstance(error, AuthorizationDeniedError):
            await _audit_denial(request, auth, error.audit_action)
        raise _map_service_errors(error) from error

    payload = {"id": str(result["id"]), "version": result["version"]}
    await idem.store(auth.session.identity_id, key, 200, payload)
    response = JSONResponse(status_code=200, content=payload)
    response.headers["ETag"] = _role_etag(result["version"])
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
    idem = get_idempotency(request)
    replayed = await idem.replay(auth.session.identity_id, key)
    if replayed is not None:
        return JSONResponse(status_code=replayed["status_code"], content=replayed["body"])

    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        async with engine.begin() as connection:
            service = RbacService(connection)
            await service.archive_role(
                auth.principal, role_id, expected_version, correlation_id_var.get()
            )
    except (
        AuthorizationDeniedError,
        ScopedResourceHiddenError,
        PreconditionFailedError,
        InvariantViolationError,
    ) as error:
        if isinstance(error, AuthorizationDeniedError):
            await _audit_denial(request, auth, error.audit_action)
        raise _map_service_errors(error) from error

    payload = {"id": str(role_id), "status": "archived"}
    await idem.store(auth.session.identity_id, key, 200, payload)
    return JSONResponse(status_code=200, content=payload)


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
    idem = get_idempotency(request)
    replayed = await idem.replay(auth.session.identity_id, key)
    if replayed is not None:
        return JSONResponse(status_code=replayed["status_code"], content=replayed["body"])

    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        async with engine.begin() as connection:
            service = RbacService(connection)
            result = await service.set_role_permissions(
                auth.principal,
                role_id,
                expected_version,
                body.permissions,
                correlation_id_var.get(),
            )
    except (
        AuthorizationDeniedError,
        ScopedResourceHiddenError,
        PreconditionFailedError,
        InvariantViolationError,
    ) as error:
        if isinstance(error, AuthorizationDeniedError):
            await _audit_denial(request, auth, error.audit_action)
        raise _map_service_errors(error) from error

    payload = {"id": str(result["id"]), "version": result["version"]}
    await idem.store(auth.session.identity_id, key, 200, payload)
    response = JSONResponse(status_code=200, content=payload)
    response.headers["ETag"] = _role_etag(result["version"])
    return response


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
    idem = get_idempotency(request)
    replayed = await idem.replay(auth.session.identity_id, key)
    if replayed is not None:
        return JSONResponse(status_code=replayed["status_code"], content=replayed["body"])

    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        async with engine.begin() as connection:
            service = RbacService(connection)
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
    except (
        AuthorizationDeniedError,
        ScopedResourceHiddenError,
        PreconditionFailedError,
        InvariantViolationError,
    ) as error:
        if isinstance(error, AuthorizationDeniedError):
            await _audit_denial(request, auth, error.audit_action)
        raise _map_service_errors(error) from error

    payload = {"id": str(grant_id)}
    await idem.store(auth.session.identity_id, key, 201, payload)
    return JSONResponse(status_code=201, content=payload)


@router.delete("/grants/{grant_id}")
async def revoke_grant(
    request: Request,
    grant_id: uuid.UUID,
    auth: AuthContext = Depends(require_csrf),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    key = _require_idempotency_key(idempotency_key)
    idem = get_idempotency(request)
    replayed = await idem.replay(auth.session.identity_id, key)
    if replayed is not None:
        return JSONResponse(status_code=replayed["status_code"], content=replayed["body"])

    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        async with engine.begin() as connection:
            service = RbacService(connection)
            await service.revoke_grant(auth.principal, grant_id, correlation_id_var.get())
    except (
        AuthorizationDeniedError,
        ScopedResourceHiddenError,
        PreconditionFailedError,
        InvariantViolationError,
    ) as error:
        if isinstance(error, AuthorizationDeniedError):
            await _audit_denial(request, auth, error.audit_action)
        raise _map_service_errors(error) from error

    payload = {"id": str(grant_id), "status": "revoked"}
    await idem.store(auth.session.identity_id, key, 200, payload)
    return JSONResponse(status_code=200, content=payload)
