"""Notification reads and writes: policies, destinations, inbox, audit.

Three boundaries are enforced here rather than in the router, because the
router is not the only caller a repository ever ends up with:

- **The inbox belongs to its recipient.** Every inbox query filters on the
  authenticated principal's identity id. There is no parameter for a
  recipient, so there is nothing to tamper with.
- **A revoked scope revokes the content.** A notification whose incident
  the reader may no longer see keeps its row — they did receive it — but
  its text and link are withheld. Deleting the row would rewrite history;
  showing the text would leak what a grant change just took away.
- **A webhook target never leaves the server.** Destination reads return a
  key and a display name. There is no column, and no code path, that could
  return a URL or a secret.
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
from drake_api.notifications.model import (
    NOTIFIABLE_EVENTS,
    SEVERITIES,
)
from drake_api.rbac.service import Principal
from drake_api.settings import Settings

NOTIFICATION_READ_PERMISSION = "notification.view"
NOTIFICATION_MANAGE_PERMISSION = "notification.manage"
# Reading an incident behind a notification follows the same right as
# reading the service's health.
INCIDENT_READ_PERMISSION = "environment.view"

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 25
MAX_MARK_READ = 100

# Fixed windows, so "recent" is a choice from a list rather than a range
# someone can widen.
INBOX_WINDOWS: dict[str, int] = {"24h": 86_400, "7d": 604_800, "30d": 2_592_000}

WITHHELD_TITLE = "Notification unavailable"
WITHHELD_BODY = (
    "This notification refers to a service you no longer have access to, so its "
    "details are not shown."
)


class NotificationError(ValueError):
    """A request the server will not carry out (422/404/409 at the router)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    return list(ids) or [uuid.UUID(int=0)]


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([created_at.isoformat(), str(row_id)]).encode()
    ).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        created_raw, id_raw = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(str(created_raw)), uuid.UUID(str(id_raw))
    except (binascii.Error, ValueError, TypeError, IndexError) as error:
        raise NotificationError("invalid_cursor", "invalid cursor") from error


# ---------------------------------------------------------------------------
# inbox
# ---------------------------------------------------------------------------


