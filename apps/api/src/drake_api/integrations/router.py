"""Integration health projection (read-only in Sprint 2).

Visibility follows the attached scope's read permission and is enforced at
the SQL boundary: only authorized rows ever leave the database, before any
filter, ordering, limit, or cursor is applied. Responses never contain
config references, provider errors, or credentials — only safe states,
timestamps, and a bounded error code.
"""

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.catalog.authz import INTEGRATION_READ_PERMISSION, visible_scope_ids
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["integrations"])

_MAX_PAGE = 100
_DEFAULT_PAGE = 25

# Scope types grouped by the permission that guards them — derived from the
# single INTEGRATION_READ_PERMISSION mapping so the two can never diverge.
_SCOPE_TYPES_BY_PERMISSION: dict[str, list[str]] = {}
for _scope_type, _permission in INTEGRATION_READ_PERMISSION.items():
    _SCOPE_TYPES_BY_PERMISSION.setdefault(_permission, []).append(_scope_type)


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


@router.get("/integrations/health")
async def integration_health(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    limit: int = Query(default=_DEFAULT_PAGE, ge=1, le=_MAX_PAGE),
    cursor: str | None = None,
    integration_type: str | None = Query(default=None, pattern="^[a-z0-9][a-z0-9_.-]{0,63}$"),
    configuration_state: str | None = Query(default=None, pattern="^(not_configured|configured)$"),
    observed_state: str | None = Query(
        default=None, pattern="^(unknown|not_configured|stale|degraded|ok)$"
    ),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        # Authorization boundary: per-permission visible scope sets become
        # SQL predicates — unauthorized rows never leave the database and
        # can never influence ordering, limits, or cursors.
        authz_clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit + 1}
        for permission, scope_types in sorted(_SCOPE_TYPES_BY_PERMISSION.items()):
            visible = await visible_scope_ids(connection, auth.principal, permission)
            slug = permission.replace(".", "_")
            authz_clauses.append(
                f"(s.scope_type = ANY(:types_{slug}) AND s.id = ANY(:scopes_{slug}))"
            )
            params[f"types_{slug}"] = scope_types
            params[f"scopes_{slug}"] = list(visible) or [uuid.UUID(int=0)]

        conditions = ["i.lifecycle = 'active'", f"({' OR '.join(authz_clauses)})"]
        if integration_type:
            conditions.append("i.integration_type = :integration_type")
            params["integration_type"] = integration_type
        if configuration_state:
            conditions.append("i.configuration_state = :configuration_state")
            params["configuration_state"] = configuration_state
        if observed_state:
            conditions.append("i.observed_state = :observed_state")
            params["observed_state"] = observed_state
        if cursor:
            cursor_ref, cursor_type, cursor_id = _decode_cursor(cursor, 3)
            conditions.append(
                "(s.external_ref, i.integration_type, i.id) > "
                "(:cursor_ref, :cursor_type, CAST(:cursor_id AS uuid))"
            )
            params["cursor_ref"] = cursor_ref
            params["cursor_type"] = cursor_type
            params["cursor_id"] = cursor_id

        rows = (
            await connection.execute(
                text(
                    """
                    SELECT i.integration_type, s.scope_type, s.external_ref,
                           i.configuration_state, i.observed_state,
                           i.last_sync_attempt_at, i.last_success_at,
                           i.last_error_code, i.schema_version, i.updated_at, i.id
                    FROM integrations i JOIN scopes s ON s.id = i.scope_id
                    WHERE {conditions}
                    ORDER BY s.external_ref, i.integration_type, i.id
                    LIMIT :limit
                    """.format(conditions=" AND ".join(conditions))  # noqa: S608
                ),
                params,
            )
        ).all()

        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1][2], page[-1][0], str(page[-1][10]))
            if len(rows) > limit and page
            else None
        )
        results = [
            {
                "integration_type": row[0],
                "scope": {"type": row[1], "ref": row[2]},
                "configuration_state": row[3],
                "observed_state": row[4],
                "last_sync_attempt_at": row[5].isoformat() if row[5] else None,
                "last_success_at": row[6].isoformat() if row[6] else None,
                "last_error_code": row[7],
                "schema_version": row[8],
                "as_of": row[9].isoformat(),
            }
            for row in page
        ]
    return {
        "integrations": results,
        "next_cursor": next_cursor,
        "as_of": datetime.now(UTC).isoformat(),
    }
