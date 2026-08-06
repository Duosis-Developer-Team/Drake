"""Scoped cluster inventory read API (Sprint 4, section J semantics).

Same contract discipline as the catalog: `cluster.view` decides visibility,
out-of-scope anything is a uniform 404, counts/cursors are computed after
authorization, and every response carries `as_of` + freshness provenance.
Nothing here fabricates metrics: only observed inventory is served, and
unknown/stale are first-class states, never dressed as healthy.
"""

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.agents.observation import agent_observations
from drake_api.agents.router_ingest import ALLOWED_KINDS
from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.catalog.authz import escape_like, visible_scope_ids
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1/clusters", tags=["cluster-inventory"])

_MAX_PAGE = 100
_DEFAULT_PAGE = 50

_WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob")


def _as_of() -> str:
    return datetime.now(UTC).isoformat()


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


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


async def _authorized_cluster(
    connection: AsyncConnection, auth: AuthContext, cluster_id: uuid.UUID
) -> None:
    """Uniform 404 for missing and out-of-scope clusters alike."""
    scopes = await visible_scope_ids(connection, auth.principal, "cluster.view")
    row = (
        await connection.execute(
            text("SELECT 1 FROM clusters WHERE id = :id AND scope_id = ANY(:scopes)"),
            {"id": cluster_id, "scopes": list(scopes) or [uuid.UUID(int=0)]},
        )
    ).first()
    if row is None:
        raise _not_found()


@router.get("/{cluster_id}/inventory/summary")
async def inventory_summary(
    request: Request,
    cluster_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        await _authorized_cluster(connection, auth, cluster_id)
        observation = (await agent_observations(connection, [cluster_id]))[cluster_id]

        kind_rows = (
            await connection.execute(
                text(
                    """
                    SELECT kind, health, count(*) FROM inventory_resources
                    WHERE cluster_id = :cluster_id AND lifecycle = 'active'
                    GROUP BY kind, health
                    """
                ),
                {"cluster_id": cluster_id},
            )
        ).all()
        by_kind: dict[str, dict[str, int]] = {}
        for kind, health, count in kind_rows:
            entry = by_kind.setdefault(
                str(kind), {"total": 0, "healthy": 0, "degraded": 0, "unhealthy": 0, "unknown": 0}
            )
            entry["total"] += int(count)
            entry[str(health)] += int(count)

        missing_count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM inventory_resources "
                    "WHERE cluster_id = :cluster_id AND lifecycle = 'missing'"
                ),
                {"cluster_id": cluster_id},
            )
        ).scalar_one()

        pod_signal_rows = (
            await connection.execute(
                text(
                    """
                    SELECT
                        count(*) FILTER (
                            WHERE health_reasons @> '["crashloop_backoff"]'::jsonb
                        ),
                        count(*) FILTER (WHERE health_reasons @> '["oom_killed"]'::jsonb),
                        COALESCE(sum(
                            CASE
                                WHEN jsonb_typeof(payload->'status_summary'->'restarts')
                                     = 'number'
                                THEN (payload->'status_summary'->>'restarts')::numeric
                                ELSE 0
                            END
                        ), 0)
                    FROM inventory_resources
                    WHERE cluster_id = :cluster_id AND kind = 'Pod'
                      AND lifecycle = 'active'
                    """
                ),
                {"cluster_id": cluster_id},
            )
        ).one()

    workloads = {"total": 0, "healthy": 0, "degraded": 0, "unhealthy": 0, "unknown": 0}
    for kind in _WORKLOAD_KINDS:
        for key, value in by_kind.get(kind, {}).items():
            workloads[key] += value

    def _rollup(kind: str) -> dict[str, int]:
        return by_kind.get(
            kind, {"total": 0, "healthy": 0, "degraded": 0, "unhealthy": 0, "unknown": 0}
        )

    return {
        "cluster_id": str(cluster_id),
        "agent": observation["agent"],
        "inventory": {
            **observation["inventory"],
            "active_resources": sum(entry["total"] for entry in by_kind.values()),
            "missing_resources": int(missing_count),
        },
        "nodes": _rollup("Node"),
        "namespaces": _rollup("Namespace"),
        "pods": {
            **_rollup("Pod"),
            "crashloop": int(pod_signal_rows[0]),
            "oom_killed": int(pod_signal_rows[1]),
            "restarts": int(pod_signal_rows[2]),
        },
        "workloads": workloads,
        "persistent_volume_claims": _rollup("PersistentVolumeClaim"),
        "by_kind": {kind: entry for kind, entry in sorted(by_kind.items())},
        "as_of": _as_of(),
    }


