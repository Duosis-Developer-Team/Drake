"""An environment's scheduled jobs: its CronJobs, their recent runs, their logs.

A CronJob is not a service. It answers no requests and keeps no replicas
up, so it has no service binding and the service-health engine never
judges it. It still belongs to the environment it runs in, and "did last
night's cleanup run?" is a question about that environment. In
production on 2026-11-16, hermes-dev's nightly cleanup had failed every
run for three months, and nothing on the environment's page said so.

Everything here is read from what Drake already holds:

- **The CronJobs:** the cluster agent's inventory, in the environment's
  cluster and namespace (`inventory_resources`, kind CronJob). Each one's
  health comes from the inventory's own rule (agents.health_rules).
- **Their recent runs:** the inventory's Jobs that the CronJob owns. The
  CronJob controller names each Job `<cronjob>-<scheduled minute>`, so the
  name carries when the run was scheduled.
- **Their logs:** the store, by `k8s.cronjob.name`. The log agent writes it
  on every line of a run's pods (deploy/otel).

Visibility is the environment's: `environment.view` to see the jobs, and
`logs.view` at the environment (or above) to read their logs. A CronJob
spans the environment, not one service, so a grant on one service is not
enough.

Nothing here writes, and nothing comes from the request except the
environment id, the CronJob's name (which must be one of the environment's
own CronJobs), and the bounded log filters.
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.catalog.authz import visible_scope_ids
from drake_api.catalog.router import _authorized_environment
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.observability.logs import (
    DEFAULT_LOG_RANGE,
    LogQueryError,
    build_log_query,
    next_cursor,
    normalize_rows,
)
from drake_api.observability.store import (
    ClickHouseStore,
    StoreContractError,
    StoreUnavailableError,
)
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["catalog"])

MAX_CRONJOBS = 50
RECENT_RUNS = 5
# A namespace keeps a few finished Jobs per CronJob (its history limits).
# The read is bounded anyway, so a namespace with a runaway history cannot
# make this page heavy.
MAX_JOBS_READ = 500

_NOT_FOUND = "not found"
_NAME = re.compile(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?")
# `<cronjob>-<minutes since the epoch>`, as the CronJob controller names it.
_SCHEDULED_SUFFIX = re.compile(r"-(\d{7,10})$")


async def _environment_place(
    connection: AsyncConnection, environment_id: uuid.UUID
) -> tuple[uuid.UUID | None, str | None, Any]:
    row = (
        await connection.execute(
            text(
                "SELECT e.cluster_id, e.namespace, e.scope_id, c.cluster_ref "
                "FROM environments e LEFT JOIN clusters c ON c.id = e.cluster_id "
                "WHERE e.id = :id"
            ),
            {"id": environment_id},
        )
    ).one()
    return row[0], row[1], row


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _outcome(status: dict[str, Any]) -> str:
    def count(key: str) -> int:
        value = status.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    if count("active") > 0:
        return "running"
    if count("failed") > 0:
        return "failed"
    if count("succeeded") > 0:
        return "succeeded"
    return "unknown"


def _scheduled_at(job_name: str) -> str | None:
    match = _SCHEDULED_SUFFIX.search(job_name)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)) * 60, UTC).isoformat()


def summarize(cronjobs: list[Any], jobs: list[Any]) -> list[dict[str, Any]]:
    """CronJob rows `(name, health, reasons, payload)` and Job rows
    `(name, payload)` into the page's shape, newest runs first."""
    runs: dict[str, list[dict[str, Any]]] = {}
    for name, payload in jobs:
        payload = payload if isinstance(payload, dict) else {}
        owner = next(
            (
                o.get("name")
                for o in payload.get("owners") or []
                if isinstance(o, dict) and o.get("kind") == "CronJob"
            ),
            None,
        )
        if not isinstance(owner, str):
            continue
        status = payload.get("status_summary") or {}
        runs.setdefault(owner, []).append(
            {
                "name": str(name),
                "scheduled_at": _scheduled_at(str(name)),
                "outcome": _outcome(status if isinstance(status, dict) else {}),
            }
        )
    out = []
    for name, health, reasons, payload in cronjobs:
        payload = payload if isinstance(payload, dict) else {}
        spec = payload.get("spec_summary") or {}
        status = payload.get("status_summary") or {}
        recent = sorted(
            runs.get(str(name), []),
            key=lambda r: (r["scheduled_at"] or "", r["name"]),
            reverse=True,
        )[:RECENT_RUNS]
        out.append(
            {
                "name": str(name),
                "schedule": _text(spec.get("schedule")),
                "suspended": spec.get("suspend") is True,
                "health": str(health or "unknown"),
                "reasons": [str(r) for r in (reasons or []) if isinstance(r, str)],
                "last_schedule_time": _text(status.get("last_schedule_time")),
                "last_successful_time": _text(status.get("last_successful_time")),
                "recent_runs": recent,
            }
        )
    return out


