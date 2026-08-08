"""Protection reads: scope-filtered, bounded, evidence-only.

Visibility follows the project the policy protects. Filtering happens in
SQL before any count, summary or page, so a policy outside a caller's
scope cannot surface through a total, a filter option, or a cursor.

No response here carries a credential, a signed URL, a storage path, a
filename, a raw provider payload, or restored business data — there are no
columns for any of them.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.catalog.authz import visible_scope_ids
from drake_api.protection.model import BackupState, OverallState, RecoverabilityState
from drake_api.rbac.service import Principal

# Reading protection posture is its own right, on top of being able to see
# the project: knowing a system is unprotected is sensitive.
PROTECTION_PERMISSION = "protection.view"
PROJECT_PERMISSION = "environment.view"

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 25
MAX_TIMELINE = 50

BACKUP_STATES = frozenset(str(state) for state in BackupState)
RECOVERABILITY_STATES = frozenset(str(state) for state in RecoverabilityState)
OVERALL_STATES = frozenset(str(state) for state in OverallState)
OFFSITE_STATES = frozenset({"present", "missing"})
WINDOWS: dict[str, int] = {"24h": 86_400, "7d": 604_800, "30d": 2_592_000}


class FilterError(ValueError):
    """A filter value outside the allowlist (422)."""


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    return list(ids) or [uuid.UUID(int=0)]


async def _scopes(connection: AsyncConnection, principal: Principal) -> list[uuid.UUID]:
    """Both rights are required, so the visible set is their intersection.

    `protection.view` alone would let someone read posture for a project
    they cannot otherwise see; project access alone would expose backup
    coverage to anyone who can read a service.
    """
    protection = await visible_scope_ids(connection, principal, PROTECTION_PERMISSION)
    project = await visible_scope_ids(connection, principal, PROJECT_PERMISSION)
    return _sentinel(protection & project)


_COLUMNS = """
    bp.id, bp.display_name, bp.store_key, bp.store_kind, bp.provider_key,
    bp.connector_key, bp.rpo_seconds, bp.rto_seconds, bp.requires_offsite,
    bp.requires_integrity_check, bp.restore_verification_ttl_seconds, bp.enabled,
    bp.schedule_description, p.project_key, e.environment_key, bp.project_id,
    bp.environment_id,
    pe.backup_state, pe.recoverability_state, pe.overall_state, pe.reasons,
    pe.last_success_at, pe.last_attempt_at, pe.last_restore_at, pe.reporter_seen_at,
    pe.consecutive_failures, pe.computed_at
"""

# The newest evaluation for each policy. Historical rows stay untouched:
# they recorded what the policy promised at the time.
_JOINS = """
    FROM backup_policies bp
    JOIN projects p ON p.id = bp.project_id
    LEFT JOIN environments e ON e.id = bp.environment_id
    LEFT JOIN LATERAL (
        SELECT * FROM protection_evaluations x
        WHERE x.policy_id = bp.id
        ORDER BY x.evaluated_for DESC, x.computed_at DESC
        LIMIT 1
    ) pe ON true
