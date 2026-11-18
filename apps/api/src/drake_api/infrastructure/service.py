"""Build and serve the infrastructure snapshot (ADR-0032).

Every number comes through the telemetry broker: the same templates,
authorization, budgets and per-template cache as every other screen. This
module adds two things on top.

- **A fixed query set.** A snapshot is 17 template queries whatever the
  number of applications: per-node series and capacities, and per
  namespace-on-node usage, requests, limits, running pods and restarts.
  Prometheus runs on a busy node, so the count must not grow with the estate.
- **Stale-while-refresh.** The assembled snapshot is kept for a minute and
  then served as it is while one refresh runs in the background, so the
  screen opens at once and a crowd of viewers causes one refresh, not one
  each. A namespace's pods are fetched only when someone opens it.

Queries run two at a time: one viewer holds at most two of the broker's
per-principal leases, so other screens keep working while this one loads.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.catalog.authz import visible_scope_ids
from drake_api.infrastructure.model import (
    DAY,
    add_series,
    by_labels,
    daily_growth,
    grid,
    group_of,
    last_value,
    on_grid,
    step_for,
)
from drake_api.rbac.service import Principal
from drake_api.telemetry.broker import QueryInput, TelemetryBroker

logger = logging.getLogger("drake_api.infrastructure")

FRESH_SECONDS = 60
KEEP_SECONDS = 15 * 60
QUERY_CONCURRENCY = 2
# A namespace's pods are what a person waits for after a click: three at a
# time still leaves a lease free under the broker's per-principal four.
POD_QUERY_CONCURRENCY = 3
CURRENT_WINDOW = 600  # "now" values: the last point of a 10-minute window

# Templates read as a 24-hour series (the last point is "now").
NODE_SERIES = {
    "cpu": "cluster.node-cpu-utilization.v1",
    "memory": "cluster.node-memory-utilization.v1",
    "disk": "cluster.node-root-disk-used.v1",
    "load5": "cluster.node-load5.v1",
}
NAMESPACE_SERIES = {
    "cpu": "cluster.namespace-cpu-usage.v1",
    "memory": "cluster.namespace-memory-usage.v1",
}
# Templates read for their current value only.
NODE_CURRENT = {
    "cores": "cluster.node-cpu-cores.v1",
    "cpu_allocatable": "cluster.node-cpu-allocatable.v1",
    "memory_allocatable": "cluster.node-memory-allocatable.v1",
    "pods_allocatable": "cluster.node-pods-allocatable.v1",
    "disk_size": "cluster.node-root-disk-size.v1",
}
NAMESPACE_CURRENT = {
    "cpu_requested": "cluster.namespace-cpu-requested.v1",
    "memory_requested": "cluster.namespace-memory-requested.v1",
    "cpu_limit": "cluster.namespace-cpu-limit.v1",
    "memory_limit": "cluster.namespace-memory-limit.v1",
    "pods": "cluster.namespace-running-pods.v1",
    "restarts_24h": "cluster.namespace-restarts-24h.v1",
}
POD_TEMPLATES = {
    "cpu": "namespace.pod-cpu-usage.v1",
    "memory": "namespace.pod-memory-usage.v1",
    "cpu_requested": "namespace.pod-cpu-requested.v1",
    "cpu_limit": "namespace.pod-cpu-limit.v1",
    "memory_limit": "namespace.pod-memory-limit.v1",
    "restarts_24h": "namespace.pod-restarts-24h.v1",
}

# Strong references to background refreshes, so they are not collected mid-run.
_REFRESHING: set[asyncio.Task[None]] = set()


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


async def visible_clusters(engine: AsyncEngine, principal: Principal) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        scopes = await visible_scope_ids(connection, principal, "cluster.view")
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT c.id, c.cluster_ref, c.display_name,
                           EXISTS (SELECT 1 FROM integrations i
                                   WHERE i.scope_id = c.scope_id
                                     AND i.integration_type = 'prometheus'
                                     AND i.lifecycle = 'active'
                                     AND i.configuration_state = 'configured') AS configured,
                           (SELECT count(*) FROM inventory_resources r
                            WHERE r.cluster_id = c.id AND r.kind = 'Node'
                              AND r.lifecycle = 'active') AS nodes
                    FROM clusters c
                    WHERE c.lifecycle = 'active' AND c.scope_id = ANY(:scopes)
                    ORDER BY c.display_name, c.cluster_ref
                    """
                ),
                {"scopes": list(scopes) or [uuid.UUID(int=0)]},
            )
        ).all()
    return [
        {
            "id": str(r[0]),
            "ref": r[1],
            "name": r[2],
            "metrics_configured": bool(r[3]),
            "nodes": int(r[4]),
        }
        for r in rows
    ]