@router.get("/{cluster_id}/inventory/resources")
async def list_inventory_resources(
    request: Request,
    cluster_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    kind: str | None = Query(default=None, min_length=1, max_length=64),
    namespace: str | None = Query(default=None, min_length=1, max_length=63),
    health: str | None = Query(default=None, pattern="^(healthy|degraded|unhealthy|unknown)$"),
    lifecycle: str = Query(default="active", pattern="^(active|missing|all)$"),
    search: str | None = Query(default=None, min_length=2, max_length=64),
    limit: int = Query(default=_DEFAULT_PAGE, ge=1, le=_MAX_PAGE),
    cursor: str | None = None,
) -> dict[str, Any]:
    if kind is not None and kind not in ALLOWED_KINDS:
        raise HTTPException(status_code=422, detail="unknown kind")
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        await _authorized_cluster(connection, auth, cluster_id)

        params: dict[str, Any] = {"cluster_id": cluster_id, "limit": limit + 1}
        conditions = ["cluster_id = :cluster_id"]
        if kind is not None:
            conditions.append("kind = :kind")
            params["kind"] = kind
        if namespace is not None:
            conditions.append("namespace = :namespace")
            params["namespace"] = namespace
        if health is not None:
            conditions.append("health = :health")
            params["health"] = health
        if lifecycle != "all":
            conditions.append("lifecycle = :lifecycle")
            params["lifecycle"] = lifecycle
        if search:
            conditions.append("name ILIKE :term ESCAPE '\\'")
            params["term"] = f"%{escape_like(search)}%"
        if cursor:
            parts = _decode_cursor(cursor, 4)
            conditions.append(
                "(kind, COALESCE(namespace, ''), name, id) > "
                "(:cursor_kind, :cursor_namespace, :cursor_name, CAST(:cursor_id AS uuid))"
            )
            params["cursor_kind"] = parts[0]
            params["cursor_namespace"] = parts[1]
            params["cursor_name"] = parts[2]
            params["cursor_id"] = parts[3]

        rows = (
            await connection.execute(
                text(
                    "SELECT id, api_group, api_version, kind, namespace, name, "  # noqa: S608
                    "health, health_reasons, lifecycle, observed_at, last_seen_at, "
                    "payload->'status_summary' "
                    f"FROM inventory_resources WHERE {' AND '.join(conditions)} "
                    "ORDER BY kind, COALESCE(namespace, ''), name, id LIMIT :limit"
                ),
                params,
            )
        ).all()
        observation = (await agent_observations(connection, [cluster_id]))[cluster_id]

    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = _encode_cursor(str(last[3]), str(last[4] or ""), str(last[5]), str(last[0]))
    return {
        "cluster_id": str(cluster_id),
        "resources": [
            {
                "id": str(row[0]),
                "api_group": row[1],
                "api_version": row[2],
                "kind": row[3],
                "namespace": row[4],
                "name": row[5],
                "health": row[6],
                "health_reasons": row[7],
                "lifecycle": row[8],
                "observed_at": row[9].isoformat(),
                "last_seen_at": row[10].isoformat(),
                "status_summary": row[11] or {},
            }
            for row in page
        ],
        "next_cursor": next_cursor,
        "inventory": observation["inventory"],
        "as_of": _as_of(),
    }


@router.get("/{cluster_id}/inventory/resources/{resource_id}")
async def get_inventory_resource(
    request: Request,
    cluster_id: uuid.UUID,
    resource_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        await _authorized_cluster(connection, auth, cluster_id)
        row = (
            await connection.execute(
                text(
                    """
                    SELECT id, api_group, api_version, kind, namespace, name, uid,
                           resource_version, payload, health, health_reasons,
                           lifecycle, first_seen_at, last_seen_at, observed_at,
                           last_snapshot_id
                    FROM inventory_resources
                    WHERE id = :id AND cluster_id = :cluster_id
                    """
                ),
                {"id": resource_id, "cluster_id": cluster_id},
            )
        ).first()
        if row is None:
            raise _not_found()
        observation = (await agent_observations(connection, [cluster_id]))[cluster_id]

    payload = row[8] if isinstance(row[8], dict) else json.loads(row[8])
    return {
        "id": str(row[0]),
        "cluster_id": str(cluster_id),
        "api_group": row[1],
        "api_version": row[2],
        "kind": row[3],
        "namespace": row[4],
        "name": row[5],
        "uid": row[6],
        "resource_version": row[7],
        "labels": payload.get("labels", {}),
        "annotations": payload.get("annotations", {}),
        "owners": payload.get("owners", []),
        "spec_summary": payload.get("spec_summary", {}),
        "status_summary": payload.get("status_summary", {}),
        "conditions": payload.get("conditions", []),
        "health": row[9],
        "health_reasons": row[10],
        "lifecycle": row[11],
        "first_seen_at": row[12].isoformat(),
        "last_seen_at": row[13].isoformat(),
        "observed_at": row[14].isoformat(),
        "provenance": {
            "source": "cluster-agent",
            "last_snapshot_id": str(row[15]) if row[15] else None,
        },
        "inventory": observation["inventory"],
        "as_of": _as_of(),
    }
