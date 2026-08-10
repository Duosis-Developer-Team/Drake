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
        # Alertmanager evidence, attached to the rows it belongs to. Counts
        # and states only — never a payload, a URL, a token, or a label.
        alertmanager = await _alertmanager_evidence(
            connection, [row[10] for row in page if row[0] == "alertmanager"]
        )
    for result, row in zip(results, page, strict=True):
        detail = alertmanager.get(str(row[10]))
        if detail is not None:
            result["alertmanager"] = detail
    return {
        "integrations": results,
        "next_cursor": next_cursor,
        "as_of": datetime.now(UTC).isoformat(),
    }


async def _alertmanager_evidence(
    connection: Any, integration_ids: list[Any]
) -> dict[str, dict[str, Any]]:
    """Delivery, mapping and silence health for one or more Alertmanagers.

    Everything here is a count, a timestamp or a bounded state. There is no
    branch that can return an alert name, a label value, a group key, a
    provider message or an address.

    `base_route_verified` is deliberately `unknown` and stays that way.
    Whether Alertmanager still notifies an independent receiver when Drake
    is unreachable is a fact about a config file Drake does not read and
    must not guess at — claiming `verified` without operator evidence would
    be the single most dangerous wrong answer this screen could give.
    """
    if not integration_ids:
        return {}
    rows = (
        await connection.execute(
            text(
                """
                SELECT d.integration_id,
                       max(d.received_at) AS last_received,
                       count(*) FILTER (WHERE d.outcome <> 'rejected') AS accepted,
                       count(*) FILTER (WHERE d.outcome = 'rejected') AS rejected,
                       coalesce(sum(d.truncated_alerts), 0) AS truncated,
                       coalesce(sum(d.unmapped_count), 0) AS unmapped
                FROM alertmanager_deliveries d
                WHERE d.integration_id = ANY(:ids)
                GROUP BY d.integration_id
                """
            ),
            {"ids": integration_ids},
        )
    ).all()
    silences = (
        await connection.execute(
            text(
                """
                SELECT s.integration_id,
                       count(*) FILTER (WHERE s.state = 'active') AS active,
                       count(*) FILTER (WHERE s.state IN ('pending', 'cancel_pending'))
                           AS pending,
                       count(*) FILTER (WHERE s.state = 'failed') AS failed
                FROM silence_requests s
                WHERE s.integration_id = ANY(:ids)
                GROUP BY s.integration_id
                """
            ),
            {"ids": integration_ids},
        )
    ).all()
    silence_by_id = {str(row[0]): row for row in silences}
    ambiguous = (
        await connection.execute(
            text(
                """
                SELECT integration_id,
                       count(*) FILTER (WHERE mapping_state = 'unmapped') AS unmapped,
                       count(*) FILTER (WHERE mapping_state = 'ambiguous') AS ambiguous
                FROM alert_instances
                WHERE integration_id = ANY(:ids)
                GROUP BY integration_id
                """
            ),
            {"ids": integration_ids},
        )
    ).all()
    mapping_by_id = {str(row[0]): row for row in ambiguous}

    evidence: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row[0])
        silence_row = silence_by_id.get(key)
        mapping_row = mapping_by_id.get(key)
        evidence[key] = {
            "webhook_last_received_at": row[1].isoformat() if row[1] else None,
            "deliveries_accepted": int(row[2]),
            "deliveries_rejected": int(row[3]),
            # A truncated delivery means Alertmanager dropped alerts before
            # Drake saw them. Shown as partial rather than silently absorbed.
            "truncated_payloads": int(row[4]),
            "alerts_unmapped": int(mapping_row[1]) if mapping_row else 0,
            "alerts_ambiguous": int(mapping_row[2]) if mapping_row else 0,
            "silences_active": int(silence_row[1]) if silence_row else 0,
            "silences_pending": int(silence_row[2]) if silence_row else 0,
            "silence_worker_failures": int(silence_row[3]) if silence_row else 0,
            # verified | unknown | invalid. Only an operator attestation can
            # move this off `unknown`; Drake will not assume its own absence
            # is survivable.
            "base_route_verified": "unknown",
            "base_route_note": (
                "Drake cannot see Alertmanager's routing tree. Independent base "
                "notification is verified by reviewing the route config, not by Drake."
            ),
        }
    return evidence
