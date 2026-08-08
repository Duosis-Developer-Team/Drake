"""Incident reads: scope-filtered, bounded, and free of provider material.

Two rules shape every query here.

**Scope filtering happens in SQL, before counting or paging.** A caller who
cannot see a service cannot learn it exists from a total, a cursor, or the
absence of a row — the same contract the catalog established in ADR-0014.

**Filters are an allowlist.** Project, environment, service, state,
severity and a bounded time window. There is no field through which a
caller supplies SQL, a regex, or a query expression, so there is nothing to
sanitize.
"""

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.catalog.authz import visible_scope_ids
from drake_api.rbac.service import Principal

# Read authority for incidents follows the service they belong to: an
# incident is a statement about a service's health, so seeing one is the
# same right as seeing that service's health.
INCIDENT_READ_PERMISSION = "environment.view"
INCIDENT_ACK_PERMISSION = "incident.ack"
INCIDENT_ASSIGN_PERMISSION = "incident.assign"

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 25
MAX_EVENTS = 200
MAX_TRANSITIONS = 100
DEFAULT_TRANSITIONS = 25

INCIDENT_STATES = frozenset({"open", "acknowledged", "resolved"})
INCIDENT_SEVERITIES = frozenset({"critical", "high"})
INCIDENT_SOURCES = frozenset({"service_health", "protection", "alert"})
INCIDENT_PRIORITIES = frozenset({"P1", "P2", "P3", "P4"})

# Fixed windows for "opened within". A free-form range would be another
# input to bound; a short list is simply bounded.
OPENED_WINDOWS: dict[str, int] = {
    "24h": 86_400,
    "7d": 604_800,
    "30d": 2_592_000,
}


class FilterError(ValueError):
    """A filter value outside the allowlist (422)."""


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    """Never let an empty visibility set become an unfiltered query."""
    return list(ids) or [uuid.UUID(int=0)]


def encode_cursor(opened_at: datetime, incident_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([opened_at.isoformat(), str(incident_id)]).encode()
    ).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        opened_raw, id_raw = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(str(opened_raw)), uuid.UUID(str(id_raw))
    except (binascii.Error, ValueError, TypeError, IndexError) as error:
        raise FilterError("invalid cursor") from error


def _row_summary(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "state": row[1],
        "severity": row[2],
        "title": row[3],
        "primary_reason": row[4],
        "opened_at": row[5].isoformat(),
        "last_critical_at": row[6].isoformat(),
        "acknowledged_at": row[7].isoformat() if row[7] else None,
        "resolved_at": row[8].isoformat() if row[8] else None,
        "version": row[9],
        "project_key": row[10],
        "environment_key": row[11],
        "service_key": row[12],
        "environment_service_id": str(row[13]) if row[13] else None,
        # `null` for an incident that is genuinely not about one workload.
        # An empty object would read as "a workload with no name".
        "binding": (
            None
            if row[14] is None
            else {
                "id": str(row[14]),
                "namespace": row[15],
                "workload_kind": row[16],
                "workload_name": row[17],
                "cluster_ref": row[18],
            }
        ),
        "source": row[22],
        "priority": row[23],
        "owner_team": row[24],
        "assignee": (
            None
            if row[25] is None
            else {
                "identity_id": str(row[25]),
                "assigned_at": row[26].isoformat() if row[26] else None,
                "display_name": row[27],
            }
        ),
        # An alert clearing is evidence the CONDITION stopped, not that
        # anyone handled it. Kept distinct from `resolved_at`.
        "mitigated_at": row[29].isoformat() if row[29] else None,
        # The current verdict, so a list can show "still critical" next to
        # "opened 40 minutes ago" without a second round trip.
        "current_health": (
            None
            if row[19] is None
            else {
                "status": row[19],
                "reasons": list(row[20] or ()),
                "last_observed_at": row[21].isoformat() if row[21] else None,
            }
        ),
    }


