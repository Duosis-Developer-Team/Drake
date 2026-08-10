"""Deployment reads: scope-filtered, bounded, and free of raw Kubernetes.

A deployment row is visible when the caller can see the service it belongs
to, or — for a workload nobody has bound yet — the cluster it runs on.
Both checks happen in SQL before counting or paging, so an unauthorized
row cannot surface through a total or shift a cursor.

What a response carries is a decision and its evidence: states, counts,
short digests, typed provenance. Never a manifest, an annotation dump, a
query, or a URL Drake did not compose itself.
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
from drake_api.deployments.model import (
    WORKLOAD_KINDS,
    EvidenceState,
    RolloutState,
    short_digest,
    workflow_run_url,
)
from drake_api.rbac.service import Principal

# Seeing a deployment follows the same right as seeing the service's
# health; an unbound workload follows the right to see its cluster.
SERVICE_READ_PERMISSION = "environment.view"
CLUSTER_READ_PERMISSION = "cluster.view"

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 25
MAX_TIMELINE = 50

ROLLOUT_STATES = frozenset(str(state) for state in RolloutState)
EVIDENCE_STATES = frozenset(str(state) for state in EvidenceState)
OPENED_WINDOWS: dict[str, int] = {"24h": 86_400, "7d": 604_800, "30d": 2_592_000}


class FilterError(ValueError):
    """A filter value outside the allowlist (422)."""


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    return list(ids) or [uuid.UUID(int=0)]


def encode_cursor(started_at: datetime, row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(
        json.dumps([started_at.isoformat(), str(row_id)]).encode()
    ).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        started_raw, id_raw = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(str(started_raw)), uuid.UUID(str(id_raw))
    except (binascii.Error, ValueError, TypeError, IndexError) as error:
        raise FilterError("invalid cursor") from error


_COLUMNS = """
    d.id, d.namespace, d.workload_kind, d.workload_name, d.revision,
    d.observed_generation, d.images, d.primary_image, d.primary_digest,
    d.commit_sha, d.workflow_provider, d.workflow_repository, d.workflow_run_id,
    d.evidence_state, d.evidence_detail, d.rollout_state, d.rollout_reason,
    d.desired_replicas, d.ready_replicas, d.updated_replicas, d.available_replicas,
    d.rollout_started_at, d.rollout_completed_at, d.last_seen_at,
    c.cluster_ref, c.id, p.project_key, e.environment_key, sd.service_key,
    d.environment_service_id, d.binding_id, d.previous_revision_id,
    hc.verdict, hc.incident_count
"""

_JOINS = """
    FROM deployment_revisions d
    JOIN clusters c ON c.id = d.cluster_id
    LEFT JOIN environment_services es ON es.id = d.environment_service_id
    LEFT JOIN projects p ON p.id = d.project_id
    LEFT JOIN environments e ON e.id = d.environment_id
    LEFT JOIN service_definitions sd ON sd.id = d.service_id
    LEFT JOIN deployment_health_comparisons hc ON hc.deployment_revision_id = d.id