async def _authorized_cluster(
    engine: AsyncEngine, principal: Principal, cluster_id: uuid.UUID
) -> Any:
    async with engine.connect() as connection:
        scopes = await visible_scope_ids(connection, principal, "cluster.view")
        row = (
            await connection.execute(
                text(
                    "SELECT id, cluster_ref, display_name, scope_id FROM clusters "
                    "WHERE id = :id AND lifecycle = 'active' AND scope_id = ANY(:scopes)"
                ),
                {"id": cluster_id, "scopes": list(scopes) or [uuid.UUID(int=0)]},
            )
        ).first()
    if row is None:
        raise _not_found()
    return row


async def _query(
    broker: TelemetryBroker,
    principal: Principal,
    gate: asyncio.Semaphore,
    template: str,
    scope_type: str,
    scope_id: uuid.UUID,
    from_ts: int,
    to_ts: int,
    step: int,
) -> dict[str, Any] | None:
    """One broker query. A failure is a missing piece, never a failed screen."""
    async with gate:
        try:
            return await broker.query(
                principal,
                QueryInput(
                    template_key=template,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    from_dt=datetime.fromtimestamp(from_ts, tz=UTC),
                    to_dt=datetime.fromtimestamp(to_ts, tz=UTC),
                    step_seconds=step,
                    parameters={},
                ),
            )
        except HTTPException as error:
            if error.status_code == 404:
                # The cluster is visible but telemetry on it is not granted.
                return {"data_state": "denied"}
            logger.warning("infrastructure: a query was refused (%s)", error.status_code)
            return None


def _usable(envelope: dict[str, Any] | None) -> bool:
    return envelope is not None and envelope.get("data_state") in ("ok", "empty")


async def _catalog_context(
    engine: AsyncEngine, principal: Principal, cluster_id: uuid.UUID
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int]:
    """Projects that claim this cluster's namespaces, the nodes the agent
    reports, and how many namespaces there are (to size the query step)."""
    async with engine.connect() as connection:
        env_visible = await visible_scope_ids(connection, principal, "environment.view")
        envs = (
            await connection.execute(
                text(
                    """
                    SELECT e.namespace, e.id, e.environment_key, e.scope_id,
                           p.id, p.project_key, p.display_name
                    FROM environments e JOIN projects p ON p.id = e.project_id
                    WHERE e.cluster_id = :c AND e.lifecycle = 'active' AND e.namespace IS NOT NULL
                    """
                ),
                {"c": cluster_id},
            )
        ).all()
        nodes = (
            await connection.execute(
                text(
                    """
                    SELECT name, payload->'spec_summary'->>'kubelet_version', health
                    FROM inventory_resources
                    WHERE cluster_id = :c AND kind = 'Node' AND lifecycle = 'active'
                    """
                ),
                {"c": cluster_id},
            )
        ).all()
        namespaces = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM inventory_resources "
                    "WHERE cluster_id = :c AND kind = 'Namespace' AND lifecycle = 'active'"
                ),
                {"c": cluster_id},
            )
        ).scalar_one()
    claimed: dict[str, dict[str, Any]] = {}
    for ns, env_id, env_key, env_scope, project_id, project_key, project_name in envs:
        # A project the viewer cannot see is not named: its namespace is
        # shown as an unclaimed namespace, with the usage the cluster grant
        # already allows, and no link.
        if env_scope in env_visible:
            claimed[str(ns)] = {
                "project_id": str(project_id),
                "project_key": project_key,
                "project_name": project_name,
                "environment_id": str(env_id),
                "environment_key": env_key,
            }
    node_info = {str(n): {"kubelet_version": v, "health": h} for n, v, h in nodes}
    return claimed, node_info, int(namespaces)