"""

_VISIBILITY = "p.scope_id = ANY(:scopes)"


def _policy_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "display_name": row[1],
        "store_key": row[2],
        "store_kind": row[3],
        "provider_key": row[4],
        "connector_key": row[5],
        "rpo_seconds": row[6],
        "rto_seconds": row[7],
        "requires_offsite": row[8],
        "requires_integrity_check": row[9],
        "restore_verification_ttl_seconds": row[10],
        "enabled": row[11],
        "schedule_description": row[12],
        "project_key": row[13],
        "environment_key": row[14],
        "project_id": str(row[15]),
        "environment_id": str(row[16]) if row[16] else None,
        # `null` when nothing has been evaluated yet — never a cheerful
        # default.
        "evaluation": (
            None
            if row[17] is None
            else {
                "backup_state": row[17],
                "recoverability_state": row[18],
                "overall_state": row[19],
                "reasons": list(row[20] or ()),
                "last_success_at": row[21].isoformat() if row[21] else None,
                "last_attempt_at": row[22].isoformat() if row[22] else None,
                "last_restore_at": row[23].isoformat() if row[23] else None,
                "reporter_seen_at": row[24].isoformat() if row[24] else None,
                "consecutive_failures": row[25],
                "computed_at": row[26].isoformat() if row[26] else None,
            }
        ),
    }


def _validate(value: str | None, allowed: frozenset[str], message: str) -> None:
    if value is not None and value not in allowed:
        raise FilterError(message)


async def list_policies(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    store_key: str | None = None,
    connector_key: str | None = None,
    backup_state: str | None = None,
    recoverability_state: str | None = None,
    offsite_state: str | None = None,
    window: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    _validate(backup_state, BACKUP_STATES, "unknown backup state")
    _validate(recoverability_state, RECOVERABILITY_STATES, "unknown recoverability state")
    _validate(offsite_state, OFFSITE_STATES, "unknown offsite state")
    _validate(window, frozenset(WINDOWS), "unsupported time window")

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    params: dict[str, Any] = {
        "scopes": await _scopes(connection, principal),
        "limit": limit,
        "offset": offset,
    }
    filters = [_VISIBILITY]
    for value, clause, key in (
        (project_id, "bp.project_id = :project", "project"),
        (environment_id, "bp.environment_id = :environment", "environment"),
        (store_key, "bp.store_key = :store", "store"),
        (connector_key, "bp.connector_key = :connector", "connector"),
        (backup_state, "pe.backup_state = :backup_state", "backup_state"),
        (recoverability_state, "pe.recoverability_state = :recoverability", "recoverability"),
    ):
        if value is not None:
            filters.append(clause)
            params[key] = value
    if offsite_state is not None:
        filters.append(
            "EXISTS (SELECT 1 FROM backup_artifacts a JOIN replication_copies rc "
            "ON rc.artifact_id = a.id WHERE a.policy_id = bp.id AND rc.is_offsite "
            "AND rc.state = 'present') = :offsite_present"
        )
        params["offsite_present"] = offsite_state == "present"
    if window is not None:
        filters.append("pe.last_success_at >= now() - CAST(:window AS interval)")
        params["window"] = f"{WINDOWS[window]} seconds"

    where = " AND ".join(filters)
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT {_COLUMNS}
                {_JOINS}
                WHERE {where}
                ORDER BY p.project_key, bp.store_key
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).all()
    total = (
        await connection.execute(
            text(
                f"""
                SELECT count(*)
                {_JOINS}
                WHERE {where}
                """
            ),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )
    ).scalar_one()

    return {
        "items": [_policy_row(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def summary(connection: AsyncConnection, principal: Principal) -> dict[str, Any]:
    """Counts across the caller's visible policies, and nothing else.

    Computed from the same scope-filtered set as the list, so the summary
    can never hint at a policy the list will not show.
    """
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT COALESCE(pe.backup_state, 'unknown'),
                       COALESCE(pe.recoverability_state, 'unknown'),
                       COALESCE(pe.overall_state, 'unknown'),
                       count(*)
                {_JOINS}
                WHERE {_VISIBILITY}
                GROUP BY 1, 2, 3
                """
            ),
            {"scopes": await _scopes(connection, principal)},
        )
    ).all()

    backup: dict[str, int] = {state: 0 for state in sorted(BACKUP_STATES)}
    recoverability: dict[str, int] = {state: 0 for state in sorted(RECOVERABILITY_STATES)}
    overall: dict[str, int] = {state: 0 for state in sorted(OVERALL_STATES)}
    total = 0
    for backup_state, recoverability_state, overall_state, count in rows:
        backup[backup_state] = backup.get(backup_state, 0) + count
        recoverability[recoverability_state] = recoverability.get(recoverability_state, 0) + count
        overall[overall_state] = overall.get(overall_state, 0) + count
        total += count
    return {
        "total_policies": total,
        "backup": backup,
        "recoverability": recoverability,
        "overall": overall,
    }


async def get_policy(
    connection: AsyncConnection, principal: Principal, policy_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await connection.execute(
            text(
                f"""
                SELECT {_COLUMNS}
                {_JOINS}
                WHERE bp.id = :id AND {_VISIBILITY}
                """
            ),
            {"id": policy_id, "scopes": await _scopes(connection, principal)},
        )
    ).first()
    return None if row is None else _policy_row(row)