"""

# Visible if the caller can see the bound service, OR — when nothing is
# bound yet — the cluster it runs on. Two paths, one WHERE clause, applied
# before any count.
_VISIBILITY = (
    "(es.scope_id = ANY(:service_scopes)"
    " OR (d.environment_service_id IS NULL AND c.scope_id = ANY(:cluster_scopes)))"
)


def _row(row: Any, workflow_base_url: str) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "namespace": row[1],
        "workload_kind": row[2],
        "workload_name": row[3],
        "revision": row[4],
        "observed_generation": row[5],
        # Digests are shown short: a 64-character hex string is unreadable
        # and the full value is available on the detail record.
        "images": [
            {
                "name": image.get("name"),
                "image": image.get("image"),
                "digest": image.get("digest"),
                "short_digest": short_digest(image.get("digest")),
            }
            for image in (row[6] or [])
        ],
        "primary_image": row[7],
        "primary_digest": row[8],
        "short_digest": short_digest(row[8]),
        "commit_sha": row[9],
        "short_commit": (row[9] or "")[:7] or None,
        "workflow": {
            "provider": row[10],
            "repository": row[11],
            "run_id": row[12],
            # Composed from a CONFIGURED base and validated parts, or absent.
            "run_url": workflow_run_url(workflow_base_url, row[10], row[11], row[12]),
        },
        "evidence_state": row[13],
        "evidence_detail": row[14] or {},
        "rollout_state": row[15],
        "rollout_reason": row[16],
        "replicas": {
            "desired": row[17],
            "ready": row[18],
            "updated": row[19],
            "available": row[20],
        },
        "rollout_started_at": row[21].isoformat(),
        "rollout_completed_at": row[22].isoformat() if row[22] else None,
        "last_seen_at": row[23].isoformat(),
        "cluster": {"cluster_ref": row[24], "id": str(row[25])},
        "project_key": row[26],
        "environment_key": row[27],
        "service_key": row[28],
        "environment_service_id": str(row[29]) if row[29] else None,
        "binding_id": str(row[30]) if row[30] else None,
        "previous_revision_id": str(row[31]) if row[31] else None,
        "health_comparison": (
            None if row[32] is None else {"verdict": row[32], "incident_count": row[33]}
        ),
    }


async def _scopes(connection: AsyncConnection, principal: Principal) -> dict[str, list[uuid.UUID]]:
    return {
        "service_scopes": _sentinel(
            await visible_scope_ids(connection, principal, SERVICE_READ_PERMISSION)
        ),
        "cluster_scopes": _sentinel(
            await visible_scope_ids(connection, principal, CLUSTER_READ_PERMISSION)
        ),
    }


async def list_deployments(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    environment_service_id: uuid.UUID | None = None,
    cluster_id: uuid.UUID | None = None,
    workload_kind: str | None = None,
    rollout_state: str | None = None,
    evidence_state: str | None = None,
    started_within: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    workflow_base_url: str = "",
) -> dict[str, Any]:
    """One bounded page, newest rollout first."""
    if workload_kind is not None and workload_kind not in WORKLOAD_KINDS:
        raise FilterError("unknown workload kind")
    if rollout_state is not None and rollout_state not in ROLLOUT_STATES:
        raise FilterError("unknown rollout state")
    if evidence_state is not None and evidence_state not in EVIDENCE_STATES:
        raise FilterError("unknown evidence state")
    if started_within is not None and started_within not in OPENED_WINDOWS:
        raise FilterError("unsupported time window")

    limit = max(1, min(limit, MAX_PAGE_SIZE))
    params: dict[str, Any] = {**await _scopes(connection, principal), "limit": limit + 1}
    filters = [_VISIBILITY]

    for value, clause, key in (
        (project_id, "d.project_id = :project", "project"),
        (environment_id, "d.environment_id = :environment", "environment"),
        (environment_service_id, "d.environment_service_id = :service", "service"),
        (cluster_id, "d.cluster_id = :cluster", "cluster"),
        (workload_kind, "d.workload_kind = :kind", "kind"),
        (rollout_state, "d.rollout_state = :rollout", "rollout"),
        (evidence_state, "d.evidence_state = :evidence", "evidence"),
    ):
        if value is not None:
            filters.append(clause)
            params[key] = value
    if started_within is not None:
        filters.append("d.rollout_started_at >= now() - CAST(:window AS interval)")
        params["window"] = f"{OPENED_WINDOWS[started_within]} seconds"
    if cursor:
        started_at, last_id = decode_cursor(cursor)
        filters.append("(d.rollout_started_at, d.id) < (:cursor_at, :cursor_id)")
        params["cursor_at"] = started_at
        params["cursor_id"] = last_id

    where = " AND ".join(filters)
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT {_COLUMNS}
                {_JOINS}
                WHERE {where}
                ORDER BY d.rollout_started_at DESC, d.id DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).all()

    page = rows[:limit]
    next_cursor = encode_cursor(page[-1][21], page[-1][0]) if len(rows) > limit and page else None
    total = (
        await connection.execute(
            text(
                f"""
                SELECT count(*)
                {_JOINS}
                WHERE {
                    " AND ".join(
                        f for f in filters if not f.startswith("(d.rollout_started_at, d.id)")
                    )
                }
                """
            ),
            {k: v for k, v in params.items() if k not in ("limit", "cursor_at", "cursor_id")},
        )
    ).scalar_one()

    return {
        "items": [_row(row, workflow_base_url) for row in page],
        "next_cursor": next_cursor,
        "total": total,
        "limit": limit,
    }


async def get_deployment(
    connection: AsyncConnection,
    principal: Principal,
    deployment_id: uuid.UUID,
    workflow_base_url: str = "",
) -> dict[str, Any] | None:
    params = {**await _scopes(connection, principal), "id": deployment_id}
    row = (
        await connection.execute(
            text(
                f"""
                SELECT {_COLUMNS}, hc.signals, hc.missing_signals,
                       hc.before_from, hc.before_to, hc.after_from, hc.after_to,
                       hc.computed_at
                {_JOINS}
                WHERE d.id = :id AND {_VISIBILITY}
                """
            ),
            params,
        )
    ).first()
    if row is None:
        return None
    detail = _row(row, workflow_base_url)
    if row[32] is not None:
        detail["health_comparison"] = {
            "verdict": row[32],
            "incident_count": row[33],
            "signals": row[34] or {},
            "missing_signals": list(row[35] or ()),
            "before": {"from": row[36].isoformat(), "to": row[37].isoformat()},
            "after": {"from": row[38].isoformat(), "to": row[39].isoformat()},
            "computed_at": row[40].isoformat(),
        }
    return detail


async def revision_timeline(
    connection: AsyncConnection,
    principal: Principal,
    deployment_id: uuid.UUID,
    limit: int = MAX_TIMELINE,
) -> list[dict[str, Any]] | None:
    """Earlier revisions of the same workload, newest first."""
    params = {**await _scopes(connection, principal), "id": deployment_id}
    anchor = (
        await connection.execute(
            text(
                f"""
                SELECT d.cluster_id, d.workload_uid
                {_JOINS}
                WHERE d.id = :id AND {_VISIBILITY}
                """
            ),
            params,
        )
    ).first()
    if anchor is None:
        return None

    rows = (
        await connection.execute(
            text(
                """
                SELECT id, revision, rollout_state, evidence_state, primary_digest,
                       commit_sha, rollout_started_at, rollout_completed_at
                FROM deployment_revisions
                WHERE cluster_id = :cluster AND workload_uid = :uid
                ORDER BY revision DESC
                LIMIT :limit
                """
            ),
            {"cluster": anchor[0], "uid": anchor[1], "limit": max(1, min(limit, MAX_TIMELINE))},
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            "revision": row[1],
            "rollout_state": row[2],
            "evidence_state": row[3],
            "short_digest": short_digest(row[4]),
            "short_commit": (row[5] or "")[:7] or None,
            "rollout_started_at": row[6].isoformat(),
            "rollout_completed_at": row[7].isoformat() if row[7] else None,
        }
        for row in rows
    ]


async def related_incidents(
    connection: AsyncConnection, principal: Principal, deployment_id: uuid.UUID
) -> list[dict[str, Any]] | None:
    """Incidents opened for this service in the window after the rollout.

    Correlation only. Drake shows what overlapped in time and leaves the
    causal question to whoever is reading.
    """
    params = {**await _scopes(connection, principal), "id": deployment_id}
    anchor = (
        await connection.execute(
            text(
                f"""
                SELECT d.environment_service_id, d.rollout_started_at
                {_JOINS}
                WHERE d.id = :id AND {_VISIBILITY}
                """
            ),
            params,
        )
    ).first()
    if anchor is None:
        return None
    if anchor[0] is None:
        return []

    rows = (
        await connection.execute(
            text(
                """
                SELECT i.id, i.state, i.severity, i.title, i.primary_reason, i.opened_at
                FROM incidents i
                WHERE i.environment_service_id = :es
                  AND i.opened_at >= :from_dt
                  AND i.opened_at <= :from_dt + interval '2 hours'
                ORDER BY i.opened_at DESC
                LIMIT 20
                """
            ),
            {"es": anchor[0], "from_dt": anchor[1]},
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            "state": row[1],
            "severity": row[2],
            "title": row[3],
            "primary_reason": row[4],
            "opened_at": row[5].isoformat(),
        }
        for row in rows
    ]
