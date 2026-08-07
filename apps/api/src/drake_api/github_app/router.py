"""Scoped GitHub integration management API.

Same contract discipline as the rest of Drake: reads are gated by the
attached scope's read permission, writes require `integration.manage`
plus CSRF, out-of-scope anything is a uniform 404, and no response ever
carries a private key, JWT, installation token, webhook secret, or a
config reference.
"""

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.catalog.authz import INTEGRATION_READ_PERMISSION, visible_scope_ids
from drake_api.db import get_engine
from drake_api.github_app import catalog, onboarding, policy, service
from drake_api.github_app.client import GitHubError, error_code
from drake_api.settings import Settings

router = APIRouter(prefix="/v1/integrations/github", tags=["github"])

_MAX_PAGE = 100
_DEFAULT_PAGE = 25
MANAGE_PERMISSION = "integration.manage"

# Inverted once at import time so read visibility can never drift from the
# single INTEGRATION_READ_PERMISSION mapping.
_SCOPE_TYPES_BY_PERMISSION: dict[str, list[str]] = {}
for _scope_type, _permission in INTEGRATION_READ_PERMISSION.items():
    _SCOPE_TYPES_BY_PERMISSION.setdefault(_permission, []).append(_scope_type)


def _as_of() -> str:
    return datetime.now(UTC).isoformat()


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


def _encode_cursor(*parts: str) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(parts)).encode()).decode()


def _decode_cursor(cursor: str, arity: int) -> list[str]:
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(parts, list) or len(parts) != arity:
            raise ValueError
        return [str(part) for part in parts]
    except (binascii.Error, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="invalid cursor") from error


async def _readable_scope_ids(connection: AsyncConnection, auth: AuthContext) -> list[uuid.UUID]:
    """Every scope the caller may READ integration data in."""
    readable: set[uuid.UUID] = set()
    for permission in _SCOPE_TYPES_BY_PERMISSION:
        readable |= await visible_scope_ids(connection, auth.principal, permission)
    # The sentinel keeps an empty set from degenerating into "match all".
    return list(readable) or [uuid.UUID(int=0)]


async def _manageable_scope_ids(connection: AsyncConnection, auth: AuthContext) -> list[uuid.UUID]:
    manageable = await visible_scope_ids(connection, auth.principal, MANAGE_PERMISSION)
    return list(manageable) or [uuid.UUID(int=0)]


def _repository_payload(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "provider": row[1],
        "external_id": row[2],
        "owner_login": row[3],
        "name": row[4],
        "full_name": row[5],
        "private": row[6],
        "visibility": row[7],
        "archived": row[8],
        "disabled": row[9],
        "default_branch": row[10],
        "onboarding_state": row[11],
        "state_reason": row[12],
        "security_gate": row[13],
        "security_gate_reason": catalog.gate_reason_for(str(row[5])) if row[13] else "",
        "access_state": row[14],
        "last_reconciled_at": row[15].isoformat() if row[15] else None,
        "last_policy_evaluated_at": row[16].isoformat() if row[16] else None,
        "last_error_code": row[17],
        "installation_external_id": row[18],
        "as_of": _as_of(),
    }


_REPOSITORY_COLUMNS = """
    r.id, r.provider, r.external_id, r.owner_login, r.name, r.full_name,
    r.private, r.visibility, r.archived, r.disabled, r.default_branch,
    r.onboarding_state, r.state_reason, r.security_gate, r.access_state,
    r.last_reconciled_at, r.last_policy_evaluated_at, r.last_error_code,
    i.external_id
"""


