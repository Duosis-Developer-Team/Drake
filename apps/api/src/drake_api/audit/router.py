"""Audit query API: cursor-paginated, scope-filtered, audit.view-gated.

The caller sees only events whose scope falls inside the subtree(s) where
they hold ``audit.view`` — plus unscoped platform events when they hold
``audit.view`` at the organization root. No offsets, no unbounded exports.
"""

import base64
import binascii
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.db import get_engine
from drake_api.rbac.service import RbacService
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["audit"])

_MAX_PAGE = 100


def _encode_cursor(occurred_at_iso: str, event_id: str) -> str:
    raw = json.dumps({"t": occurred_at_iso, "id": event_id}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return str(payload["t"]), str(payload["id"])
    except (binascii.Error, ValueError, KeyError) as error:
        raise HTTPException(status_code=422, detail="invalid cursor") from error


@router.get("/audit-events")
async def list_audit_events(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    limit: int = Query(default=50, ge=1, le=_MAX_PAGE),
    cursor: str | None = None,
    scope_type: str | None = None,
    scope_ref: str | None = None,
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)

    async with engine.connect() as connection:
        service = RbacService(connection)
        grants = await service.effective_grants(auth.principal)
        audit_scopes = [g for g in grants if g.permission == "audit.view"]
        if not audit_scopes:
            raise HTTPException(status_code=403, detail="not permitted")

        root = await service.scopes.organization_root()
        has_root_view = any(g.scope_id == root.id for g in audit_scopes)

        # Visible (scope_type, scope_ref) pairs: subtree of every audit.view scope.
        rows = (
            await connection.execute(
                text(
                    """
                    WITH RECURSIVE visible AS (
                        SELECT id, scope_type, external_ref FROM scopes
                        WHERE id = ANY(:roots)
                        UNION
                        SELECT s.id, s.scope_type, s.external_ref
                        FROM scopes s JOIN visible v ON s.parent_id = v.id
                    )
                    SELECT scope_type, external_ref FROM visible
                    """
                ),
                {"roots": [g.scope_id for g in audit_scopes]},
            )
        ).all()
        visible_pairs = {(row[0], row[1]) for row in rows}

        if scope_type is not None or scope_ref is not None:
            if scope_type is None or scope_ref is None:
                raise HTTPException(
                    status_code=422, detail="scope_type and scope_ref must be used together"
                )
            if (scope_type, scope_ref) not in visible_pairs:
                # Consistent 404: filtering outside your visibility reveals nothing.
                raise HTTPException(status_code=404, detail="not found")

        conditions = ["1=1"]
        params: dict[str, Any] = {"limit": limit + 1}

        if scope_type is not None:
            conditions.append("e.scope_type = :scope_type AND e.scope_id = :scope_ref")
            params["scope_type"] = scope_type
            params["scope_ref"] = scope_ref
        elif not has_root_view:
            pair_clauses = []
            for index, (visible_type, visible_ref) in enumerate(sorted(visible_pairs)):
                pair_clauses.append(f"(e.scope_type = :vt{index} AND e.scope_id = :vr{index})")
                params[f"vt{index}"] = visible_type
                params[f"vr{index}"] = visible_ref
            conditions.append("(" + " OR ".join(pair_clauses) + ")" if pair_clauses else "1=0")

        if cursor:
            cursor_time, cursor_id = _decode_cursor(cursor)
            conditions.append(
                "(e.occurred_at, e.id) < (CAST(:cursor_time AS timestamptz), "
                "CAST(:cursor_id AS uuid))"
            )
            params["cursor_time"] = cursor_time
            params["cursor_id"] = cursor_id

        query = text(
            f"""
            SELECT e.id, e.occurred_at, e.actor_type, e.actor_id, e.action,
                   e.scope_type, e.scope_id, e.target_type, e.target_id,
                   e.result, e.correlation_id
            FROM audit_events e
            WHERE {" AND ".join(conditions)}
            ORDER BY e.occurred_at DESC, e.id DESC
            LIMIT :limit
            """  # noqa: S608 - conditions are built from fixed fragments only
        )
        result_rows = (await connection.execute(query, params)).all()

    has_more = len(result_rows) > limit
    page = result_rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last[1].isoformat(), str(last[0]))

    return {
        "events": [
            {
                "id": str(row[0]),
                "occurred_at": row[1].isoformat(),
                "actor_type": row[2],
                "actor_id": row[3],
                "action": row[4],
                "scope_type": row[5],
                "scope_ref": row[6],
                "target_type": row[7],
                "target_id": row[8],
                "result": row[9],
                "correlation_id": row[10],
            }
            for row in page
        ],
        "next_cursor": next_cursor,
    }
