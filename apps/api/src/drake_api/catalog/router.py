"""Scoped catalog read API.

Semantics per ADR-0014: collections return authorized-only 200 (empty is an
honest answer); out-of-scope details are a consistent 404; counts, search and
cursors are computed after authorization. Responses carry provenance and
`as_of`; they never carry config refs, credentials, subjects, or raw errors.
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
from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.catalog.authz import escape_like, visible_scope_ids
from drake_api.catalog.external_runtime import (
    EXTERNAL_NOT_APPLICABLE,
    HealthSourceStatus,
    RuntimeKind,
    dependency_is_workload,
    evaluate_external_health,
    metrics_profile_state,
    workload_applicability,
)
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["catalog"])

_MAX_PAGE = 100
_DEFAULT_PAGE = 25

OPERATIONAL_CAPABILITIES = {
    "telemetry": "prometheus",
    "inventory": "cluster-agent",
    "deployment": "github",
    "protection": "backup-reporter",
}


def _as_of() -> str:
    return datetime.now(UTC).isoformat()


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


def _provenance(row: Any, offset: int) -> dict[str, Any]:
    return {
        "kind": row[offset],
        "ref": row[offset + 1],
        "revision": row[offset + 2],
        "accepted_at": row[offset + 3].isoformat(),
    }


async def _operational_states(connection: AsyncConnection, scope_id: uuid.UUID) -> dict[str, str]:
    """Honest capability states from integrations at this scope.

    No integration row, or a not_configured one → `not_configured`; a
    configured integration without observations → `unknown`. Nothing here
    can produce `healthy` in Sprint 2.
    """
    rows = (
        await connection.execute(
            text(
                "SELECT integration_type, configuration_state, observed_state "
                "FROM integrations WHERE scope_id = :scope_id AND lifecycle = 'active'"
            ),
            {"scope_id": scope_id},
        )
    ).all()
    by_type = {row[0]: (row[1], row[2]) for row in rows}
    states: dict[str, str] = {}
    for capability, integration_type in OPERATIONAL_CAPABILITIES.items():
        entry = by_type.get(integration_type)
        if entry is None or entry[0] == "not_configured":
            states[capability] = "not_configured"
        else:
            states[capability] = entry[1]  # observed_state: unknown/stale/…
    return states


async def _visible_project_ids(
    connection: AsyncConnection, auth: AuthContext
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """(fully-visible project ids, breadcrumb-only project ids)."""
    project_scopes = await visible_scope_ids(connection, auth.principal, "project.view")
    env_scopes = await visible_scope_ids(connection, auth.principal, "environment.view")

    full = {
        row[0]
        for row in (
            await connection.execute(
                text("SELECT id FROM projects WHERE scope_id = ANY(:scopes)"),
                {"scopes": list(project_scopes) or [uuid.UUID(int=0)]},
            )
        ).all()
    }
    breadcrumb = {
        row[0]
        for row in (
            await connection.execute(
                text("SELECT DISTINCT project_id FROM environments WHERE scope_id = ANY(:scopes)"),
                {"scopes": list(env_scopes) or [uuid.UUID(int=0)]},
            )
        ).all()
    } - full
    return full, breadcrumb


async def _project_payload(
    connection: AsyncConnection,
    auth: AuthContext,
    row: Any,
    *,
    include_details: bool,
) -> dict[str, Any]:
    project_id = row[0]
    env_scopes = await visible_scope_ids(connection, auth.principal, "environment.view")
    env_count = (
        await connection.execute(
            text(
                "SELECT count(*) FROM environments "
                "WHERE project_id = :pid AND lifecycle = 'active' "
                "AND scope_id = ANY(:scopes)"
            ),
            {"pid": project_id, "scopes": list(env_scopes) or [uuid.UUID(int=0)]},
        )
    ).scalar_one()
    binding_count = (
        await connection.execute(
            text(
                """
                SELECT count(*) FROM environment_services b
                JOIN environments e ON e.id = b.environment_id
                WHERE e.project_id = :pid AND b.lifecycle = 'active'
                  AND e.scope_id = ANY(:scopes)
                """
            ),
            {"pid": project_id, "scopes": list(env_scopes) or [uuid.UUID(int=0)]},
        )
    ).scalar_one()

    payload: dict[str, Any] = {
        "id": str(project_id),
        "project_key": row[1],
        "display_name": row[2],
        "lifecycle": row[3],
        "criticality": row[4],
        "tenant_model": row[5],
        "repository": {
            "provider": row[6],
            "owner": row[7],
            "name": row[8],
            "default_branch": row[9],
        },
        "version": row[14],
        "scope": {"type": "project", "ref": row[1]},
        "source": _provenance(row, 10),
        "counts": {
            "environments": int(env_count),
            "services": int(binding_count),
        },
        "as_of": _as_of(),
    }

    # Dependencies are visible exactly when their PROJECT is — this function
    # is only reached for a project the principal can already see, so no
    # second scope check is needed and none is invented. `include_details`
    # keeps the list off the collection endpoint.
    if include_details:
        payload["dependencies"] = [
            {
                "id": str(dep[0]),
                "dependency_key": dep[1],
                "display_name": dep[2],
                "dependency_class": dep[3],
                "engine": dep[4],
                "scope": dep[5],
                # `unknown` rather than null: a provider nobody recorded is a
                # real answer, and the closed vocabulary has a word for it.
                "provider": dep[6] or "unknown",
                "verification": dep[7],
                # Class-aware, and the same value the plan reports. An
                # in-cluster datastore IS a workload; only what a provider
                # runs is not_applicable.
                "workload_applicability": str(workload_applicability(dep[3])),
                # The external evaluator answers for things Drake does not
                # run. An in-cluster datastore's health comes from the
                # existing workload path, so this does not speak for it.
                **(
                    {
                        "health": evaluate_external_health(
                            source=HealthSourceStatus.NOT_CONFIGURED
                        ).as_dict()
                    }
                    if not dependency_is_workload(dep[3])
                    else {}
                ),
            }
            for dep in (
                await connection.execute(
                    text(
                        "SELECT id, dependency_key, display_name, dependency_class, engine, "
                        "store_scope, provider, verification FROM project_dependencies "
                        "WHERE project_id = :pid AND lifecycle = 'active' "
                        "ORDER BY dependency_key"
                    ),
                    {"pid": project_id},
                )
            ).all()
        ]

    if include_details:
        owners = (
            await connection.execute(
                text(
                    "SELECT team_key, owner_role FROM project_owners "
                    "WHERE project_id = :pid ORDER BY owner_role, team_key"
                ),
                {"pid": project_id},
            )
        ).all()
        payload["owners"] = [{"team": o[0], "role": o[1]} for o in owners]
        payload["operational"] = await _operational_states(connection, row[15])
    return payload


_PROJECT_COLUMNS = """
    p.id, p.project_key, p.display_name, p.lifecycle, p.criticality, p.tenant_model,
    p.repo_provider, p.repo_owner, p.repo_name, p.default_branch,
    p.catalog_source_kind, p.catalog_source_ref, p.source_revision, p.accepted_at,
    p.version, p.scope_id