async def build_snapshot(
    engine: AsyncEngine, broker: TelemetryBroker, principal: Principal, cluster: Any
) -> dict[str, Any]:
    cluster_id, cluster_ref, cluster_name, _cluster_scope = cluster
    claimed, node_info, namespace_count = await _catalog_context(engine, principal, cluster_id)
    node_count = max(1, len(node_info))
    now = int(time.time())
    node_step = step_for(node_count)
    # A namespace is counted once per node it could run on; capped by the
    # broker's own series ceiling, beyond which the query is refused anyway.
    ns_step = step_for(min(200, max(1, namespace_count) * node_count))
    gate = asyncio.Semaphore(QUERY_CONCURRENCY)

    def q(template: str, from_ts: int, step: int) -> Any:
        return _query(broker, principal, gate, template, "cluster", cluster_id, from_ts, now, step)

    jobs: dict[str, Any] = {}
    for name, template in NODE_SERIES.items():
        jobs[f"node:{name}"] = q(template, now - DAY, node_step)
    for name, template in NAMESPACE_SERIES.items():
        jobs[f"ns:{name}"] = q(template, now - DAY, ns_step)
    for name, template in NODE_CURRENT.items():
        jobs[f"nodec:{name}"] = q(template, now - CURRENT_WINDOW, 300)
    for name, template in NAMESPACE_CURRENT.items():
        jobs[f"nsc:{name}"] = q(template, now - CURRENT_WINDOW, 300)
    results = dict(zip(jobs, await asyncio.gather(*jobs.values()), strict=True))
    missing = sorted(k for k, v in results.items() if not _usable(v))
    if all(not _usable(v) for v in results.values()):
        states = {v.get("data_state") for v in results.values() if v}
        state = (
            "denied"
            if "denied" in states
            else "not_configured"
            if "not_configured" in states
            else "unavailable"
        )
        return {
            "cluster": {"id": str(cluster_id), "ref": cluster_ref, "name": cluster_name},
            "state": state,
            "built_at": datetime.now(UTC).isoformat(),
            "nodes": [],
            "namespaces": [],
            "times": [],
            "step_seconds": ns_step,
            "missing": missing,
        }

    node_times = grid(now - DAY, now, node_step)
    ns_times = grid(now - DAY, now, ns_step)

    node_series = {k: by_labels(results[f"node:{k}"], "node") for k in NODE_SERIES}
    node_current = {k: by_labels(results[f"nodec:{k}"], "node") for k in NODE_CURRENT}
    names = sorted(
        set(node_info)
        | {key[0] for series in node_series.values() for key in series}
        | {key[0] for series in node_current.values() for key in series}
    )

    ns_series = {k: by_labels(results[f"ns:{k}"], "namespace", "node") for k in NAMESPACE_SERIES}
    ns_current = {k: by_labels(results[f"nsc:{k}"], "namespace", "node") for k in NAMESPACE_CURRENT}
    pairs = sorted(
        {key for d in ns_series.values() for key in d}
        | {key for d in ns_current.values() for key in d}
    )
    namespaces = []
    for ns, node in pairs:
        if not ns or not node:
            continue
        cpu = on_grid(ns_series["cpu"].get((ns, node), []), ns_times)
        memory = on_grid(ns_series["memory"].get((ns, node), []), ns_times)
        current = {k: last_value(ns_current[k].get((ns, node), [])) for k in NAMESPACE_CURRENT}
        project = claimed.get(ns)
        namespaces.append(
            {
                "namespace": ns,
                "node": node,
                "group": group_of(ns, project),
                "project": project,
                "cpu": {"now": last_value(ns_series["cpu"].get((ns, node), [])), "series": cpu},
                "memory": {
                    "now": last_value(ns_series["memory"].get((ns, node), [])),
                    "series": memory,
                },
                **current,
            }
        )

    nodes = []
    for name in names:
        info = node_info.get(name, {})
        disk = on_grid(node_series["disk"].get((name,), []), node_times)
        here = [n for n in namespaces if n["node"] == name]
        nodes.append(
            {
                "name": name,
                "kubelet_version": info.get("kubelet_version"),
                "health": info.get("health", "unknown"),
                "cores": last_value(node_current["cores"].get((name,), [])),
                "cpu": {
                    "now": last_value(node_series["cpu"].get((name,), [])),
                    "series": on_grid(node_series["cpu"].get((name,), []), node_times),
                    "allocatable": last_value(node_current["cpu_allocatable"].get((name,), [])),
                    "requested": _sum(here, "cpu_requested"),
                },
                "memory": {
                    "now": last_value(node_series["memory"].get((name,), [])),
                    "series": on_grid(node_series["memory"].get((name,), []), node_times),
                    "allocatable": last_value(node_current["memory_allocatable"].get((name,), [])),
                    "requested": _sum(here, "memory_requested"),
                },
                "disk": {
                    "now": last_value(node_series["disk"].get((name,), [])),
                    "series": disk,
                    "size_bytes": last_value(node_current["disk_size"].get((name,), [])),
                    "growth_per_day": daily_growth(disk, node_step),
                },
                "load5": {
                    "now": last_value(node_series["load5"].get((name,), [])),
                    "series": on_grid(node_series["load5"].get((name,), []), node_times),
                },
                "pods": {
                    "running": _sum(here, "pods"),
                    "allocatable": last_value(node_current["pods_allocatable"].get((name,), [])),
                },
            }
        )
    return {
        "cluster": {"id": str(cluster_id), "ref": cluster_ref, "name": cluster_name},
        "state": "partial" if missing else "ok",
        "built_at": datetime.now(UTC).isoformat(),
        "node_times": node_times,
        "node_step_seconds": node_step,
        "times": ns_times,
        "step_seconds": ns_step,
        "nodes": nodes,
        "namespaces": namespaces,
        "missing": missing,
    }