_SUMMARY_COLUMNS = """
    i.id, i.state, i.severity, i.title, i.primary_reason,
    i.opened_at, i.last_critical_at, i.acknowledged_at, i.resolved_at, i.version,
    p.project_key, e.environment_key, sd.service_key, i.environment_service_id,
    b.id, b.namespace, b.workload_kind, b.workload_name, c.cluster_ref,
    hs.current_status, hs.current_reasons, hs.last_observed_at,
    i.source, i.priority, i.owner_team, i.assigned_identity_id, i.assigned_at,
    ai.display_name, ai.email, i.mitigated_at
"""

# Every join below is OUTER. An incident no longer requires a workload
# binding: a protection or project-level signal is a real problem, and
# dropping it from the list because it had no pod would be silence, not
# safety.
_SUMMARY_JOINS = """
    FROM incidents i
    LEFT JOIN environment_services es ON es.id = i.environment_service_id
    JOIN projects p ON p.id = i.project_id
    JOIN environments e ON e.id = i.environment_id
    LEFT JOIN service_definitions sd ON sd.id = i.service_id
    LEFT JOIN service_workload_bindings b ON b.id = i.binding_id
    LEFT JOIN clusters c ON c.id = b.cluster_id
    LEFT JOIN service_health_state hs ON hs.binding_id = i.binding_id
    LEFT JOIN identities ai ON ai.id = i.assigned_identity_id
"""


async def list_incidents(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    environment_service_id: uuid.UUID | None = None,
    state: str | None = None,
    severity: str | None = None,
    opened_within: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, Any]:
    """One bounded page, newest first, filtered to what the caller may see."""
    if state is not None and state not in INCIDENT_STATES:
        raise FilterError("unknown incident state")
    if severity is not None and severity not in INCIDENT_SEVERITIES:
        raise FilterError("unknown severity")
    if opened_within is not None and opened_within not in OPENED_WINDOWS:
        raise FilterError("unsupported time window")

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    visible = await visible_scope_ids(connection, principal, INCIDENT_READ_PERMISSION)

    # A service-scoped incident is visible through its service scope; a
    # project-level one through its project's. Both are checked in SQL
    # before any count, page or cursor.
    filters = ["COALESCE(es.scope_id, p.scope_id) = ANY(:visible)"]
    params: dict[str, Any] = {"visible": _sentinel(visible), "limit": limit + 1}
    if project_id is not None:
        filters.append("i.project_id = :project")
        params["project"] = project_id
    if environment_id is not None:
        filters.append("i.environment_id = :environment")
        params["environment"] = environment_id
    if environment_service_id is not None:
        filters.append("i.environment_service_id = :service")
        params["service"] = environment_service_id
    if state is not None:
        filters.append("i.state = :state")
        params["state"] = state
    if severity is not None:
        filters.append("i.severity = :severity")
        params["severity"] = severity
    if opened_within is not None:
        # The window is measured by the database clock, so a client with a
        # skewed clock cannot widen or narrow what it is allowed to see.
        filters.append("i.opened_at >= now() - CAST(:window AS interval)")
        params["window"] = f"{OPENED_WINDOWS[opened_within]} seconds"
    if cursor:
        # Keyset pagination on (opened_at, id): stable under inserts, and it
        # cannot skip or repeat a row the way an offset can.
        opened_at, last_id = decode_cursor(cursor)
        filters.append("(i.opened_at, i.id) < (:cursor_at, :cursor_id)")
        params["cursor_at"] = opened_at
        params["cursor_id"] = last_id

    where = " AND ".join(filters)
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT {_SUMMARY_COLUMNS}
                {_SUMMARY_JOINS}
                WHERE {where}
                ORDER BY i.opened_at DESC, i.id DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).all()

    page, next_cursor = rows[:limit], None
    if len(rows) > limit and page:
        next_cursor = encode_cursor(page[-1][5], page[-1][0])

    # The count is computed from the SAME scope-filtered query, so it can
    # never reveal rows the caller may not see.
    total = (
        await connection.execute(
            text(
                f"""
                SELECT count(*)
                {_SUMMARY_JOINS}
                WHERE {" AND ".join(f for f in filters if not f.startswith("(i.opened_at, i.id)"))}
                """
            ),
            {k: v for k, v in params.items() if k not in ("limit", "cursor_at", "cursor_id")},
        )
    ).scalar_one()

    return {
        "items": [_row_summary(row) for row in page],
        "next_cursor": next_cursor,
        "total": total,
        "limit": limit,
    }


