"""Objectives defined in the screen, measured on stored requests.

A project manifest can still declare objectives (`slos:`), and those stay
the manifest's: the screen neither edits nor removes them. What the screen
adds is the common case here — services that publish no HTTP metrics but
whose requests Tracesis stores as traces — with two indicators:

- **availability**: the share of requests that did not fail;
- **latency**: the share of timed requests faster than a fixed threshold.

Both are counted per request from `sli_rollups`, so a busy minute weighs
more than a quiet one. Targets, windows and thresholds come from short,
fixed lists; the server builds the definition, the client never names a
template, a profile or a query.

`slo.manage` on the project decides who may write; reading stays
`slo.view` + `project.view` like the rest of the SLO API.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.alerting import sli_rollups
from drake_api.alerting.slo import DEFAULT_BURN_PROFILE
from drake_api.alerting.slo_service import (
    TRACE_AVAILABILITY,
    TRACE_LATENCY,
    TRACE_TEMPLATES,
    resolve_slo_incidents,
    trace_threshold_ms,
)
from drake_api.catalog.authz import visible_scope_ids
from drake_api.rbac.service import Principal

MANAGE_PERMISSION = "slo.manage"
DAY = 86_400
WINDOWS: tuple[int, ...] = (7 * DAY, 28 * DAY, 30 * DAY)
OBJECTIVES: tuple[float, ...] = (0.9, 0.95, 0.99, 0.995, 0.999, 0.9995, 0.9999)
# What a new objective starts from, per indicator: what a service that is
# not in trouble already meets, so a fresh objective does not page at once.
SUGGESTED = {
    "availability": {"objective_ratio": 0.995, "window_seconds": 30 * DAY},
    "latency": {"objective_ratio": 0.95, "window_seconds": 30 * DAY, "threshold_ms": 1000},
}
_KEY = re.compile(r"[^a-z0-9]+")


class SloManageError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


async def _manageable_scopes(connection: AsyncConnection, principal: Principal) -> set[uuid.UUID]:
    manage = await visible_scope_ids(connection, principal, MANAGE_PERMISSION)
    view = await visible_scope_ids(connection, principal, "project.view")
    return set(manage) & set(view)


async def options(connection: AsyncConnection, principal: Principal) -> dict[str, Any]:
    """The services the caller may set objectives for, and the fixed lists."""
    scopes = await _manageable_scopes(connection, principal)
    rows = (
        await connection.execute(
            text(
                """
                SELECT es.id, p.id, p.project_key, e.id, e.environment_key,
                       sd.id, sd.service_key,
                       (SELECT count(*) FROM slo_definitions d
                         WHERE d.environment_service_id = es.id) AS objectives
                FROM environment_services es
                JOIN projects p ON p.id = es.project_id
                JOIN environments e ON e.id = es.environment_id
                JOIN service_definitions sd ON sd.id = es.service_id
                WHERE p.scope_id = ANY(:scopes)
                  AND EXISTS (SELECT 1 FROM service_workload_bindings b
                              WHERE b.environment_service_id = es.id
                                AND b.lifecycle = 'active')
                ORDER BY p.project_key, e.environment_key, sd.service_key
                """
            ),
            {"scopes": list(scopes) or [uuid.UUID(int=0)]},
        )
    ).all()
    return {
        "can_manage": bool(scopes),
        "services": [
            {
                "environment_service_id": str(r[0]),
                "project_id": str(r[1]),
                "project_key": r[2],
                "environment_id": str(r[3]),
                "environment_key": r[4],
                "service_id": str(r[5]),
                "service_key": r[6],
                "objectives": int(r[7]),
            }
            for r in rows
        ],
        "indicators": ["availability", "latency"],
        "objectives": list(OBJECTIVES),
        "windows_seconds": list(WINDOWS),
        "thresholds_ms": list(sli_rollups.THRESHOLDS_MS),
        "suggested": SUGGESTED,
    }


async def _target(
    connection: AsyncConnection, principal: Principal, environment_service_id: uuid.UUID
) -> Any:
    scopes = await _manageable_scopes(connection, principal)
    row = (
        await connection.execute(
            text(
                """
                SELECT es.project_id, es.environment_id, es.service_id, p.project_key,
                       e.environment_key, sd.service_key
                FROM environment_services es
                JOIN projects p ON p.id = es.project_id
                JOIN environments e ON e.id = es.environment_id
                JOIN service_definitions sd ON sd.id = es.service_id
                WHERE es.id = :es AND p.scope_id = ANY(:scopes)
                """
            ),
            {"es": environment_service_id, "scopes": list(scopes) or [uuid.UUID(int=0)]},
        )
    ).first()
    if row is None:
        raise SloManageError("not_found", "not found", 404)
    return row


def _check(values: dict[str, Any]) -> None:
    if values.get("indicator") not in ("availability", "latency"):
        raise SloManageError("invalid_indicator", "availability or latency")
    if float(values.get("objective_ratio", 0)) not in OBJECTIVES:
        raise SloManageError("invalid_objective", "choose one of the offered objectives")
    if int(values.get("window_seconds", 0)) not in WINDOWS:
        raise SloManageError("invalid_window", "choose one of the offered windows")
    if values["indicator"] == "latency" and values.get("threshold_ms") not in (
        sli_rollups.THRESHOLDS_MS
    ):
        raise SloManageError("invalid_threshold", "choose one of the offered thresholds")
    name = str(values.get("display_name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise SloManageError("invalid_name", "a name of 1 to 120 characters")


async def create(
    connection: AsyncConnection, principal: Principal, values: dict[str, Any]
) -> dict[str, Any]:
    target = await _target(connection, principal, values["environment_service_id"])
    _check(values)
    indicator = values["indicator"]
    base = _KEY.sub("-", f"{target[5]}-{target[4]}-{indicator}".lower()).strip("-")[:80]
    taken = {
        str(r[0])
        for r in (
            await connection.execute(
                text("SELECT slo_key FROM slo_definitions WHERE project_id = :p"),
                {"p": target[0]},
            )
        ).all()
    }
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    row = (
        await connection.execute(
            text(
                """
                INSERT INTO slo_definitions
                    (project_id, environment_id, service_id, environment_service_id,
                     slo_key, display_name, indicator, objective_ratio, window_seconds,
                     sli_template_key, threshold_profile_key, burn_profile_key)
                VALUES (:project, :environment, :service, :es, :key, :name, :indicator,
                        :objective, :window, :template, :threshold, :burn)
                RETURNING id, version
                """
            ),
            {
                "project": target[0],
                "environment": target[1],
                "service": target[2],
                "es": values["environment_service_id"],
                "key": key,
                "name": str(values["display_name"]).strip(),
                "indicator": indicator,
                "objective": float(values["objective_ratio"]),
                "window": int(values["window_seconds"]),
                "template": TRACE_LATENCY if indicator == "latency" else TRACE_AVAILABILITY,
                "threshold": (
                    f"traces.over_{int(values['threshold_ms'])}ms"
                    if indicator == "latency"
                    else None
                ),
                "burn": DEFAULT_BURN_PROFILE,
            },
        )
    ).one()
    return {"id": str(row[0]), "version": int(row[1]), "slo_key": key}


async def _owned(connection: AsyncConnection, principal: Principal, slo_id: uuid.UUID) -> Any:
    """A screen-defined objective the caller may write. Manifest ones are
    the manifest's: 404 here as for anything else they cannot change."""
    row = (
        await connection.execute(
            text(
                "SELECT environment_service_id, indicator, objective_ratio, window_seconds, "
                "threshold_profile_key, display_name, enabled, version, sli_template_key "
                "FROM slo_definitions WHERE id = :id"
            ),
            {"id": slo_id},
        )
    ).first()
    if row is None or row[0] is None or str(row[8]) not in TRACE_TEMPLATES:
        raise SloManageError("not_found", "not found", 404)
    await _target(connection, principal, row[0])
    return row