@router.get("/status")
async def integration_status(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Configuration readiness — never the values themselves."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _readable_scope_ids(connection, auth)
        counts = (
            await connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM github_installations
                         WHERE scope_id = ANY(:scopes)),
                        (SELECT count(*) FROM github_repositories
                         WHERE scope_id = ANY(:scopes)),
                        (SELECT count(*) FROM github_repositories
                         WHERE scope_id = ANY(:scopes) AND onboarding_state = 'blocked')
                    """
                ),
                {"scopes": scopes},
            )
        ).one()

    # Presence of a reference — NEVER the reference name or its value.
    configured = bool(
        settings.github_app_enabled
        and settings.github_app_private_key_file
        and settings.github_webhook_secret_file
        and (settings.github_app_client_id or settings.github_app_id)
    )
    missing: list[str] = []
    if not settings.github_app_enabled:
        missing.append("feature_disabled")
    if not (settings.github_app_client_id or settings.github_app_id):
        missing.append("app_identity")
    if not settings.github_app_private_key_file:
        missing.append("private_key_reference")
    if not settings.github_webhook_secret_file:
        missing.append("webhook_secret_reference")

    return {
        "configuration_state": "configured" if configured else "not_configured",
        "missing_operator_inputs": missing,
        "installations": int(counts[0]),
        "repositories": int(counts[1]),
        "blocked_repositories": int(counts[2]),
        "supported_events": sorted(
            __import__(
                "drake_api.github_app.webhook", fromlist=["SUPPORTED_EVENTS"]
            ).SUPPORTED_EVENTS
        ),
        "policy_profiles": sorted(policy.PROFILES),
        "as_of": _as_of(),
    }


@router.get("/installations")
async def list_installations(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _readable_scope_ids(connection, auth)
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT id, external_id, account_login, account_type, app_slug,
                           repository_selection, granted_permissions, subscribed_events,
                           state, suspended_at, last_reconciled_at, last_error_code
                    FROM github_installations
                    WHERE scope_id = ANY(:scopes)
                    ORDER BY account_login, external_id
                    LIMIT :limit
                    """
                ),
                {"scopes": scopes, "limit": _MAX_PAGE},
            )
        ).all()
    return {
        "installations": [
            {
                "id": str(row[0]),
                "external_id": row[1],
                "account_login": row[2],
                "account_type": row[3],
                "app_slug": row[4],
                "repository_selection": row[5],
                "granted_permissions": row[6],
                "subscribed_events": row[7],
                "state": row[8],
                "suspended_at": row[9].isoformat() if row[9] else None,
                "last_reconciled_at": row[10].isoformat() if row[10] else None,
                "last_error_code": row[11],
            }
            for row in rows
        ],
        "as_of": _as_of(),
    }