async def get_incident(
    connection: AsyncConnection, principal: Principal, incident_id: uuid.UUID
) -> dict[str, Any] | None:
    """One incident, or None — which the router turns into the same 404 an
    unknown id produces, so neither answers "does this exist"."""
    visible = await visible_scope_ids(connection, principal, INCIDENT_READ_PERMISSION)
    row = (
        await connection.execute(
            text(
                f"""
                SELECT {_SUMMARY_COLUMNS},
                       i.opening_reasons, i.binding_revision, i.resolution_source,
                       ack.display_name, ack.email, i.project_id, i.environment_id
                {_SUMMARY_JOINS}
                LEFT JOIN identities ack ON ack.id = i.acknowledged_by
                WHERE i.id = :id AND COALESCE(es.scope_id, p.scope_id) = ANY(:visible)
                """
            ),
            {"id": incident_id, "visible": _sentinel(visible)},
        )
    ).first()
    if row is None:
        return None

    summary = _row_summary(row)
    return {
        **summary,
        "opening_reasons": list(row[30] or ()),
        "binding_revision": row[31],
        "resolution_source": row[32],
        # Enough to say who acknowledged, and nothing more about them.
        "acknowledged_by": (
            None if row[33] is None else {"display_name": row[33], "email": row[34]}
        ),
        "project_id": str(row[35]),
        "environment_id": str(row[36]),
    }


async def list_incident_events(
    connection: AsyncConnection, principal: Principal, incident_id: uuid.UUID
) -> list[dict[str, Any]] | None:
    """The immutable timeline, oldest first.

    Visibility is re-checked here rather than assumed from a prior call:
    an endpoint that trusts its caller to have checked is an endpoint that
    will eventually be called by one that did not.
    """
    visible = await visible_scope_ids(connection, principal, INCIDENT_READ_PERMISSION)
    allowed = (
        await connection.execute(
            text(
                """
                SELECT 1 FROM incidents i
                LEFT JOIN environment_services es ON es.id = i.environment_service_id
                JOIN projects p ON p.id = i.project_id
                WHERE i.id = :id AND COALESCE(es.scope_id, p.scope_id) = ANY(:visible)
                """
            ),
            {"id": incident_id, "visible": _sentinel(visible)},
        )
    ).first()
    if allowed is None:
        return None

    rows = (
        await connection.execute(
            text(
                """
                SELECT ev.event_type, ev.occurred_at, ev.detail, ident.display_name
                FROM incident_events ev
                LEFT JOIN identities ident ON ident.id = ev.actor_identity_id
                WHERE ev.incident_id = :id
                ORDER BY ev.occurred_at, ev.id
                LIMIT :limit
                """
            ),
            {"id": incident_id, "limit": MAX_EVENTS},
        )
    ).all()
    return [
        {
            "event_type": row[0],
            "occurred_at": row[1].isoformat(),
            "detail": row[2] or {},
            "actor": row[3],
        }
        for row in rows
    ]