async def list_inbox(
    connection: AsyncConnection,
    principal: Principal,
    *,
    unread_only: bool = False,
    window: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The caller's own notifications, newest first."""
    if window is not None and window not in INBOX_WINDOWS:
        raise NotificationError("unsupported_window", "unsupported time window")
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    # What the reader may still see. Computed once and applied per row, so
    # a grant revoked yesterday takes effect on today's read.
    visible = await visible_scope_ids(connection, principal, INCIDENT_READ_PERMISSION)

    filters = ["n.recipient_identity_id = :me"]
    params: dict[str, Any] = {
        "me": principal.identity_id,
        "limit": limit + 1,
        "visible": _sentinel(visible),
    }
    if unread_only:
        filters.append("n.read_at IS NULL")
    if window is not None:
        filters.append("n.created_at >= now() - CAST(:window AS interval)")
        params["window"] = f"{INBOX_WINDOWS[window]} seconds"
    if cursor:
        created_at, last_id = decode_cursor(cursor)
        filters.append("(n.created_at, n.id) < (:cursor_at, :cursor_id)")
        params["cursor_at"] = created_at
        params["cursor_id"] = last_id

    rows = (
        await connection.execute(
            text(
                f"""
                SELECT n.id, n.event_type, n.title, n.body, n.target_path,
                       n.metadata_snapshot, n.created_at, n.read_at, n.incident_id,
                       (es.scope_id = ANY(:visible)) AS accessible
                FROM in_app_notifications n
                JOIN incidents i ON i.id = n.incident_id
                JOIN environment_services es ON es.id = i.environment_service_id
                WHERE {" AND ".join(filters)}
                ORDER BY n.created_at DESC, n.id DESC
                LIMIT :limit
                """  # noqa: S608 - every fragment is fixed text
            ),
            params,
        )
    ).all()

    page = rows[:limit]
    next_cursor = encode_cursor(page[-1][6], page[-1][0]) if len(rows) > limit and page else None
    return {
        "items": [_inbox_row(row) for row in page],
        "next_cursor": next_cursor,
        "limit": limit,
    }


def _inbox_row(row: Any) -> dict[str, Any]:
    accessible = bool(row[9])
    return {
        "id": str(row[0]),
        "event_type": row[1] if accessible else None,
        "title": row[2] if accessible else WITHHELD_TITLE,
        "body": row[3] if accessible else WITHHELD_BODY,
        # No link either: a path to an incident they cannot open would only
        # produce a 404 and confirm it exists.
        "target_path": row[4] if accessible else None,
        "metadata": (row[5] or {}) if accessible else {},
        "created_at": row[6].isoformat(),
        "read_at": row[7].isoformat() if row[7] else None,
        "incident_id": str(row[8]) if accessible else None,
        "accessible": accessible,
    }


async def unread_count(connection: AsyncConnection, principal: Principal) -> int:
    """Counts the caller's own unread rows, accessible or not.

    Counting only accessible ones would make the badge disagree with the
    list for no useful reason — the row is theirs either way.
    """
    return int(
        (
            await connection.execute(
                text(
                    "SELECT count(*) FROM in_app_notifications "
                    "WHERE recipient_identity_id = :me AND read_at IS NULL"
                ),
                {"me": principal.identity_id},
            )
        ).scalar_one()
    )


async def mark_read(
    connection: AsyncConnection, principal: Principal, notification_ids: list[uuid.UUID]
) -> int:
    """Mark the caller's own notifications read. Idempotent by construction.

    The recipient filter is in the UPDATE itself, so an id belonging to
    someone else matches nothing — it cannot be used to probe for one
    either, since the count is the same as for an unknown id.
    """
    if not notification_ids:
        return 0
    result = await connection.execute(
        text(
            """
            UPDATE in_app_notifications
            SET read_at = now()
            WHERE recipient_identity_id = :me
              AND id = ANY(:ids)
              AND read_at IS NULL
            """
        ),
        {"me": principal.identity_id, "ids": notification_ids[:MAX_MARK_READ]},
    )
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# destinations
# ---------------------------------------------------------------------------


def available_webhook_keys(settings: Settings) -> list[dict[str, Any]]:
    """The operator's registry, as the UI is allowed to see it.

    Key and display name. The URL, the timeout and the signing secret
    reference stay in settings — this function is the reason there is no
    code path from the API to any of them.
    """
    return [
        {
            "key": key,
            "display_name": destination.display_name or key,
            "payload_schema_version": destination.payload_schema_version,
        }
        for key, destination in sorted(settings.notification_webhooks.items())
    ]


async def _project_scope(connection: AsyncConnection, project_id: uuid.UUID) -> uuid.UUID | None:
    return (
        await connection.execute(
            text("SELECT scope_id FROM projects WHERE id = :id"), {"id": project_id}
        )
    ).scalar_one_or_none()


async def _identity_covers_project(
    connection: AsyncConnection, identity_id: uuid.UUID, project_scope: uuid.UUID
) -> bool:
    """Whether this user can see anything inside the project.

    Without this check, a policy could deliver incident titles for a
    project to somebody with no access to it — a notification is a side
    channel, and this is where that channel is closed.

    Intersection with the project's SUBTREE rather than the project scope
    itself: a service owner granted on one service is exactly the person
    who should receive that service's incidents, and requiring
    project-level access would exclude them.
    """
    candidate = Principal(identity_id=identity_id, issuer="")
    visible = await visible_scope_ids(connection, candidate, INCIDENT_READ_PERMISSION)
    if not visible:
        return False
    if project_scope in visible:
        return True
    overlap = (
        await connection.execute(
            text(
                """
                WITH RECURSIVE subtree AS (
                    SELECT id FROM scopes WHERE id = :project
                    UNION
                    SELECT s.id FROM scopes s JOIN subtree t ON s.parent_id = t.id
                )
                SELECT 1 FROM subtree WHERE id = ANY(:visible) LIMIT 1
                """
            ),
            {"project": project_scope, "visible": list(visible)},
        )
    ).first()
    return overlap is not None


async def list_destinations(
    connection: AsyncConnection, principal: Principal, project_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    visible = await visible_scope_ids(connection, principal, NOTIFICATION_READ_PERMISSION)
    params: dict[str, Any] = {"visible": _sentinel(visible)}
    clause = ""
    if project_id is not None:
        clause = "AND d.project_id = :project"
        params["project"] = project_id
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT d.id, d.destination_type, d.display_name, d.destination_key,
                       d.enabled, d.project_id, ident.display_name, ident.email,
                       d.payload_schema_version, d.version
                FROM notification_destinations d
                JOIN projects p ON p.id = d.project_id
                LEFT JOIN identities ident ON ident.id = d.identity_id
                WHERE p.scope_id = ANY(:visible) {clause}
                ORDER BY d.destination_type, d.display_name
                LIMIT 200
                """  # noqa: S608 - `clause` is fixed text
            ),
            params,
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            "destination_type": row[1],
            "display_name": row[2],
            # The key is an opaque handle into the operator's registry. It
            # is safe to show; the URL behind it is not, and is not here.
            "destination_key": row[3],
            "enabled": row[4],
            "project_id": str(row[5]),
            "recipient": (None if row[6] is None else {"display_name": row[6], "email": row[7]}),
            "payload_schema_version": row[8],
            "version": row[9],
        }
        for row in rows
    ]


async def create_destination(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID,
    destination_type: str,
    display_name: str,
    identity_id: uuid.UUID | None,
    destination_key: str | None,
    settings: Settings,
    actor_identity_id: uuid.UUID,
) -> dict[str, Any]:
    manageable = await visible_scope_ids(connection, principal, NOTIFICATION_MANAGE_PERMISSION)
    project_scope = await _project_scope(connection, project_id)
    if project_scope is None or project_scope not in manageable:
        raise NotificationError("not_found", "not found")

    if destination_type == "in_app_user":
        if identity_id is None:
            raise NotificationError("invalid_destination", "an in-app destination needs a user")
        if not await _identity_covers_project(connection, identity_id, project_scope):
            # Refused, not silently dropped: an operator picking a user who
            # cannot see the project should be told, not quietly ignored.
            raise NotificationError(
                "recipient_out_of_scope", "that user cannot see this project"
            )
        destination_key = None
    elif destination_type == "webhook":
        if destination_key is None or destination_key not in settings.notification_webhooks:
            # The registry is the allowlist. A key that is not in it cannot
            # be created, so a policy can never point at an arbitrary URL.
            raise NotificationError("unknown_destination_key", "unknown webhook destination")
        identity_id = None
    else:
        raise NotificationError("invalid_destination", "unknown destination type")

    schema_version = (
        settings.notification_webhooks[destination_key].payload_schema_version
        if destination_key
        else 1
    )
    row = (
        await connection.execute(
            text(
                """
                INSERT INTO notification_destinations
                    (destination_type, display_name, project_id, identity_id,
                     destination_key, payload_schema_version, created_by)
                VALUES (:type, :name, :project, :identity, :key, :schema, :actor)
                ON CONFLICT DO NOTHING
                RETURNING id, version
                """
            ),
            {
                "type": destination_type,
                "name": display_name,
                "project": project_id,
                "identity": identity_id,
                "key": destination_key,
                "schema": schema_version,
                "actor": actor_identity_id,
            },
        )
    ).first()
    if row is None:
        raise NotificationError("duplicate_destination", "this destination already exists")
    return {"id": str(row[0]), "version": row[1]}


# ---------------------------------------------------------------------------
# policies
# ---------------------------------------------------------------------------


def validate_policy_shape(event_types: list[str], severities: list[str]) -> None:
    unknown_events = set(event_types) - set(NOTIFIABLE_EVENTS)
    if not event_types or unknown_events:
        raise NotificationError("invalid_event_types", "unsupported incident event type")
    unknown_severities = set(severities) - set(SEVERITIES)
    if not severities or unknown_severities:
        raise NotificationError("invalid_severities", "unsupported severity")


async def _validate_catalog_narrowing(
    connection: AsyncConnection,
    project_id: uuid.UUID,
    environment_id: uuid.UUID | None,
    service_id: uuid.UUID | None,
) -> None:
    """An environment or service filter must belong to the policy's project.

    Otherwise a policy scoped to project A could be narrowed to a service
    in project B and quietly become a channel between them.
    """
    if environment_id is not None:
        owner = (
            await connection.execute(
                text("SELECT project_id FROM environments WHERE id = :id"),
                {"id": environment_id},
            )
        ).scalar_one_or_none()
        if owner != project_id:
            raise NotificationError("environment_out_of_scope", "not found")
    if service_id is not None:
        owner = (
            await connection.execute(
                text("SELECT project_id FROM service_definitions WHERE id = :id"),
                {"id": service_id},
            )
        ).scalar_one_or_none()
        if owner != project_id:
            raise NotificationError("service_out_of_scope", "not found")


async def create_policy(
    connection: AsyncConnection,
    principal: Principal,
    *,
    display_name: str,
    project_id: uuid.UUID,
    environment_id: uuid.UUID | None,
    service_id: uuid.UUID | None,
    event_types: list[str],
    severities: list[str],
    actor_identity_id: uuid.UUID,
) -> dict[str, Any]:
    validate_policy_shape(event_types, severities)
    manageable = await visible_scope_ids(connection, principal, NOTIFICATION_MANAGE_PERMISSION)
    project_scope = await _project_scope(connection, project_id)
    if project_scope is None or project_scope not in manageable:
        raise NotificationError("not_found", "not found")
    await _validate_catalog_narrowing(connection, project_id, environment_id, service_id)

    row = (
        await connection.execute(
            text(
                """
                INSERT INTO notification_policies
                    (display_name, project_id, environment_id, service_id,
                     event_types, severities, created_by, updated_by)
                VALUES (:name, :project, :environment, :service,
                        CAST(:events AS jsonb), CAST(:severities AS jsonb), :actor, :actor)
                RETURNING id, version
                """
            ),
            {
                "name": display_name,
                "project": project_id,
                "environment": environment_id,
                "service": service_id,
                "events": json.dumps(sorted(set(event_types))),
                "severities": json.dumps(sorted(set(severities))),
                "actor": actor_identity_id,
            },
        )
    ).first()
    assert row is not None
    return {"id": str(row[0]), "version": row[1]}


async def update_policy(
    connection: AsyncConnection,
    principal: Principal,
    policy_id: uuid.UUID,
    *,
    display_name: str,
    environment_id: uuid.UUID | None,
    service_id: uuid.UUID | None,
    event_types: list[str],
    severities: list[str],
    enabled: bool,
    expected_version: int,
    actor_identity_id: uuid.UUID,
) -> dict[str, Any]:
    """Change a policy. Only future events are affected.

    Deliveries already planned keep their frozen payload and destination —
    editing a rule must never rewrite what was already decided.
    """
    validate_policy_shape(event_types, severities)
    manageable = await visible_scope_ids(connection, principal, NOTIFICATION_MANAGE_PERMISSION)
    row = (
        await connection.execute(
            text(
                """
                SELECT pol.version, pol.project_id, p.scope_id
                FROM notification_policies pol
                JOIN projects p ON p.id = pol.project_id
                WHERE pol.id = :id
                """
            ),
            {"id": policy_id},
        )
    ).first()
    if row is None or row[2] not in manageable:
        raise NotificationError("not_found", "not found")
    if row[0] != expected_version:
        raise NotificationError("version_conflict", "the policy changed since it was read")
    await _validate_catalog_narrowing(connection, row[1], environment_id, service_id)

    updated = (
        await connection.execute(
            text(
                """
                UPDATE notification_policies
                SET display_name = :name,
                    environment_id = :environment,
                    service_id = :service,
                    event_types = CAST(:events AS jsonb),
                    severities = CAST(:severities AS jsonb),
                    enabled = :enabled,
                    version = version + 1,
                    updated_at = now(),
                    updated_by = :actor
                WHERE id = :id AND version = :expected
                RETURNING version
                """
            ),
            {
                "id": policy_id,
                "name": display_name,
                "environment": environment_id,
                "service": service_id,
                "events": json.dumps(sorted(set(event_types))),
                "severities": json.dumps(sorted(set(severities))),
                "enabled": enabled,
                "expected": expected_version,
                "actor": actor_identity_id,
            },
        )
    ).first()
    if updated is None:
        raise NotificationError("version_conflict", "the policy changed since it was read")
    return {"id": str(policy_id), "version": updated[0]}


async def attach_destination(
    connection: AsyncConnection,
    principal: Principal,
    policy_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> dict[str, Any]:
    manageable = await visible_scope_ids(connection, principal, NOTIFICATION_MANAGE_PERMISSION)
    row = (
        await connection.execute(
            text(
                """
                SELECT pol.project_id, p.scope_id, d.project_id
                FROM notification_policies pol
                JOIN projects p ON p.id = pol.project_id
                JOIN notification_destinations d ON d.id = :destination
                WHERE pol.id = :policy
                """
            ),
            {"policy": policy_id, "destination": destination_id},
        )
    ).first()
    if row is None or row[1] not in manageable:
        raise NotificationError("not_found", "not found")
    if row[0] != row[2]:
        # Cross-project attachment is how a notification rule becomes a
        # bridge between two tenants.
        raise NotificationError("destination_out_of_scope", "not found")

    inserted = (
        await connection.execute(
            text(
                """
                INSERT INTO notification_policy_destinations (policy_id, destination_id)
                VALUES (:policy, :destination)
                ON CONFLICT (policy_id, destination_id) DO NOTHING
                RETURNING id
                """
            ),
            {"policy": policy_id, "destination": destination_id},
        )
    ).first()
    return {"attached": inserted is not None}


async def list_policies(
    connection: AsyncConnection,
    principal: Principal,
    project_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    visible = await visible_scope_ids(connection, principal, NOTIFICATION_READ_PERMISSION)
    params: dict[str, Any] = {"visible": _sentinel(visible)}
    clause = ""
    if project_id is not None:
        clause = "AND pol.project_id = :project"
        params["project"] = project_id
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT pol.id, pol.display_name, pol.project_id, p.project_key,
                       pol.environment_id, e.environment_key,
                       pol.service_id, sd.service_key,
                       pol.event_types, pol.severities, pol.enabled, pol.version,
                       COALESCE(
                         (SELECT count(*) FROM notification_policy_destinations pd
                          WHERE pd.policy_id = pol.id AND pd.enabled), 0)
                FROM notification_policies pol
                JOIN projects p ON p.id = pol.project_id
                LEFT JOIN environments e ON e.id = pol.environment_id
                LEFT JOIN service_definitions sd ON sd.id = pol.service_id
                WHERE p.scope_id = ANY(:visible) {clause}
                ORDER BY p.project_key, pol.display_name
                LIMIT 200
                """  # noqa: S608 - `clause` is fixed text
            ),
            params,
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            "display_name": row[1],
            "project_id": str(row[2]),
            "project_key": row[3],
            "environment_id": str(row[4]) if row[4] else None,
            "environment_key": row[5],
            "service_id": str(row[6]) if row[6] else None,
            "service_key": row[7],
            "event_types": list(row[8] or ()),
            "severities": list(row[9] or ()),
            "enabled": row[10],
            "version": row[11],
            "destination_count": row[12],
        }
        for row in rows
    ]