async def policy_runs(
    connection: AsyncConnection,
    principal: Principal,
    policy_id: uuid.UUID,
    limit: int = MAX_TIMELINE,
) -> list[dict[str, Any]] | None:
    if await get_policy(connection, principal, policy_id) is None:
        return None
    rows = (
        await connection.execute(
            text(
                """
                SELECT r.id, r.provider_run_id, r.status, r.started_at, r.completed_at,
                       r.duration_seconds, r.error_code, r.attempt, r.source_event_at,
                       r.ingested_at,
                       (SELECT count(*) FROM backup_artifacts a WHERE a.run_id = r.id)
                FROM backup_runs r
                WHERE r.policy_id = :id
                ORDER BY r.started_at DESC
                LIMIT :limit
                """
            ),
            {"id": policy_id, "limit": max(1, min(limit, MAX_TIMELINE))},
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            # The provider's own run handle: opaque, and safe to show.
            "provider_run_id": row[1],
            "status": row[2],
            "started_at": row[3].isoformat(),
            "completed_at": row[4].isoformat() if row[4] else None,
            "duration_seconds": row[5],
            "error_code": row[6],
            "attempt": row[7],
            # Provider time and Drake time, side by side: a late delivery
            # is visible as a late delivery, not as a late backup.
            "source_event_at": row[8].isoformat(),
            "ingested_at": row[9].isoformat(),
            "artifact_count": row[10],
        }
        for row in rows
    ]


async def get_run(
    connection: AsyncConnection, principal: Principal, run_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await connection.execute(
            text(
                f"""
                SELECT r.id, r.provider_run_id, r.status, r.started_at, r.completed_at,
                       r.duration_seconds, r.error_code, r.attempt, r.source_event_at,
                       r.ingested_at, bp.id, bp.display_name, bp.store_key
                FROM backup_runs r
                JOIN backup_policies bp ON bp.id = r.policy_id
                JOIN projects p ON p.id = bp.project_id
                WHERE r.id = :id AND {_VISIBILITY}
                """  # noqa: S608 - `_VISIBILITY` is fixed text
            ),
            {"id": run_id, "scopes": await _scopes(connection, principal)},
        )
    ).first()
    if row is None:
        return None
    artifacts = (
        await connection.execute(
            text(
                """
                SELECT id, artifact_external_key, size_bytes, checksum_algorithm,
                       checksum, encrypted, presence, storage_provider_key,
                       storage_site_key, created_at_source, expires_at, last_seen_at
                FROM backup_artifacts WHERE run_id = :run
                ORDER BY source_event_at DESC LIMIT 20
                """
            ),
            {"run": run_id},
        )
    ).all()
    return {
        "id": str(row[0]),
        "provider_run_id": row[1],
        "status": row[2],
        "started_at": row[3].isoformat(),
        "completed_at": row[4].isoformat() if row[4] else None,
        "duration_seconds": row[5],
        "error_code": row[6],
        "attempt": row[7],
        "source_event_at": row[8].isoformat(),
        "ingested_at": row[9].isoformat(),
        "policy": {"id": str(row[10]), "display_name": row[11], "store_key": row[12]},
        "artifacts": [_artifact_row(entry) for entry in artifacts],
    }


def _artifact_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        # An opaque provider handle — not a path, not a filename, and there
        # is no column that could hold either.
        "artifact_external_key": row[1],
        "size_bytes": row[2],
        "checksum_algorithm": row[3],
        # Shown short: a checksum is evidence that one exists and matches,
        # not something anyone reads in full.
        "checksum_prefix": (row[4] or "")[:12] or None,
        "encrypted": row[5],
        "presence": row[6],
        "storage_provider_key": row[7],
        "storage_site_key": row[8],
        "created_at": row[9].isoformat() if row[9] else None,
        "expires_at": row[10].isoformat() if row[10] else None,
        "last_seen_at": row[11].isoformat() if row[11] else None,
    }