"""


@router.get("/projects")
async def list_projects(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    limit: int = Query(default=_DEFAULT_PAGE, ge=1, le=_MAX_PAGE),
    cursor: str | None = None,
    search: str | None = Query(default=None, min_length=2, max_length=64),
    lifecycle: str = Query(default="active", pattern="^(active|archived|all)$"),
    criticality: str | None = Query(default=None, pattern="^(low|medium|high|critical)$"),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        full, breadcrumb = await _visible_project_ids(connection, auth)
        allowed = list(full | breadcrumb)
        if not allowed:
            return {"projects": [], "next_cursor": None, "as_of": _as_of()}

        conditions = ["p.id = ANY(:allowed)"]
        params: dict[str, Any] = {"allowed": allowed, "limit": limit + 1}
        if lifecycle != "all":
            conditions.append("p.lifecycle = :lifecycle")
            params["lifecycle"] = lifecycle
        if criticality:
            conditions.append("p.criticality = :criticality")
            params["criticality"] = criticality
        if search:
            conditions.append(
                "(p.project_key ILIKE :term ESCAPE '\\' OR p.display_name ILIKE :term ESCAPE '\\')"
            )
            params["term"] = f"%{escape_like(search)}%"
        if cursor:
            cursor_key, cursor_id = _decode_cursor(cursor, 2)
            conditions.append("(p.project_key, p.id) > (:cursor_key, CAST(:cursor_id AS uuid))")
            params["cursor_key"] = cursor_key
            params["cursor_id"] = cursor_id

        rows = (
            await connection.execute(
                text(
                    f"SELECT {_PROJECT_COLUMNS} FROM projects p "  # noqa: S608
                    f"WHERE {' AND '.join(conditions)} "
                    "ORDER BY p.project_key, p.id LIMIT :limit"
                ),
                params,
            )
        ).all()

        page = rows[:limit]
        projects = [
            await _project_payload(connection, auth, row, include_details=False) for row in page
        ]
    next_cursor = (
        _encode_cursor(page[-1][1], str(page[-1][0])) if len(rows) > limit and page else None
    )
    return {"projects": projects, "next_cursor": next_cursor, "as_of": _as_of()}


@router.get("/projects/{project_id}")
async def get_project(
    request: Request, project_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        full, breadcrumb = await _visible_project_ids(connection, auth)
        if project_id not in full and project_id not in breadcrumb:
            raise HTTPException(status_code=404, detail="not found")
        row = (
            await connection.execute(
                text(f"SELECT {_PROJECT_COLUMNS} FROM projects p WHERE p.id = :id"),  # noqa: S608
                {"id": project_id},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        return await _project_payload(connection, auth, row, include_details=True)


# NOTE: callers append `p.project_key` AFTER this list, so read it as
# `row[-1]`. Indexing it by number broke silently when `hosting_provider`
# was appended here — the payload still rendered, with `None` as the
# project key.
_ENV_COLUMNS = """
    e.id, e.environment_key, e.runtime, e.branch, e.criticality, e.namespace,
    e.lifecycle, e.catalog_source_kind, e.catalog_source_ref, e.source_revision,
    e.accepted_at, e.version, c.cluster_ref, c.display_name, e.project_id, e.scope_id,
    e.hosting_provider