async def update(
    connection: AsyncConnection,
    principal: Principal,
    slo_id: uuid.UUID,
    changes: dict[str, Any],
    expected_version: int,
) -> dict[str, Any]:
    current = await _owned(connection, principal, slo_id)
    merged = {
        "indicator": current[1],
        "objective_ratio": float(current[2]),
        "window_seconds": int(current[3]),
        "threshold_ms": trace_threshold_ms(current[4]),
        "display_name": current[5],
        "enabled": bool(current[6]),
        **{k: v for k, v in changes.items() if v is not None},
    }
    _check(merged)
    row = (
        await connection.execute(
            text(
                """
                UPDATE slo_definitions
                SET display_name = :name, objective_ratio = :objective,
                    window_seconds = :window, threshold_profile_key = :threshold,
                    enabled = :enabled, version = version + 1, updated_at = now()
                WHERE id = :id AND version = :version
                RETURNING version
                """
            ),
            {
                "id": slo_id,
                "version": expected_version,
                "name": str(merged["display_name"]).strip(),
                "objective": float(merged["objective_ratio"]),
                "window": int(merged["window_seconds"]),
                "threshold": (
                    f"traces.over_{int(merged['threshold_ms'])}ms"
                    if merged["indicator"] == "latency"
                    else None
                ),
                "enabled": bool(merged["enabled"]),
            },
        )
    ).first()
    if row is None:
        raise SloManageError("version_conflict", "the objective changed since it was read", 409)
    if current[6] and not merged["enabled"]:
        # Disabled: never measured again, so it lets go of what it holds.
        await resolve_slo_incidents(connection, slo_id, datetime.now(UTC), "slo_disabled")
    return {"id": str(slo_id), "version": int(row[0])}


async def delete(connection: AsyncConnection, principal: Principal, slo_id: uuid.UUID) -> None:
    await _owned(connection, principal, slo_id)
    await resolve_slo_incidents(connection, slo_id, datetime.now(UTC), "slo_deleted")
    await connection.execute(text("DELETE FROM slo_definitions WHERE id = :id"), {"id": slo_id})