def _pod_cpu_now(pod: dict[str, Any]) -> float:
    cpu = pod.get("cpu") or {}
    value = cpu.get("now") if isinstance(cpu, dict) else None
    return -float(value) if isinstance(value, int | float) else 0.0


def _sum(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(values), 6) if values else None


class InfrastructureService:
    def __init__(self, engine: AsyncEngine, broker: TelemetryBroker, redis: aioredis.Redis) -> None:
        self._engine = engine
        self._broker = broker
        self._redis = redis

    @staticmethod
    def _key(cluster_id: uuid.UUID) -> str:
        return f"infrastructure:snapshot:v1:{cluster_id}"

    async def _read(self, cluster_id: uuid.UUID) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(self._key(cluster_id))
        except Exception:
            return None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    async def _write(self, cluster_id: uuid.UUID, snapshot: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await self._redis.set(
                self._key(cluster_id),
                json.dumps({"stored_at": time.time(), "snapshot": snapshot}),
                ex=KEEP_SECONDS,
            )

    async def _refresh(self, principal: Principal, cluster: Any) -> dict[str, Any]:
        snapshot = await build_snapshot(self._engine, self._broker, principal, cluster)
        if snapshot["state"] in ("ok", "partial"):
            await self._write(cluster[0], snapshot)
        return snapshot

    async def _refresh_in_background(self, principal: Principal, cluster: Any) -> None:
        lock = f"infrastructure:refresh:v1:{cluster[0]}"
        try:
            acquired = await self._redis.set(lock, "1", nx=True, ex=90)
        except Exception:
            return
        if not acquired:
            return

        async def run() -> None:
            try:
                await self._refresh(principal, cluster)
            except Exception:
                logger.warning("infrastructure: background refresh failed")
            finally:
                with contextlib.suppress(Exception):
                    await self._redis.delete(lock)

        task = asyncio.create_task(run(), name="infrastructure-refresh")
        _REFRESHING.add(task)
        task.add_done_callback(_REFRESHING.discard)

    async def snapshot(self, principal: Principal, cluster_id: uuid.UUID) -> dict[str, Any]:
        cluster = await _authorized_cluster(self._engine, principal, cluster_id)
        # The cluster grant is checked above for every caller; the stored
        # snapshot holds nothing a cluster viewer may not see except project
        # names, which are re-filtered per caller below.
        cached = await self._read(cluster_id)
        if cached is not None:
            age = time.time() - float(cached.get("stored_at", 0))
            if age >= FRESH_SECONDS:
                await self._refresh_in_background(principal, cluster)
            snapshot = cached["snapshot"]
            return await self._for_caller(
                principal, snapshot, age=age, refreshing=age >= FRESH_SECONDS
            )
        snapshot = await self._refresh(principal, cluster)
        return await self._for_caller(principal, snapshot, age=0.0, refreshing=False)

    async def _for_caller(
        self, principal: Principal, snapshot: dict[str, Any], *, age: float, refreshing: bool
    ) -> dict[str, Any]:
        """Drop project names and links this caller may not see.

        The stored snapshot may have been built for someone with a wider
        grant; its usage numbers are cluster-level and fine for any cluster
        viewer, but a project's name and ids are not.
        """
        async with self._engine.connect() as connection:
            visible = {
                str(s) for s in await visible_scope_ids(connection, principal, "environment.view")
            }
            env_ids = [
                uuid.UUID(n["project"]["environment_id"])
                for n in snapshot.get("namespaces", [])
                if n.get("project")
            ]
            scopes = {}
            if env_ids:
                rows = (
                    await connection.execute(
                        text("SELECT id, scope_id FROM environments WHERE id = ANY(:ids)"),
                        {"ids": env_ids},
                    )
                ).all()
                scopes = {str(r[0]): str(r[1]) for r in rows}
        namespaces = []
        for n in snapshot.get("namespaces", []):
            project = n.get("project")
            if project and scopes.get(project["environment_id"]) not in visible:
                n = {**n, "project": None, "group": group_of(n["namespace"], None)}
            namespaces.append(n)
        return {
            **snapshot,
            "namespaces": namespaces,
            "age_seconds": round(age, 1),
            "refreshing": refreshing,
        }

    async def namespace_pods(
        self, principal: Principal, cluster_id: uuid.UUID, namespace: str
    ) -> dict[str, Any]:
        await _authorized_cluster(self._engine, principal, cluster_id)
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT id FROM inventory_resources WHERE cluster_id = :c "
                        "AND kind = 'Namespace' AND coalesce(namespace, '') = '' "
                        "AND name = :n AND lifecycle = 'active'"
                    ),
                    {"c": cluster_id, "n": namespace},
                )
            ).first()
            if row is None:
                raise _not_found()
            pod_count = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM inventory_resources WHERE cluster_id = :c "
                        "AND kind = 'Pod' AND namespace = :n AND lifecycle = 'active'"
                    ),
                    {"c": cluster_id, "n": namespace},
                )
            ).scalar_one()
        scope_id = row[0]
        now = int(time.time())
        step = step_for(min(200, max(1, int(pod_count))))
        gate = asyncio.Semaphore(POD_QUERY_CONCURRENCY)
        jobs = {
            name: _query(
                self._broker,
                principal,
                gate,
                template,
                "namespace",
                scope_id,
                now - (DAY if name == "cpu" else CURRENT_WINDOW),
                now,
                step if name == "cpu" else 300,
            )
            for name, template in POD_TEMPLATES.items()
        }
        results = dict(zip(jobs, await asyncio.gather(*jobs.values()), strict=True))
        times = grid(now - DAY, now, step)
        cpu = by_labels(results["cpu"], "pod", "node")
        current = {k: by_labels(results[k], "pod") for k in POD_TEMPLATES if k != "cpu"}
        names = sorted({p for p, _ in cpu} | {key[0] for d in current.values() for key in d})
        pods = []
        for name in names:
            node_points = [(node, pts) for (p, node), pts in cpu.items() if p == name]
            series: list[float | None] = [None] * len(times)
            for _node, pts in node_points:
                series = add_series(series, on_grid(pts, times))
            pods.append(
                {
                    "pod": name,
                    "node": node_points[0][0] if node_points else None,
                    "cpu": {
                        "now": next((v for v in reversed(series) if v is not None), None),
                        "series": series,
                    },
                    "memory": last_value(current["memory"].get((name,), [])),
                    "cpu_requested": last_value(current["cpu_requested"].get((name,), [])),
                    "cpu_limit": last_value(current["cpu_limit"].get((name,), [])),
                    "memory_limit": last_value(current["memory_limit"].get((name,), [])),
                    "restarts_24h": last_value(current["restarts_24h"].get((name,), [])),
                }
            )
        pods.sort(key=_pod_cpu_now)
        missing = sorted(k for k, v in results.items() if not _usable(v))
        return {
            "namespace": namespace,
            "times": times,
            "step_seconds": step,
            "pods": pods,
            "state": "partial" if missing else "ok",
            "missing": missing,
        }