@router.get("/installations/{installation_id}")
async def get_installation(
    request: Request, installation_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _readable_scope_ids(connection, auth)
        row = (
            await connection.execute(
                text(
                    """
                    SELECT id, external_id, account_login, account_type, app_slug,
                           repository_selection, granted_permissions, subscribed_events,
                           state, suspended_at, last_reconciled_at, last_error_code
                    FROM github_installations
                    WHERE id = :id AND scope_id = ANY(:scopes)
                    """
                ),
                {"id": installation_id, "scopes": scopes},
            )
        ).first()
        if row is None:
            raise _not_found()
        repositories = (
            await connection.execute(
                text("SELECT count(*) FROM github_repositories WHERE installation_id = :id"),
                {"id": installation_id},
            )
        ).scalar_one()
    return {
        "id": str(row[0]),
        "external_id": row[1],
        "account_login": row[2],
        "account_type": row[3],
        "app_slug": row[4],
        "repository_selection": row[5],
        "granted_permissions": row[6],
        "subscribed_events": row[7],
        "state": row[8],
        "suspended_at": row[9].isoformat() if row[9] else None,
        "last_reconciled_at": row[10].isoformat() if row[10] else None,
        "last_error_code": row[11],
        "repository_count": int(repositories),
        "as_of": _as_of(),
    }


@router.get("/repositories")
async def list_repositories(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    onboarding_state: str | None = Query(
        default=None, pattern="^(discovered|validating|ready|blocked|degraded|disabled)$"
    ),
    search: str | None = Query(default=None, min_length=2, max_length=64),
    limit: int = Query(default=_DEFAULT_PAGE, ge=1, le=_MAX_PAGE),
    cursor: str | None = None,
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _readable_scope_ids(connection, auth)
        params: dict[str, Any] = {"scopes": scopes, "limit": limit + 1}
        conditions = ["r.scope_id = ANY(:scopes)"]
        if onboarding_state:
            conditions.append("r.onboarding_state = :state")
            params["state"] = onboarding_state
        if search:
            conditions.append("r.full_name ILIKE :term ESCAPE '\\'")
            params["term"] = f"%{search.replace('%', '').replace('_', '')}%"
        if cursor:
            parts = _decode_cursor(cursor, 2)
            conditions.append("(r.full_name, r.id) > (:cursor_name, CAST(:cursor_id AS uuid))")
            params["cursor_name"] = parts[0]
            params["cursor_id"] = parts[1]
        rows = (
            await connection.execute(
                text(
                    f"SELECT {_REPOSITORY_COLUMNS} FROM github_repositories r "  # noqa: S608
                    "JOIN github_installations i ON i.id = r.installation_id "
                    f"WHERE {' AND '.join(conditions)} "
                    "ORDER BY r.full_name, r.id LIMIT :limit"
                ),
                params,
            )
        ).all()
    page = rows[:limit]
    next_cursor = (
        _encode_cursor(str(page[-1][5]), str(page[-1][0])) if len(rows) > limit and page else None
    )
    return {
        "repositories": [_repository_payload(row) for row in page],
        "next_cursor": next_cursor,
        "as_of": _as_of(),
    }


async def _load_repository(
    connection: AsyncConnection, repository_id: uuid.UUID, scopes: list[uuid.UUID]
) -> Any:
    row = (
        await connection.execute(
            text(
                f"SELECT {_REPOSITORY_COLUMNS} FROM github_repositories r "  # noqa: S608
                "JOIN github_installations i ON i.id = r.installation_id "
                "WHERE r.id = :id AND r.scope_id = ANY(:scopes)"
            ),
            {"id": repository_id, "scopes": scopes},
        )
    ).first()
    if row is None:
        raise _not_found()
    return row


@router.get("/repositories/{repository_id}")
async def get_repository(
    request: Request, repository_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _readable_scope_ids(connection, auth)
        row = await _load_repository(connection, repository_id, scopes)
    return _repository_payload(row)


@router.get("/repositories/{repository_id}/policy")
async def latest_policy(
    request: Request, repository_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _readable_scope_ids(connection, auth)
        await _load_repository(connection, repository_id, scopes)
        row = (
            await connection.execute(
                text(
                    """
                    SELECT id, profile, overall, blocking_count, unknown_count, results,
                           evidence_digest, dry_run, evaluated_at
                    FROM github_policy_evaluations
                    WHERE repository_id = :id
                    ORDER BY evaluated_at DESC, id DESC LIMIT 1
                    """
                ),
                {"id": repository_id},
            )
        ).first()
    if row is None:
        return {
            "repository_id": str(repository_id),
            "state": "never_evaluated",
            "results": [],
            "as_of": _as_of(),
        }
    return {
        "repository_id": str(repository_id),
        "state": "evaluated",
        "id": str(row[0]),
        "profile": row[1],
        "overall": row[2],
        "blocking_count": int(row[3]),
        "unknown_count": int(row[4]),
        "results": row[5],
        "evidence_digest": row[6],
        "dry_run": row[7],
        "evaluated_at": row[8].isoformat(),
        "as_of": _as_of(),
    }


@router.get("/repositories/{repository_id}/violations")
async def latest_violations(
    request: Request, repository_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    snapshot = await latest_policy(request, repository_id, auth)
    results = snapshot.get("results") or []
    violations = [
        result
        for result in results
        if isinstance(result, dict) and result.get("verdict") in ("fail", "warn", "unknown")
    ]
    return {
        "repository_id": str(repository_id),
        "violations": violations,
        "blocking": [item for item in violations if item.get("blocking")],
        "evaluated_at": snapshot.get("evaluated_at"),
        "as_of": _as_of(),
    }


@router.get("/webhook-deliveries")
async def list_deliveries(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    limit: int = Query(default=_DEFAULT_PAGE, ge=1, le=_MAX_PAGE),
) -> dict[str, Any]:
    """Safe delivery metadata only — never headers, signatures, or bodies."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        manageable = await _manageable_scope_ids(connection, auth)
        if manageable == [uuid.UUID(int=0)]:
            raise HTTPException(status_code=403, detail="not permitted")
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT delivery_id, event_type, status, installation_external_id,
                           repository_external_id, received_at, processed_at
                    FROM github_webhook_deliveries
                    ORDER BY received_at DESC LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        ).all()
    return {
        "deliveries": [
            {
                "delivery_id": row[0],
                "event_type": row[1],
                "status": row[2],
                "installation_external_id": row[3],
                "repository_external_id": row[4],
                "received_at": row[5].isoformat(),
                "processed_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ],
        "as_of": _as_of(),
    }


async def _require_manageable_repository(
    connection: AsyncConnection, repository_id: uuid.UUID, auth: AuthContext
) -> Any:
    """Manage authority AND read visibility, both enforced in SQL.

    An unmanageable or unknown repository is the same uniform 404, so the
    endpoint is not an existence oracle.
    """
    manageable = await _manageable_scope_ids(connection, auth)
    return await _load_repository(connection, repository_id, manageable)


@router.post("/repositories/{repository_id}/reconcile", status_code=202)
async def reconcile_repository(
    request: Request, repository_id: uuid.UUID, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """Trigger a read-only reconciliation + dry-run policy evaluation."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        row = await _require_manageable_repository(connection, repository_id, auth)

    full_name = str(row[5])
    installation_external_id = int(row[18])
    gate = catalog.security_gate_for(full_name)
    if gate:
        # Blocked BEFORE any credential is minted or any call is made.
        async with engine.begin() as connection:
            await service.apply_state(
                connection, repository_id, onboarding.BLOCKED, f"security_gate_{gate}"
            )
        await service._audit(
            engine,
            action="github.repository.reconcile",
            result="denied",
            target_id=str(repository_id),
            metadata={"reason": f"security_gate_{gate}", "full_name": full_name},
            actor_type="user",
            actor_id=str(auth.principal.identity_id),
        )
        raise HTTPException(status_code=409, detail="repository is blocked by a security gate")

    reconciler = getattr(request.app.state, "github_reconciler", None)
    if reconciler is None:
        raise HTTPException(status_code=503, detail="github integration is not configured")

    async with engine.begin() as connection:
        await service.apply_state(
            connection, repository_id, onboarding.VALIDATING, "reconciliation_started"
        )
    try:
        evaluation = await reconciler.evaluate_repository(installation_external_id, full_name)
    except service.SecurityGateBlockedError as blocked:
        raise HTTPException(
            status_code=409, detail="repository is blocked by a security gate"
        ) from blocked
    except GitHubError as error:
        code = error_code(error)
        async with engine.begin() as connection:
            await service.apply_state(
                connection, repository_id, onboarding.DEGRADED, "reconciliation_error"
            )
            await connection.execute(
                text(
                    "UPDATE github_repositories SET last_error_code = :code, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"code": code, "id": repository_id},
            )
        await service._audit(
            engine,
            action="github.repository.reconcile",
            result="failure",
            target_id=str(repository_id),
            metadata={"error_code": code},
            actor_type="user",
            actor_id=str(auth.principal.identity_id),
        )
        raise HTTPException(status_code=503, detail="github is unavailable") from error

    async with engine.begin() as connection:
        await service.store_policy_evaluation(connection, repository_id, evaluation, dry_run=True)
        await connection.execute(
            text(
                "UPDATE github_repositories SET last_reconciled_at = now(), "
                "last_error_code = NULL, updated_at = now() WHERE id = :id"
            ),
            {"id": repository_id},
        )
        await service.apply_state(connection, repository_id, onboarding.READY, "reconciled")

    await service._audit(
        engine,
        action="github.policy.evaluated",
        result="success",
        target_id=str(repository_id),
        metadata={
            "overall": evaluation.overall,
            "blocking": evaluation.blocking_count,
            "unknown": evaluation.unknown_count,
            "dry_run": True,
        },
        actor_type="user",
        actor_id=str(auth.principal.identity_id),
    )
    if evaluation.blocking_count:
        await service._audit(
            engine,
            action="github.policy.blocking",
            result="failure",
            target_id=str(repository_id),
            metadata={"blocking": evaluation.blocking_count, "overall": evaluation.overall},
        )
    return {
        "repository_id": str(repository_id),
        "overall": evaluation.overall,
        "blocking_count": evaluation.blocking_count,
        "unknown_count": evaluation.unknown_count,
        "dry_run": True,
        "as_of": _as_of(),
    }