async def get_artifact(
    connection: AsyncConnection, principal: Principal, artifact_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await connection.execute(
            text(
                f"""
                SELECT a.id, a.artifact_external_key, a.size_bytes, a.checksum_algorithm,
                       a.checksum, a.encrypted, a.presence, a.storage_provider_key,
                       a.storage_site_key, a.created_at_source, a.expires_at, a.last_seen_at,
                       bp.id, bp.display_name, bp.store_key
                FROM backup_artifacts a
                JOIN backup_policies bp ON bp.id = a.policy_id
                JOIN projects p ON p.id = bp.project_id
                WHERE a.id = :id AND {_VISIBILITY}
                """  # noqa: S608 - `_VISIBILITY` is fixed text
            ),
            {"id": artifact_id, "scopes": await _scopes(connection, principal)},
        )
    ).first()
    if row is None:
        return None
    checks = (
        await connection.execute(
            text(
                """
                SELECT method, result, error_code, checked_at
                FROM integrity_checks WHERE artifact_id = :id
                ORDER BY checked_at DESC LIMIT 20
                """
            ),
            {"id": artifact_id},
        )
    ).all()
    copies = (
        await connection.execute(
            text(
                """
                SELECT site_key, provider_key, state, is_offsite, size_bytes, observed_at
                FROM replication_copies WHERE artifact_id = :id
                ORDER BY observed_at DESC LIMIT 20
                """
            ),
            {"id": artifact_id},
        )
    ).all()
    return {
        **_artifact_row(row),
        "policy": {"id": str(row[12]), "display_name": row[13], "store_key": row[14]},
        "integrity_checks": [
            {
                "method": entry[0],
                "result": entry[1],
                "error_code": entry[2],
                "checked_at": entry[3].isoformat(),
            }
            for entry in checks
        ],
        "copies": [
            {
                # A site KEY, never a bucket or container URL.
                "site_key": entry[0],
                "provider_key": entry[1],
                "state": entry[2],
                "is_offsite": entry[3],
                "size_bytes": entry[4],
                "observed_at": entry[5].isoformat(),
            }
            for entry in copies
        ],
    }


async def policy_drills(
    connection: AsyncConnection,
    principal: Principal,
    policy_id: uuid.UUID,
    limit: int = MAX_TIMELINE,
) -> list[dict[str, Any]] | None:
    if await get_policy(connection, principal, policy_id) is None:
        return None
    rows = (
        await connection.execute(
            text(
                """
                SELECT id, drill_external_id, target_profile, result, started_at,
                       completed_at, duration_seconds, rto_met, validations, error_code
                FROM restore_drills WHERE policy_id = :id
                ORDER BY started_at DESC LIMIT :limit
                """
            ),
            {"id": policy_id, "limit": max(1, min(limit, MAX_TIMELINE))},
        )
    ).all()
    return [_drill_row(row) for row in rows]


def _drill_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "drill_external_id": row[1],
        # A profile name, never a connection string.
        "target_profile": row[2],
        "result": row[3],
        "started_at": row[4].isoformat(),
        "completed_at": row[5].isoformat() if row[5] else None,
        "duration_seconds": row[6],
        "rto_met": row[7],
        # Typed pass/fail only. No row samples, no SQL, no command output.
        "validations": row[8] or {},
        "error_code": row[9],
    }


async def get_drill(
    connection: AsyncConnection, principal: Principal, drill_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await connection.execute(
            text(
                f"""
                SELECT d.id, d.drill_external_id, d.target_profile, d.result, d.started_at,
                       d.completed_at, d.duration_seconds, d.rto_met, d.validations,
                       d.error_code, bp.id, bp.display_name, bp.store_key
                FROM restore_drills d
                JOIN backup_policies bp ON bp.id = d.policy_id
                JOIN projects p ON p.id = bp.project_id
                WHERE d.id = :id AND {_VISIBILITY}
                """  # noqa: S608 - `_VISIBILITY` is fixed text
            ),
            {"id": drill_id, "scopes": await _scopes(connection, principal)},
        )
    ).first()
    if row is None:
        return None
    return {
        **_drill_row(row),
        "policy": {"id": str(row[10]), "display_name": row[11], "store_key": row[12]},
    }


async def policy_incidents(
    connection: AsyncConnection, principal: Principal, policy_id: uuid.UUID
) -> list[dict[str, Any]] | None:
    """Incidents raised for this policy's project, for the timeline."""
    policy = await get_policy(connection, principal, policy_id)
    if policy is None:
        return None
    rows = (
        await connection.execute(
            text(
                """
                SELECT i.id, i.state, i.title, i.primary_reason, i.opened_at, i.resolved_at
                FROM incidents i
                WHERE i.project_id = :project
                  AND i.primary_reason = ANY(:reasons)
                ORDER BY i.opened_at DESC LIMIT 20
                """
            ),
            {
                "project": policy["project_id"],
                "reasons": [
                    "backup_overdue",
                    "latest_run_failed",
                    "integrity_failed",
                    "offsite_missing",
                    "restore_failed",
                    "restore_verification_expired",
                ],
            },
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            "state": row[1],
            "title": row[2],
            "primary_reason": row[3],
            "opened_at": row[4].isoformat(),
            "resolved_at": row[5].isoformat() if row[5] else None,
        }
        for row in rows
    ]
