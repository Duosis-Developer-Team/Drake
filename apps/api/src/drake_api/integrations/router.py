"""Integration health projection (read-only in Sprint 2).

Visibility follows the attached scope's read permission. Responses never
contain config references, provider errors, or credentials — only safe
states, timestamps, and a bounded error code.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.catalog.authz import INTEGRATION_READ_PERMISSION, visible_scope_ids
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["integrations"])


@router.get("/integrations/health")
async def integration_health(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        visible_by_permission: dict[str, set[uuid.UUID]] = {}
        for permission in set(INTEGRATION_READ_PERMISSION.values()):
            visible_by_permission[permission] = await visible_scope_ids(
                connection, auth.principal, permission
            )

        rows = (
            await connection.execute(
                text(
                    """
                    SELECT i.integration_type, s.scope_type, s.external_ref,
                           i.configuration_state, i.observed_state,
                           i.last_sync_attempt_at, i.last_success_at,
                           i.last_error_code, i.schema_version, i.updated_at, s.id
                    FROM integrations i JOIN scopes s ON s.id = i.scope_id
                    WHERE i.lifecycle = 'active'
                    ORDER BY s.external_ref, i.integration_type
                    """
                )
            )
        ).all()

        results = []
        for row in rows:
            required = INTEGRATION_READ_PERMISSION.get(row[1])
            if required is None or row[10] not in visible_by_permission[required]:
                continue  # silently absent: no cross-scope existence signal
            results.append(
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
            )
    return {"integrations": results, "as_of": datetime.now(UTC).isoformat()}