"""


def _environment_payload(row: Any, project_key: str) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "environment_key": row[1],
        "runtime": row[2],
        "branch": row[3],
        "criticality": row[4],
        "namespace": row[5],
        "lifecycle": row[6],
        "hosting_provider": row[16],
        "cluster": ({"ref": row[12], "display_name": row[13]} if row[12] is not None else None),
        # Explicit, additive, and backward compatible: `cluster` and
        # `namespace` keep their existing types, and a client that knows
        # about this list can tell "this runtime has no such concept" from
        # "nobody recorded one". Conflating those is what makes an external
        # application look like a Kubernetes one that lost its cluster.
        "not_applicable": sorted(EXTERNAL_NOT_APPLICABLE) if row[2] == RuntimeKind.EXTERNAL else [],
        # Computed by the domain state machine, not asserted by the UI. No
        # observation system exists for an external runtime yet, so this is
        # honestly unknown/unavailable — but it is unknown because the
        # function said so, and the day a source appears the same call
        # starts returning a real verdict without the UI changing.
        **(
            {"health": evaluate_external_health(source=HealthSourceStatus.NOT_CONFIGURED).as_dict()}
            if row[2] == RuntimeKind.EXTERNAL
            else {}
        ),
        "version": row[11],
        "scope": {"type": "environment", "ref": f"{project_key}/{row[1]}"},
        "source": {
            "kind": row[7],
            "ref": row[8],
            "revision": row[9],
            "accepted_at": row[10].isoformat(),
        },
        "as_of": _as_of(),
    }


async def _authorized_environment(
    connection: AsyncConnection,
    auth: AuthContext,
    project_id: uuid.UUID,
    environment_id: uuid.UUID,
) -> tuple[Any, str]:
    env_scopes = await visible_scope_ids(connection, auth.principal, "environment.view")
    row = (
        await connection.execute(
            text(
                f"SELECT {_ENV_COLUMNS}, p.project_key "  # noqa: S608
                "FROM environments e "
                "JOIN projects p ON p.id = e.project_id "
                "LEFT JOIN clusters c ON c.id = e.cluster_id "
                "WHERE e.id = :eid AND e.project_id = :pid AND e.scope_id = ANY(:scopes)"
            ),
            {
                "eid": environment_id,
                "pid": project_id,
                "scopes": list(env_scopes) or [uuid.UUID(int=0)],
            },
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return row, str(row[-1])


@router.get("/projects/{project_id}/environments")
async def list_environments(
    request: Request,
    project_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    limit: int = Query(default=_DEFAULT_PAGE, ge=1, le=_MAX_PAGE),
    cursor: str | None = None,
    search: str | None = Query(default=None, min_length=2, max_length=64),
    lifecycle: str = Query(default="active", pattern="^(active|archived|all)$"),
    criticality: str | None = Query(default=None, pattern="^(low|medium|high|critical)$"),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        # Authorization first: project visibility, then the environment-scope
        # filter inside the query — before search, ordering, and the cursor.
        full, breadcrumb = await _visible_project_ids(connection, auth)
        if project_id not in full and project_id not in breadcrumb:
            raise HTTPException(status_code=404, detail="not found")
        env_scopes = await visible_scope_ids(connection, auth.principal, "environment.view")

        conditions = ["e.project_id = :pid", "e.scope_id = ANY(:scopes)"]
        params: dict[str, Any] = {
            "pid": project_id,
            "scopes": list(env_scopes) or [uuid.UUID(int=0)],
            "limit": limit + 1,
        }
        if lifecycle != "all":
            conditions.append("e.lifecycle = :lifecycle")
            params["lifecycle"] = lifecycle
        if criticality:
            conditions.append("e.criticality = :criticality")
            params["criticality"] = criticality
        if search:
            conditions.append("e.environment_key ILIKE :term ESCAPE '\\'")
            params["term"] = f"%{escape_like(search)}%"
        if cursor:
            cursor_key, cursor_id = _decode_cursor(cursor, 2)
            conditions.append("(e.environment_key, e.id) > (:cursor_key, CAST(:cursor_id AS uuid))")
            params["cursor_key"] = cursor_key
            params["cursor_id"] = cursor_id

        rows = (
            await connection.execute(
                text(
                    f"SELECT {_ENV_COLUMNS}, p.project_key "  # noqa: S608
                    "FROM environments e "
                    "JOIN projects p ON p.id = e.project_id "
                    "LEFT JOIN clusters c ON c.id = e.cluster_id "
                    f"WHERE {' AND '.join(conditions)} "
                    "ORDER BY e.environment_key, e.id LIMIT :limit"
                ),
                params,
            )
        ).all()
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1][1], str(page[-1][0])) if len(rows) > limit and page else None
        )
        return {
            "environments": [_environment_payload(row, str(row[-1])) for row in page],
            "next_cursor": next_cursor,
            "as_of": _as_of(),
        }


@router.get("/projects/{project_id}/environments/{environment_id}")
async def get_environment(
    request: Request,
    project_id: uuid.UUID,
    environment_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        row, project_key = await _authorized_environment(
            connection, auth, project_id, environment_id
        )
        payload = _environment_payload(row, project_key)
        payload["operational"] = {
            "workloads": "not_configured",
            "targets": "not_configured",
            "quotas": "not_configured",
            "drift": "not_configured",
        }
        return payload


@router.get("/projects/{project_id}/environments/{environment_id}/services")
async def list_services(
    request: Request,
    project_id: uuid.UUID,
    environment_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    limit: int = Query(default=_DEFAULT_PAGE, ge=1, le=_MAX_PAGE),
    cursor: str | None = None,
    search: str | None = Query(default=None, min_length=2, max_length=64),
    lifecycle: str = Query(default="active", pattern="^(active|archived|all)$"),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        # Environment authorization happens before any pagination input is
        # even parsed against data — cursors cannot probe other environments.
        env_row, project_key = await _authorized_environment(
            connection, auth, project_id, environment_id
        )
        environment_key = str(env_row[1])

        conditions = ["b.environment_id = :eid"]
        params: dict[str, Any] = {"eid": environment_id, "limit": limit + 1}
        if lifecycle != "all":
            conditions.append("b.lifecycle = :lifecycle")
            params["lifecycle"] = lifecycle
        if search:
            conditions.append(
                "(s.service_key ILIKE :term ESCAPE '\\' OR s.display_name ILIKE :term ESCAPE '\\')"
            )
            params["term"] = f"%{escape_like(search)}%"
        if cursor:
            cursor_key, cursor_id = _decode_cursor(cursor, 2)
            conditions.append("(s.service_key, b.id) > (:cursor_key, CAST(:cursor_id AS uuid))")
            params["cursor_key"] = cursor_key
            params["cursor_id"] = cursor_id

        rows = (
            await connection.execute(
                text(
                    """
                    SELECT b.id, s.service_key, s.display_name, s.component, s.runtime,
                           s.metrics_profile, b.lifecycle, s.version,
                           s.catalog_source_kind, s.catalog_source_ref,
                           s.source_revision, s.accepted_at
                    FROM environment_services b
                    JOIN service_definitions s ON s.id = b.service_id
                    WHERE {conditions}
                    ORDER BY s.service_key, b.id LIMIT :limit
                    """.format(conditions=" AND ".join(conditions))  # noqa: S608
                ),
                params,
            )
        ).all()
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1][1], str(page[-1][0])) if len(rows) > limit and page else None
        )
        return {
            "services": [
                {
                    "id": str(row[0]),
                    "service_key": row[1],
                    "display_name": row[2],
                    "component": row[3],
                    "runtime": row[4],
                    "metrics_profile": metrics_profile_state(row[5])[0],
                    "lifecycle": row[6],
                    "version": row[7],
                    "scope": {
                        "type": "service",
                        "ref": f"{project_key}/{environment_key}/{row[1]}",
                    },
                    "source": {
                        "kind": row[8],
                        "ref": row[9],
                        "revision": row[10],
                        "accepted_at": row[11].isoformat(),
                    },
                }
                for row in page
            ],
            "next_cursor": next_cursor,
            "as_of": _as_of(),
        }


@router.get("/projects/{project_id}/environments/{environment_id}/services/{service_binding_id}")
async def get_service(
    request: Request,
    project_id: uuid.UUID,
    environment_id: uuid.UUID,
    service_binding_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        _row, project_key = await _authorized_environment(
            connection, auth, project_id, environment_id
        )
        row = (
            await connection.execute(
                text(
                    """
                    SELECT b.id, s.service_key, s.display_name, s.component, s.runtime,
                           s.metrics_profile, s.workload_selector, s.health, b.lifecycle,
                           s.catalog_source_kind, s.catalog_source_ref, s.source_revision,
                           s.accepted_at, s.version
                    FROM environment_services b
                    JOIN service_definitions s ON s.id = b.service_id
                    WHERE b.id = :bid AND b.environment_id = :eid
                    """
                ),
                {"bid": service_binding_id, "eid": environment_id},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        environment_key = _row[1]
        return {
            "id": str(row[0]),
            "service_key": row[1],
            "display_name": row[2],
            "component": row[3],
            "runtime": row[4],
            "metrics_profile": metrics_profile_state(row[5])[0],
            "workload_selector": row[6],
            "health": row[7],
            "lifecycle": row[8],
            "scope": {
                "type": "service",
                "ref": f"{project_key}/{environment_key}/{row[1]}",
            },
            "source": {
                "kind": row[9],
                "ref": row[10],
                "revision": row[11],
                "accepted_at": row[12].isoformat(),
            },
            "version": row[13],
            "operational": {
                "metrics": "not_configured",
                "logs": "not_configured",
                "traces": "not_configured",
                "deployments": "not_configured",
            },
            "as_of": _as_of(),
        }


_CLUSTER_COLUMNS = """
    c.id, c.cluster_ref, c.display_name, c.site, c.lifecycle,
    c.catalog_source_kind, c.catalog_source_ref, c.source_revision, c.accepted_at,
    c.version, c.scope_id