@router.get("/projects/{project_id}/environments/{environment_id}/scheduled-jobs")
async def list_scheduled_jobs(
    request: Request,
    project_id: uuid.UUID,
    environment_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The environment's CronJobs, each with its health and recent runs."""
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        await _authorized_environment(connection, auth, project_id, environment_id)
        cluster_id, namespace, _place = await _environment_place(connection, environment_id)
        base = {"environment_id": str(environment_id), "as_of": datetime.now(UTC).isoformat()}
        if cluster_id is None or not namespace:
            # An external runtime has no CronJobs to read: not "none".
            return {**base, "state": "not_applicable", "jobs": []}
        cronjobs = (
            await connection.execute(
                text(
                    """
                    SELECT name, health, health_reasons, payload
                    FROM inventory_resources
                    WHERE cluster_id = :cluster AND namespace = :namespace
                      AND kind = 'CronJob' AND lifecycle = 'active'
                    ORDER BY name
                    LIMIT :limit
                    """
                ),
                {"cluster": cluster_id, "namespace": namespace, "limit": MAX_CRONJOBS},
            )
        ).all()
        jobs = (
            await connection.execute(
                text(
                    """
                    SELECT name, payload
                    FROM inventory_resources
                    WHERE cluster_id = :cluster AND namespace = :namespace
                      AND kind = 'Job' AND lifecycle = 'active'
                    ORDER BY name DESC
                    LIMIT :limit
                    """
                ),
                {"cluster": cluster_id, "namespace": namespace, "limit": MAX_JOBS_READ},
            )
        ).all()
    return {
        **base,
        "state": "ok",
        "jobs": summarize([tuple(r) for r in cronjobs], [tuple(r) for r in jobs]),
    }


@router.get("/projects/{project_id}/environments/{environment_id}/scheduled-jobs/{name}/logs")
async def scheduled_job_logs(
    request: Request,
    project_id: uuid.UUID,
    environment_id: uuid.UUID,
    name: str,
    range_key: str = Query(DEFAULT_LOG_RANGE, alias="range", max_length=8),
    severity: str = Query("any", max_length=8),
    q: str = Query("", max_length=100),
    limit: int = Query(100, ge=1, le=200),
    before: datetime | None = Query(None),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Log lines of every run of one of the environment's CronJobs."""
    if not _NAME.fullmatch(name):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        await _authorized_environment(connection, auth, project_id, environment_id)
        cluster_id, namespace, place = await _environment_place(connection, environment_id)
        log_scopes = await visible_scope_ids(connection, auth.principal, "logs.view")
        known = (
            await connection.execute(
                text(
                    "SELECT 1 FROM inventory_resources WHERE cluster_id = :cluster "
                    "AND namespace = :namespace AND kind = 'CronJob' AND name = :name "
                    "AND lifecycle = 'active'"
                ),
                {"cluster": cluster_id, "namespace": namespace, "name": name},
            )
        ).first()
    # Not one of this environment's CronJobs, or logs not readable here:
    # the same answer, so neither can be probed.
    if known is None or place[2] not in log_scopes or not place[3]:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "environment_id": str(environment_id),
        "cronjob": name,
        "range": range_key,
        "as_of": now.isoformat(),
        "entries": [],
        "next_before": None,
    }
    store: ClickHouseStore = request.app.state.observability_store
    connector = store.connector
    if not settings.observability_logs_enabled or connector is None:
        return {**base, "state": "not_configured"}
    try:
        query = build_log_query(
            database=connector.database,
            binding={
                "namespace": namespace,
                "workload_kind": "CronJob",
                "workload_name": name,
                "cluster": {"cluster_ref": place[3]},
            },
            range_key=range_key,
            severity=severity,
            term=q,
            limit=limit,
            before=before,
            now=now,
        )
    except LogQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        result = await store.query(
            query.statement,
            query.parameters,
            max_rows=query.limit,
            timeout_seconds=connector.timeout_seconds,
            correlation_id=correlation_id_var.get() or "",
        )
        entries = normalize_rows(result.rows)
    except StoreUnavailableError as error:
        return {**base, "state": "unavailable", "reason": error.code}
    except (StoreContractError, ValueError) as error:
        code = error.code if isinstance(error, StoreContractError) else "store_malformed_row"
        return {**base, "state": "unavailable", "reason": code}
    return {
        **base,
        "state": "ok",
        "window": {"start": query.start.isoformat(), "end": query.end.isoformat()},
        "entries": entries,
        "next_before": next_cursor(entries, query.limit),
    }
