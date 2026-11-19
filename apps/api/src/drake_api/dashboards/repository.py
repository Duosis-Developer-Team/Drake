"""Dashboard reads and writes (ADR-0033).

Seeing an environment's dashboard is `environment.view` at the environment.
Saving one is `dashboard.manage` at its project. A caller who cannot see an
environment cannot tell whether it has a dashboard: every refusal there is
the same 404.

An environment without a saved row still has a dashboard: the default
layout, at version 0. The first save writes version 1. Every save adds a
row to `dashboard_versions`; a restore is a save of an old layout, so
history only grows and nothing is deleted.
"""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.catalog.authz import visible_scope_ids
from drake_api.dashboards.model import (
    default_layout,
    default_title,
    validate_layout,
    validate_note,
    validate_title,
)
from drake_api.rbac.service import Principal

VIEW_PERMISSION = "environment.view"
MANAGE_PERMISSION = "dashboard.manage"
MAX_VERSIONS = 50
MAX_LIST = 500


class DashboardError(ValueError):
    """A request the server will not carry out (404/409/422 at the router)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    return list(ids) or [uuid.UUID(int=0)]


_ENVIRONMENT = """
SELECT e.id, e.environment_key, e.scope_id, p.id, p.project_key, p.display_name, p.scope_id
FROM environments e
JOIN projects p ON p.id = e.project_id
WHERE e.id = :id AND e.lifecycle = 'active'
"""


async def _environment(
    connection: AsyncConnection, principal: Principal, environment_id: uuid.UUID
) -> dict[str, Any]:
    row = (await connection.execute(text(_ENVIRONMENT), {"id": environment_id})).first()
    visible = await visible_scope_ids(connection, principal, VIEW_PERMISSION)
    if row is None or row[2] not in visible:
        raise DashboardError("not_found", "not found")
    manage = await visible_scope_ids(connection, principal, MANAGE_PERMISSION)
    return {
        "environment_id": row[0],
        "environment_key": row[1],
        "project_id": row[3],
        "project_key": row[4],
        "project_name": row[5],
        "can_manage": row[6] in manage,
    }


def _layout(value: Any) -> list[dict[str, Any]]:
    # A stored layout is re-validated on read, so a widget added to the
    # catalog after the save still appears (hidden) and an unknown one never
    # reaches the screen.
    raw = json.loads(value) if isinstance(value, str) else value
    try:
        return validate_layout(raw)
    except ValueError:
        return default_layout()


def _payload(env: dict[str, Any], row: Any | None) -> dict[str, Any]:
    base = {
        "environment_id": str(env["environment_id"]),
        "environment_key": env["environment_key"],
        "project_id": str(env["project_id"]),
        "project_key": env["project_key"],
        "project_name": env["project_name"],
        "can_manage": env["can_manage"],
    }
    if row is None:
        return {
            **base,
            "id": None,
            "title": default_title(env["project_name"], env["environment_key"]),
            "layout": default_layout(),
            "version": 0,
            "saved": False,
            "updated_at": None,
            "updated_by": None,
        }
    return {
        **base,
        "id": str(row[0]),
        "title": row[1],
        "layout": _layout(row[2]),
        "version": row[3],
        "saved": True,
        "updated_at": row[4].isoformat(),
        "updated_by": str(row[5]) if row[5] else None,
    }


_DASHBOARD = """
SELECT id, title, layout, version, updated_at, updated_by
FROM dashboards
WHERE environment_id = :environment_id AND archived_at IS NULL
"""


async def get_dashboard(
    connection: AsyncConnection, principal: Principal, environment_id: uuid.UUID
) -> dict[str, Any]:
    env = await _environment(connection, principal, environment_id)
    row = (await connection.execute(text(_DASHBOARD), {"environment_id": environment_id})).first()
    return _payload(env, row)


async def list_dashboards(connection: AsyncConnection, principal: Principal) -> dict[str, Any]:
    """Every active environment the caller may see, with its dashboard's
    title and version (version 0: the default, never saved)."""
    visible = await visible_scope_ids(connection, principal, VIEW_PERMISSION)
    manage = await visible_scope_ids(connection, principal, MANAGE_PERMISSION)
    rows = (
        await connection.execute(
            text(
                """
                SELECT e.id, e.environment_key, p.id, p.project_key, p.display_name, p.scope_id,
                       d.title, d.version, d.updated_at,
                       (SELECT count(*) FROM service_workload_bindings b
                        WHERE b.environment_id = e.id) AS services
                FROM environments e
                JOIN projects p ON p.id = e.project_id
                LEFT JOIN dashboards d ON d.environment_id = e.id AND d.archived_at IS NULL
                WHERE e.lifecycle = 'active' AND e.scope_id = ANY(:visible)
                ORDER BY p.display_name, e.environment_key
                LIMIT :limit
                """
            ),
            {"visible": _sentinel(visible), "limit": MAX_LIST},
        )
    ).all()
    return {
        "items": [
            {
                "environment_id": str(r[0]),
                "environment_key": r[1],
                "project_id": str(r[2]),
                "project_key": r[3],
                "project_name": r[4],
                "can_manage": r[5] in manage,
                "title": r[6] or default_title(r[4], r[1]),
                "version": r[7] or 0,
                "saved": r[6] is not None,
                "updated_at": r[8].isoformat() if r[8] else None,
                "services": int(r[9]),
            }
            for r in rows
        ]
    }


async def save_dashboard(
    connection: AsyncConnection,
    principal: Principal,
    environment_id: uuid.UUID,
    values: dict[str, Any],
    actor: uuid.UUID,
) -> dict[str, Any]:
    """Saves a new version. `expected_version` is the version the caller
    edited (0 for the default): a save over a newer one is a 409, never a
    silent overwrite of someone else's change."""
    env = await _environment(connection, principal, environment_id)
    if not env["can_manage"]:
        raise DashboardError("not_found", "not found")
    try:
        title = validate_title(values.get("title"))
        layout = validate_layout(values.get("layout"))
        note = validate_note(values.get("note"))
    except ValueError as error:
        raise DashboardError("invalid_dashboard", str(error)) from error
    expected = values.get("expected_version")
    if not isinstance(expected, int) or expected < 0:
        raise DashboardError("invalid_dashboard", "expected_version is required")

    encoded = json.dumps(layout)
    current = (
        await connection.execute(
            text(_DASHBOARD + " FOR UPDATE"), {"environment_id": environment_id}
        )
    ).first()
    if current is None:
        if expected != 0:
            raise DashboardError("version_conflict", "the dashboard changed; reload it")
        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO dashboards
                        (project_id, environment_id, title, layout, created_by, updated_by)
                    VALUES (:project_id, :environment_id, :title, CAST(:layout AS jsonb),
                            :actor, :actor)
                    ON CONFLICT (environment_id) DO NOTHING
                    RETURNING id, version
                    """
                ),
                {
                    "project_id": env["project_id"],
                    "environment_id": environment_id,
                    "title": title,
                    "layout": encoded,
                    "actor": actor,
                },
            )
        ).first()
        if row is None:  # another save created it first
            raise DashboardError("version_conflict", "the dashboard changed; reload it")
    else:
        if current[3] != expected:
            raise DashboardError("version_conflict", "the dashboard changed; reload it")
        row = (
            await connection.execute(
                text(
                    """
                    UPDATE dashboards
                    SET title = :title, layout = CAST(:layout AS jsonb),
                        version = version + 1, updated_by = :actor, updated_at = now()
                    WHERE id = :id AND version = :expected
                    RETURNING id, version
                    """
                ),
                {
                    "id": current[0],
                    "title": title,
                    "layout": encoded,
                    "actor": actor,
                    "expected": expected,
                },
            )
        ).first()
        if row is None:
            raise DashboardError("version_conflict", "the dashboard changed; reload it")

    await connection.execute(
        text(
            """
            INSERT INTO dashboard_versions (dashboard_id, version, title, layout, note, saved_by)
            VALUES (:id, :version, :title, CAST(:layout AS jsonb), :note, :actor)
            """
        ),
        {
            "id": row[0],
            "version": row[1],
            "title": title,
            "layout": encoded,
            "note": note,
            "actor": actor,
        },
    )
    return await get_dashboard(connection, principal, environment_id)


async def list_versions(
    connection: AsyncConnection, principal: Principal, environment_id: uuid.UUID
) -> dict[str, Any]:
    await _environment(connection, principal, environment_id)
    rows = (
        await connection.execute(
            text(
                """
                SELECT v.version, v.title, v.layout, v.note, v.saved_by, v.saved_at
                FROM dashboard_versions v
                JOIN dashboards d ON d.id = v.dashboard_id
                WHERE d.environment_id = :environment_id
                ORDER BY v.version DESC
                LIMIT :limit
                """
            ),
            {"environment_id": environment_id, "limit": MAX_VERSIONS},
        )
    ).all()
    return {
        "items": [
            {
                "version": r[0],
                "title": r[1],
                "layout": _layout(r[2]),
                "note": r[3],
                "saved_by": str(r[4]) if r[4] else None,
                "saved_at": r[5].isoformat(),
            }
            for r in rows
        ]
    }