async def get_policy(
    connection: AsyncConnection, principal: Principal, policy_id: uuid.UUID
) -> dict[str, Any] | None:
    policies = await list_policies(connection, principal)
    match = next((policy for policy in policies if policy["id"] == str(policy_id)), None)
    if match is None:
        return None
    rows = (
        await connection.execute(
            text(
                """
                SELECT d.id, d.destination_type, d.display_name, d.destination_key,
                       d.enabled, pd.enabled
                FROM notification_policy_destinations pd
                JOIN notification_destinations d ON d.id = pd.destination_id
                WHERE pd.policy_id = :policy
                ORDER BY d.display_name
                """
            ),
            {"policy": policy_id},
        )
    ).all()
    return {
        **match,
        "destinations": [
            {
                "id": str(row[0]),
                "destination_type": row[1],
                "display_name": row[2],
                "destination_key": row[3],
                "enabled": bool(row[4] and row[5]),
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# delivery audit
# ---------------------------------------------------------------------------


async def list_deliveries(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    state: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    visible = await visible_scope_ids(connection, principal, NOTIFICATION_READ_PERMISSION)
    filters = ["p.scope_id = ANY(:visible)"]
    params: dict[str, Any] = {"visible": _sentinel(visible), "limit": limit}
    if project_id is not None:
        filters.append("wd.project_id = :project")
        params["project"] = project_id
    if state is not None:
        filters.append("wd.state = :state")
        params["state"] = state

    rows = (
        await connection.execute(
            text(
                f"""
                SELECT wd.id, wd.state, wd.attempt_count, wd.next_attempt_at,
                       wd.delivered_at, wd.last_error_code, wd.last_http_status,
                       wd.created_at, d.display_name, ev.event_type, wd.incident_id,
                       i.title, p.project_key
                FROM webhook_deliveries wd
                JOIN projects p ON p.id = wd.project_id
                JOIN notification_destinations d ON d.id = wd.destination_id
                JOIN incident_events ev ON ev.id = wd.incident_event_id
                JOIN incidents i ON i.id = wd.incident_id
                WHERE {" AND ".join(filters)}
                ORDER BY wd.created_at DESC, wd.id DESC
                LIMIT :limit
                """  # noqa: S608 - every fragment is fixed text
            ),
            params,
        )
    ).all()
    return {
        "items": [
            {
                "id": str(row[0]),
                "state": row[1],
                "attempt_count": row[2],
                "next_attempt_at": row[3].isoformat() if row[3] else None,
                "delivered_at": row[4].isoformat() if row[4] else None,
                # A bounded classification code, never an exception text.
                "last_error_code": row[5],
                "last_http_status": row[6],
                "created_at": row[7].isoformat(),
                # The display name only. The URL it resolves to is not
                # readable through any endpoint.
                "destination_display_name": row[8],
                "event_type": row[9],
                "incident_id": str(row[10]),
                "incident_title": row[11],
                "project_key": row[12],
            }
            for row in rows
        ],
        "limit": limit,
    }


async def list_delivery_attempts(
    connection: AsyncConnection, principal: Principal, delivery_id: uuid.UUID
) -> list[dict[str, Any]] | None:
    visible = await visible_scope_ids(connection, principal, NOTIFICATION_READ_PERMISSION)
    allowed = (
        await connection.execute(
            text(
                """
                SELECT 1 FROM webhook_deliveries wd
                JOIN projects p ON p.id = wd.project_id
                WHERE wd.id = :id AND p.scope_id = ANY(:visible)
                """
            ),
            {"id": delivery_id, "visible": _sentinel(visible)},
        )
    ).first()
    if allowed is None:
        return None
    rows = (
        await connection.execute(
            text(
                """
                SELECT attempt_number, started_at, completed_at, outcome,
                       http_status, error_code, duration_ms, retry_at
                FROM webhook_delivery_attempts
                WHERE delivery_id = :id
                ORDER BY attempt_number
                LIMIT 50
                """
            ),
            {"id": delivery_id},
        )
    ).all()
    return [
        {
            "attempt_number": row[0],
            "started_at": row[1].isoformat(),
            "completed_at": row[2].isoformat() if row[2] else None,
            "outcome": row[3],
            "http_status": row[4],
            "error_code": row[5],
            "duration_ms": row[6],
            "retry_at": row[7].isoformat() if row[7] else None,
        }
        for row in rows
    ]