"""


def _cluster_payload(row: Any, observation: dict[str, Any] | None = None) -> dict[str, Any]:
    # Capability states come from REAL agent observation (ADR-0017 §4):
    # no enrolled agent stays not_configured; a heartbeat alone never
    # upgrades inventory beyond what snapshots/events actually delivered.
    observation = observation or {
        "agent": {"status": "not_configured"},
        "inventory": {"state": "not_configured"},
    }
    return {
        "id": str(row[0]),
        "cluster_ref": row[1],
        "display_name": row[2],
        "site": row[3],
        "lifecycle": row[4],
        "version": row[9],
        "scope": {"type": "cluster", "ref": row[1]},
        "source": _provenance(row, 5),
        "operational": {
            "agent": observation["agent"]["status"],
            "inventory": observation["inventory"]["state"],
        },
        "agent_observation": observation,
        "as_of": _as_of(),
    }


@router.get("/clusters")
async def list_clusters(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    limit: int = Query(default=_DEFAULT_PAGE, ge=1, le=_MAX_PAGE),
    cursor: str | None = None,
    search: str | None = Query(default=None, min_length=2, max_length=64),
    lifecycle: str = Query(default="active", pattern="^(active|archived|all)$"),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        cluster_scopes = await visible_scope_ids(connection, auth.principal, "cluster.view")
        if not cluster_scopes:
            return {"clusters": [], "next_cursor": None, "as_of": _as_of()}
        params: dict[str, Any] = {"scopes": list(cluster_scopes), "limit": limit + 1}
        conditions = ["c.scope_id = ANY(:scopes)"]
        if lifecycle != "all":
            conditions.append("c.lifecycle = :lifecycle")
            params["lifecycle"] = lifecycle
        if search:
            conditions.append(
                "(c.cluster_ref ILIKE :term ESCAPE '\\' OR c.display_name ILIKE :term ESCAPE '\\')"
            )
            params["term"] = f"%{escape_like(search)}%"
        if cursor:
            cursor_ref, cursor_id = _decode_cursor(cursor, 2)
            conditions.append("(c.cluster_ref, c.id) > (:cursor_ref, CAST(:cursor_id AS uuid))")
            params["cursor_ref"] = cursor_ref
            params["cursor_id"] = cursor_id
        rows = (
            await connection.execute(
                text(
                    f"SELECT {_CLUSTER_COLUMNS} FROM clusters c "  # noqa: S608
                    f"WHERE {' AND '.join(conditions)} "
                    "ORDER BY c.cluster_ref, c.id LIMIT :limit"
                ),
                params,
            )
        ).all()
        page = rows[:limit]
        observations = await agent_observations(connection, [row[0] for row in page])
    next_cursor = (
        _encode_cursor(page[-1][1], str(page[-1][0])) if len(rows) > limit and page else None
    )
    return {
        "clusters": [_cluster_payload(row, observations.get(row[0])) for row in page],
        "next_cursor": next_cursor,
        "as_of": _as_of(),
    }


@router.get("/clusters/{cluster_id}")
async def get_cluster(
    request: Request, cluster_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        cluster_scopes = await visible_scope_ids(connection, auth.principal, "cluster.view")
        row = (
            await connection.execute(
                text(
                    f"SELECT {_CLUSTER_COLUMNS} FROM clusters c "  # noqa: S608
                    "WHERE c.id = :id AND c.scope_id = ANY(:scopes)"
                ),
                {"id": cluster_id, "scopes": list(cluster_scopes) or [uuid.UUID(int=0)]},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="not found")
        observations = await agent_observations(connection, [row[0]])
        payload = _cluster_payload(row, observations.get(row[0]))

        env_scopes = await visible_scope_ids(connection, auth.principal, "environment.view")
        referencing = (
            await connection.execute(
                text(
                    """
                    SELECT p.project_key, e.environment_key, e.namespace
                    FROM environments e JOIN projects p ON p.id = e.project_id
                    WHERE e.cluster_id = :cid AND e.lifecycle = 'active'
                      AND e.scope_id = ANY(:scopes)
                    ORDER BY p.project_key, e.environment_key
                    """
                ),
                {"cid": cluster_id, "scopes": list(env_scopes) or [uuid.UUID(int=0)]},
            )
        ).all()
        payload["referenced_environments"] = [
            {"project_key": row[0], "environment_key": row[1], "namespace": row[2]}
            for row in referencing
        ]
        return payload


@router.get("/catalog/search")
async def catalog_search(
    request: Request,
    auth: AuthContext = Depends(require_auth),
    q: str = Query(min_length=2, max_length=64),
    limit: int = Query(default=20, ge=1, le=20),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    term = f"%{escape_like(q)}%"
    async with engine.connect() as connection:
        full, breadcrumb = await _visible_project_ids(connection, auth)
        project_ids = list(full | breadcrumb) or [uuid.UUID(int=0)]
        env_scopes = list(
            await visible_scope_ids(connection, auth.principal, "environment.view")
        ) or [uuid.UUID(int=0)]
        cluster_scopes = list(
            await visible_scope_ids(connection, auth.principal, "cluster.view")
        ) or [uuid.UUID(int=0)]

        rows = (
            await connection.execute(
                text(
                    """
                    (SELECT 'project' AS kind, p.id, p.project_key AS key,
                            p.display_name, p.project_key AS project_key,
                            NULL AS parent_id, p.id AS project_id
                     FROM projects p
                     WHERE p.id = ANY(:project_ids) AND p.lifecycle = 'active'
                       AND (p.project_key ILIKE :term ESCAPE '\\'
                            OR p.display_name ILIKE :term ESCAPE '\\'))
                    UNION ALL
                    (SELECT 'environment', e.id, e.environment_key,
                            e.environment_key, p.project_key, e.project_id, p.id
                     FROM environments e JOIN projects p ON p.id = e.project_id
                     WHERE e.scope_id = ANY(:env_scopes) AND e.lifecycle = 'active'
                       AND e.environment_key ILIKE :term ESCAPE '\\')
                    UNION ALL
                    (SELECT 'service', b.id, s.service_key, s.display_name,
                            p.project_key, b.environment_id, p.id
                     FROM environment_services b
                     JOIN environments e ON e.id = b.environment_id
                     JOIN projects p ON p.id = e.project_id
                     JOIN service_definitions s ON s.id = b.service_id
                     WHERE e.scope_id = ANY(:env_scopes) AND b.lifecycle = 'active'
                       AND (s.service_key ILIKE :term ESCAPE '\\'
                            OR s.display_name ILIKE :term ESCAPE '\\'))
                    UNION ALL
                    (SELECT 'cluster', c.id, c.cluster_ref, c.display_name,
                            NULL, NULL, NULL
                     FROM clusters c
                     WHERE c.scope_id = ANY(:cluster_scopes) AND c.lifecycle = 'active'
                       AND (c.cluster_ref ILIKE :term ESCAPE '\\'
                            OR c.display_name ILIKE :term ESCAPE '\\'))
                    ORDER BY kind, project_key NULLS FIRST, key, id LIMIT :limit
                    """
                ),
                {
                    "project_ids": project_ids,
                    "env_scopes": env_scopes,
                    "cluster_scopes": cluster_scopes,
                    "term": term,
                    "limit": limit,
                },
            )
        ).all()
    return {
        "results": [
            {
                "kind": row[0],
                "id": str(row[1]),
                "key": row[2],
                "display_name": row[3],
                "project_key": row[4],
                "parent_id": str(row[5]) if row[5] else None,
                "project_id": str(row[6]) if row[6] else None,
            }
            for row in rows
        ],
        "as_of": _as_of(),
    }


@router.get("/catalog/context")
async def catalog_context(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Authorized catalog counts for the shell/Command Center."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        full, breadcrumb = await _visible_project_ids(connection, auth)
        env_scopes = list(
            await visible_scope_ids(connection, auth.principal, "environment.view")
        ) or [uuid.UUID(int=0)]
        cluster_scopes = list(
            await visible_scope_ids(connection, auth.principal, "cluster.view")
        ) or [uuid.UUID(int=0)]
        environment_count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM environments "
                    "WHERE scope_id = ANY(:scopes) AND lifecycle = 'active'"
                ),
                {"scopes": env_scopes},
            )
        ).scalar_one()
        cluster_count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM clusters "
                    "WHERE scope_id = ANY(:scopes) AND lifecycle = 'active'"
                ),
                {"scopes": cluster_scopes},
            )
        ).scalar_one()
    return {
        "projects": len(full | breadcrumb),
        "environments": int(environment_count),
        "clusters": int(cluster_count),
        "as_of": _as_of(),
    }