async def list_health_transitions(
    connection: AsyncConnection,
    principal: Principal,
    binding_id: uuid.UUID,
    limit: int = DEFAULT_TRANSITIONS,
) -> list[dict[str, Any]] | None:
    """Status changes for one binding, newest first.

    Transitions only — no metric payloads are copied here, so this stays a
    history of decisions rather than a second, diverging copy of the data
    they were made from.
    """
    limit = max(1, min(limit, MAX_TRANSITIONS))
    visible = await visible_scope_ids(connection, principal, INCIDENT_READ_PERMISSION)
    rows = (
        await connection.execute(
            text(
                """
                SELECT t.previous_status, t.new_status, t.reasons,
                       t.computed_at, t.recorded_at, t.binding_revision
                FROM service_health_transitions t
                JOIN environment_services es ON es.id = t.environment_service_id
                WHERE t.binding_id = :binding AND es.scope_id = ANY(:visible)
                ORDER BY t.computed_at DESC, t.id DESC
                LIMIT :limit
                """
            ),
            {"binding": binding_id, "visible": _sentinel(visible), "limit": limit},
        )
    ).all()
    return [
        {
            "previous_status": row[0],
            "new_status": row[1],
            "reasons": list(row[2] or ()),
            "computed_at": row[3].isoformat(),
            "recorded_at": row[4].isoformat(),
            "binding_revision": row[5],
        }
        for row in rows
    ]


async def can_acknowledge(
    connection: AsyncConnection, principal: Principal, incident_id: uuid.UUID
) -> bool:
    """Whether this principal may acknowledge this incident.

    Read authority is not write authority: someone who can see an incident
    still needs `incident.ack` in a scope that covers it.
    """
    manageable = await visible_scope_ids(connection, principal, INCIDENT_ACK_PERMISSION)
    row = (
        await connection.execute(
            text(
                """
                SELECT 1 FROM incidents i
                LEFT JOIN environment_services es ON es.id = i.environment_service_id
                JOIN projects p ON p.id = i.project_id
                WHERE i.id = :id AND COALESCE(es.scope_id, p.scope_id) = ANY(:visible)
                """
            ),
            {"id": incident_id, "visible": _sentinel(manageable)},
        )
    ).first()
    return row is not None


async def can_assign(
    connection: AsyncConnection, principal: Principal, incident_id: uuid.UUID
) -> bool:
    """Whether this principal may change this incident's owner."""
    manageable = await visible_scope_ids(connection, principal, INCIDENT_ASSIGN_PERMISSION)
    row = (
        await connection.execute(
            text(
                """
                SELECT 1 FROM incidents i
                LEFT JOIN environment_services es ON es.id = i.environment_service_id
                JOIN projects p ON p.id = i.project_id
                WHERE i.id = :id AND COALESCE(es.scope_id, p.scope_id) = ANY(:visible)
                """
            ),
            {"id": incident_id, "visible": _sentinel(manageable)},
        )
    ).first()
    return row is not None


async def assignee_is_eligible(
    connection: AsyncConnection, incident_id: uuid.UUID, identity_id: uuid.UUID
) -> bool:
    """Whether the proposed owner can actually see what they are being given.

    Assigning an incident to someone with no access to its project would
    produce an owner who cannot open the page, which is indistinguishable
    from nobody owning it — except that it looks handled.
    """
    row = (
        await connection.execute(
            text(
                """
                WITH RECURSIVE target AS (
                    SELECT COALESCE(es.scope_id, p.scope_id) AS scope_id
                    FROM incidents i
                    LEFT JOIN environment_services es ON es.id = i.environment_service_id
                    JOIN projects p ON p.id = i.project_id
                    WHERE i.id = :incident
                ),
                chain AS (
                    SELECT id, parent_id FROM scopes
                    WHERE id = (SELECT scope_id FROM target)
                    UNION ALL
                    SELECT s.id, s.parent_id FROM scopes s JOIN chain ON s.id = chain.parent_id
                )
                SELECT 1
                FROM grants g
                JOIN role_permissions rp ON rp.role_id = g.role_id
                WHERE g.identity_id = :identity
                  AND g.revoked_at IS NULL
                  AND rp.permission_key = :permission
                  AND g.scope_id IN (SELECT id FROM chain)
                LIMIT 1
                """
            ),
            {
                "incident": incident_id,
                "identity": identity_id,
                "permission": INCIDENT_READ_PERMISSION,
            },
        )
    ).first()
    return row is not None
