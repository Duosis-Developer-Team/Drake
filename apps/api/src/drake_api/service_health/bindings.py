"""Service ↔ workload bindings: create, validate, resolve, list.

A binding is the join between the catalog and the cluster inventory. Two
things it is careful about:

- **It stores no query.** The workload is identified by validated fields;
  which metrics are read comes from the curated registry. There is no path
  from a binding to arbitrary PromQL.
- **It is scope-filtered in SQL.** Visibility is applied before counting or
  paging, so a caller cannot learn that a service exists by its absence
  from a total.

Resolution against inventory is deliberately non-destructive: a workload
that has not been seen is `unresolved`, and one that stops being seen keeps
its binding. Inventory going stale is a telemetry problem, not a reason to
forget an operator's configuration.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.catalog.authz import visible_scope_ids
from drake_api.rbac.service import Principal
from drake_api.service_health.policy import DEFAULT_POLICY_KEY, policy_keys

WORKLOAD_KINDS: frozenset[str] = frozenset({"Deployment", "StatefulSet", "DaemonSet"})

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


class BindingError(ValueError):
    """A binding the caller asked for that Drake will not create."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BindingTarget:
    environment_service_id: uuid.UUID
    cluster_id: uuid.UUID
    namespace: str
    workload_kind: str
    workload_name: str
    preset_key: str
    health_policy_key: str = DEFAULT_POLICY_KEY
    integration_id: uuid.UUID | None = None


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    """Never let an empty visibility set become an unfiltered query."""
    return list(ids) or [uuid.UUID(int=0)]


def validate_target(target: BindingTarget, known_presets: frozenset[str]) -> None:
    """Reject anything that could not be a valid workload reference.

    These checks mirror the database constraints on purpose. The database
    is the guarantee; this is the readable error.
    """
    if target.workload_kind not in WORKLOAD_KINDS:
        raise BindingError(
            "unsupported_workload_kind",
            f"workload kind must be one of {sorted(WORKLOAD_KINDS)}",
        )
    if target.health_policy_key not in policy_keys():
        raise BindingError("unknown_health_policy", "unknown health policy")
    if target.preset_key not in known_presets:
        raise BindingError("unknown_preset", "unknown metric preset")
    if not 1 <= len(target.namespace) <= 63:
        raise BindingError("invalid_namespace", "namespace is not a valid Kubernetes name")
    if not 1 <= len(target.workload_name) <= 253:
        raise BindingError("invalid_workload_name", "workload name is not a valid Kubernetes name")


async def _catalog_context(
    connection: AsyncConnection, environment_service_id: uuid.UUID
) -> dict[str, Any] | None:
    """The project/environment/service a binding must stay consistent with."""
    row = (
        await connection.execute(
            text(
                """
                SELECT es.id, es.scope_id, e.id, e.environment_key, p.id, p.project_key,
                       sd.id, sd.service_key
                FROM environment_services es
                JOIN environments e ON e.id = es.environment_id
                JOIN projects p ON p.id = e.project_id
                JOIN service_definitions sd ON sd.id = es.service_id
                WHERE es.id = :id AND es.lifecycle = 'active'
                """
            ),
            {"id": environment_service_id},
        )
    ).first()
    if row is None:
        return None
    return {
        "environment_service_id": row[0],
        "scope_id": row[1],
        "environment_id": row[2],
        "environment_key": row[3],
        "project_id": row[4],
        "project_key": row[5],
        "service_id": row[6],
        "service_key": row[7],
    }


async def resolve_workload(
    connection: AsyncConnection,
    cluster_id: uuid.UUID,
    namespace: str,
    kind: str,
    name: str,
) -> dict[str, Any] | None:
    """Look the workload up in inventory.

    Returns None when it has not been observed. That is reported as
    `unresolved`, never as "does not exist": the agent may simply not have
    reported yet.
    """
    row = (
        await connection.execute(
            text(
                """
                SELECT uid, payload, last_seen_at, observed_at, health, lifecycle
                FROM inventory_resources
                WHERE cluster_id = :cluster_id AND namespace = :namespace
                  AND kind = :kind AND name = :name
                ORDER BY observed_at DESC
                LIMIT 1
                """
            ),
            {"cluster_id": cluster_id, "namespace": namespace, "kind": kind, "name": name},
        )
    ).first()
    if row is None:
        return None
    return {
        "uid": row[0],
        "payload": row[1] or {},
        "last_seen_at": row[2],
        "observed_at": row[3],
        "inventory_health": row[4],
        "lifecycle": row[5],
    }


async def create_binding(
    connection: AsyncConnection,
    principal: Principal,
    target: BindingTarget,
    known_presets: frozenset[str],
    actor_identity_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Create a binding, or explain precisely why not."""
    validate_target(target, known_presets)

    context = await _catalog_context(connection, target.environment_service_id)
    if context is None:
        # Uniform: an unknown id and an invisible one look the same.
        raise BindingError("not_found", "not found")

    # The caller must be able to manage the service AND see the cluster.
    manageable = await visible_scope_ids(connection, principal, "integration.manage")
    if context["scope_id"] not in manageable:
        raise BindingError("not_found", "not found")

    cluster = (
        await connection.execute(
            text(
                "SELECT scope_id, cluster_ref FROM clusters WHERE id = :id AND lifecycle = 'active'"
            ),
            {"id": target.cluster_id},
        )
    ).first()
    if cluster is None:
        raise BindingError("not_found", "not found")
    visible_clusters = await visible_scope_ids(connection, principal, "cluster.view")
    if cluster[0] not in visible_clusters:
        raise BindingError("not_found", "not found")

    existing = (
        await connection.execute(
            text(
                """
                SELECT id, lifecycle FROM service_workload_bindings
                WHERE environment_service_id = :es AND cluster_id = :cluster
                  AND namespace = :ns AND workload_kind = :kind AND workload_name = :name
                """
            ),
            {
                "es": target.environment_service_id,
                "cluster": target.cluster_id,
                "ns": target.namespace,
                "kind": target.workload_kind,
                "name": target.workload_name,
            },
        )
    ).first()
    if existing is not None:
        raise BindingError(
            "duplicate_binding",
            "this service is already bound to that workload",
        )

    resolved = await resolve_workload(
        connection,
        target.cluster_id,
        target.namespace,
        target.workload_kind,
        target.workload_name,
    )

    row = (
        await connection.execute(
            text(
                """
                INSERT INTO service_workload_bindings
                    (environment_service_id, project_id, environment_id, service_id,
                     cluster_id, namespace, workload_kind, workload_name,
                     resolved_resource_uid, resolved_at, preset_key, health_policy_key,
                     integration_id, created_by, updated_by)
                VALUES
                    (:es, :project, :environment, :service, :cluster, :ns, :kind, :name,
                     :uid, :resolved_at, :preset, :policy, :integration, :actor, :actor)
                RETURNING id, revision, created_at, updated_at
                """
            ),
            {
                "es": target.environment_service_id,
                "project": context["project_id"],
                "environment": context["environment_id"],
                "service": context["service_id"],
                "cluster": target.cluster_id,
                "ns": target.namespace,
                "kind": target.workload_kind,
                "name": target.workload_name,
                "uid": resolved["uid"] if resolved else None,
                "resolved_at": datetime.now(UTC) if resolved else None,
                "preset": target.preset_key,
                "policy": target.health_policy_key,
                "integration": target.integration_id,
                "actor": actor_identity_id,
            },
        )
    ).first()
    assert row is not None

    return {
        "id": str(row[0]),
        "revision": row[1],
        "resolved": resolved is not None,
        "scope_id": context["scope_id"],
        "created_at": row[2].isoformat(),
        "updated_at": row[3].isoformat(),
    }


async def set_lifecycle(
    connection: AsyncConnection,
    principal: Principal,
    binding_id: uuid.UUID,
    lifecycle: str,
    expected_revision: int | None,
    actor_identity_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Disable or re-enable. Never deletes: history stays readable."""
    if lifecycle not in ("active", "disabled"):
        raise BindingError("invalid_lifecycle", "lifecycle must be active or disabled")

    manageable = await visible_scope_ids(connection, principal, "integration.manage")
    row = (
        await connection.execute(
            text(
                """
                SELECT b.revision, b.lifecycle, es.scope_id
                FROM service_workload_bindings b
                JOIN environment_services es ON es.id = b.environment_service_id
                WHERE b.id = :id
                """
            ),
            {"id": binding_id},
        )
    ).first()
    if row is None or row[2] not in manageable:
        raise BindingError("not_found", "not found")

    if expected_revision is not None and expected_revision != row[0]:
        raise BindingError("revision_conflict", "the binding changed since it was read")

    updated = (
        await connection.execute(
            text(
                """
                UPDATE service_workload_bindings
                SET lifecycle = :lifecycle, revision = revision + 1,
                    updated_at = now(), updated_by = :actor
                WHERE id = :id
                RETURNING revision, lifecycle
                """
            ),
            {"id": binding_id, "lifecycle": lifecycle, "actor": actor_identity_id},
        )
    ).first()
    assert updated is not None
    return {
        "id": str(binding_id),
        "revision": updated[0],
        "lifecycle": updated[1],
        "changed": row[1] != lifecycle,
    }


async def update_binding(
    connection: AsyncConnection,
    principal: Principal,
    binding_id: uuid.UUID,
    preset_key: str,
    health_policy_key: str,
    expected_revision: int | None,
    known_presets: frozenset[str],
    actor_identity_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Change which metrics and thresholds a binding is read with.

    The workload itself is not editable: repointing a service at a different
    workload is a different binding, and keeping it that way means a health
    history always refers to one thing.
    """
    if preset_key not in known_presets:
        raise BindingError("unknown_preset", "unknown metric preset")
    if health_policy_key not in policy_keys():
        raise BindingError("unknown_health_policy", "unknown health policy")

    manageable = await visible_scope_ids(connection, principal, "integration.manage")
    row = (
        await connection.execute(
            text(
                """
                SELECT b.revision, b.preset_key, b.health_policy_key, es.scope_id
                FROM service_workload_bindings b
                JOIN environment_services es ON es.id = b.environment_service_id
                WHERE b.id = :id
                """
            ),
            {"id": binding_id},
        )
    ).first()
    if row is None or row[3] not in manageable:
        raise BindingError("not_found", "not found")

    if expected_revision is not None and expected_revision != row[0]:
        raise BindingError("revision_conflict", "the binding changed since it was read")

    changed = row[1] != preset_key or row[2] != health_policy_key
    if not changed:
        return {
            "id": str(binding_id),
            "revision": row[0],
            "preset_key": row[1],
            "health_policy_key": row[2],
            "changed": False,
        }

    # The revision bump is what invalidates every cached verdict computed
    # under the old preset or policy: it is part of the cache identity, so
    # the next read cannot address the previous answer.
    updated = (
        await connection.execute(
            text(
                """
                UPDATE service_workload_bindings
                SET preset_key = :preset, health_policy_key = :policy,
                    revision = revision + 1, updated_at = now(), updated_by = :actor
                WHERE id = :id
                RETURNING revision, preset_key, health_policy_key
                """
            ),
            {
                "id": binding_id,
                "preset": preset_key,
                "policy": health_policy_key,
                "actor": actor_identity_id,
            },
        )
    ).first()
    assert updated is not None
    return {
        "id": str(binding_id),
        "revision": updated[0],
        "preset_key": updated[1],
        "health_policy_key": updated[2],
        "changed": True,
    }


async def get_binding(
    connection: AsyncConnection, principal: Principal, binding_id: uuid.UUID
) -> dict[str, Any] | None:
    visible = await visible_scope_ids(connection, principal, "environment.view")
    row = (
        await connection.execute(
            text(
                """
                SELECT b.id, b.namespace, b.workload_kind, b.workload_name,
                       b.resolved_resource_uid, b.resolved_at, b.preset_key,
                       b.health_policy_key, b.lifecycle, b.revision,
                       b.created_at, b.updated_at,
                       c.cluster_ref, c.id,
                       p.project_key, e.environment_key, sd.service_key,
                       b.environment_service_id, b.integration_id
                FROM service_workload_bindings b
                JOIN environment_services es ON es.id = b.environment_service_id
                JOIN clusters c ON c.id = b.cluster_id
                JOIN projects p ON p.id = b.project_id
                JOIN environments e ON e.id = b.environment_id
                JOIN service_definitions sd ON sd.id = b.service_id
                WHERE b.id = :id AND es.scope_id = ANY(:visible)
                """
            ),
            {"id": binding_id, "visible": _sentinel(visible)},
        )
    ).first()
    if row is None:
        return None
    return _binding_payload(row)


def _binding_payload(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "namespace": row[1],
        "workload_kind": row[2],
        "workload_name": row[3],
        "resolution": {
            "resolved": row[4] is not None,
            "resource_uid": row[4],
            "resolved_at": row[5].isoformat() if row[5] else None,
        },
        "preset_key": row[6],
        "health_policy_key": row[7],
        "lifecycle": row[8],
        "revision": row[9],
        "created_at": row[10].isoformat(),
        "updated_at": row[11].isoformat(),
        "cluster": {"cluster_ref": row[12], "id": str(row[13])},
        "project_key": row[14],
        "environment_key": row[15],
        "service_key": row[16],
        "environment_service_id": str(row[17]),
        "integration_id": str(row[18]) if row[18] else None,
    }


async def list_bindings(
    connection: AsyncConnection,
    principal: Principal,
    *,
    environment_service_id: uuid.UUID | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    """Bounded, scope-filtered listing.

    The filter is applied inside the query that also produces the total, so
    the count cannot reveal rows the caller may not see.
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    visible = await visible_scope_ids(connection, principal, "environment.view")

    filters = "es.scope_id = ANY(:visible)"
    params: dict[str, Any] = {"visible": _sentinel(visible), "limit": limit, "offset": offset}
    if environment_service_id is not None:
        filters += " AND b.environment_service_id = :es"
        params["es"] = environment_service_id

    total = (
        await connection.execute(
            text(
                f"""
                SELECT count(*)
                FROM service_workload_bindings b
                JOIN environment_services es ON es.id = b.environment_service_id
                WHERE {filters}
                """  # noqa: S608 - `filters` is built from fixed fragments only
            ),
            params,
        )
    ).scalar_one()

    rows = (
        await connection.execute(
            text(
                f"""
                SELECT b.id, b.namespace, b.workload_kind, b.workload_name,
                       b.resolved_resource_uid, b.resolved_at, b.preset_key,
                       b.health_policy_key, b.lifecycle, b.revision,
                       b.created_at, b.updated_at,
                       c.cluster_ref, c.id,
                       p.project_key, e.environment_key, sd.service_key,
                       b.environment_service_id, b.integration_id
                FROM service_workload_bindings b
                JOIN environment_services es ON es.id = b.environment_service_id
                JOIN clusters c ON c.id = b.cluster_id
                JOIN projects p ON p.id = b.project_id
                JOIN environments e ON e.id = b.environment_id
                JOIN service_definitions sd ON sd.id = b.service_id
                WHERE {filters}
                ORDER BY b.created_at DESC, b.id
                LIMIT :limit OFFSET :offset
                """  # noqa: S608 - `filters` is built from fixed fragments only
            ),
            params,
        )
    ).all()

    return {
        "items": [_binding_payload(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def list_service_rows(
    connection: AsyncConnection,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    """Services in scope, each with the binding that answers for it.

    A service with no binding is listed with `binding: null` rather than
    omitted. A list that quietly dropped unbound services would make an
    unobserved estate look like a healthy one, which is the exact reading
    error this screen exists to prevent.
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    offset = max(0, offset)
    visible = await visible_scope_ids(connection, principal, "environment.view")

    filters = "es.scope_id = ANY(:visible) AND es.lifecycle = 'active'"
    params: dict[str, Any] = {"visible": _sentinel(visible), "limit": limit, "offset": offset}
    if project_id is not None:
        filters += " AND e.project_id = :project"
        params["project"] = project_id
    if environment_id is not None:
        filters += " AND es.environment_id = :environment"
        params["environment"] = environment_id

    total = (
        await connection.execute(
            text(
                f"""
                SELECT count(*)
                FROM environment_services es
                JOIN environments e ON e.id = es.environment_id
                WHERE {filters}
                """  # noqa: S608 - `filters` is built from fixed fragments only
            ),
            params,
        )
    ).scalar_one()

    rows = (
        await connection.execute(
            text(
                f"""
                SELECT es.id, p.id, p.project_key, e.id, e.environment_key,
                       sd.service_key, sd.display_name, sd.component,
                       b.id, b.lifecycle, b.namespace, b.workload_kind, b.workload_name,
                       b.resolved_resource_uid, b.preset_key, b.health_policy_key,
                       b.revision, c.cluster_ref, c.id
                FROM environment_services es
                JOIN environments e ON e.id = es.environment_id
                JOIN projects p ON p.id = e.project_id
                JOIN service_definitions sd ON sd.id = es.service_id
                -- One binding per service row, chosen deterministically:
                -- an active binding wins over a disabled one, then the most
                -- recently created. Which binding answered is always named
                -- in the response, never implied.
                LEFT JOIN LATERAL (
                    SELECT * FROM service_workload_bindings sb
                    WHERE sb.environment_service_id = es.id
                    ORDER BY (sb.lifecycle = 'active') DESC, sb.created_at DESC, sb.id
                    LIMIT 1
                ) b ON true
                LEFT JOIN clusters c ON c.id = b.cluster_id
                WHERE {filters}
                ORDER BY p.project_key, e.environment_key, sd.service_key
                LIMIT :limit OFFSET :offset
                """  # noqa: S608 - `filters` is built from fixed fragments only
            ),
            params,
        )
    ).all()

    return {
        "items": [
            {
                "environment_service_id": str(row[0]),
                "project_id": str(row[1]),
                "project_key": row[2],
                "environment_id": str(row[3]),
                "environment_key": row[4],
                "service_key": row[5],
                "display_name": row[6],
                "component": row[7],
                "binding": (
                    None
                    if row[8] is None
                    else {
                        "id": str(row[8]),
                        "lifecycle": row[9],
                        "namespace": row[10],
                        "workload_kind": row[11],
                        "workload_name": row[12],
                        "resolved": row[13] is not None,
                        "preset_key": row[14],
                        "health_policy_key": row[15],
                        "revision": row[16],
                        "cluster": {"cluster_ref": row[17], "id": str(row[18])},
                    }
                ),
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def binding_options(
    connection: AsyncConnection,
    principal: Principal,
    *,
    cluster_id: uuid.UUID | None,
    namespace: str | None,
) -> dict[str, Any]:
    """The choices a binding form may offer, one dependent level at a time.

    Every option comes from cluster inventory the caller can already see.
    That is what lets the form be a set of selects rather than a text box:
    there is nothing to type, so there is nothing to inject.
    """
    visible = await visible_scope_ids(connection, principal, "cluster.view")

    clusters = (
        await connection.execute(
            text(
                """
                SELECT id, cluster_ref, display_name
                FROM clusters
                WHERE lifecycle = 'active' AND scope_id = ANY(:visible)
                ORDER BY cluster_ref
                LIMIT :limit
                """
            ),
            {"visible": _sentinel(visible), "limit": MAX_PAGE_SIZE},
        )
    ).all()

    namespaces: list[str] = []
    workloads: list[dict[str, Any]] = []

    # A cluster the caller cannot see yields no namespaces — the same answer
    # as a cluster with none, so the form cannot be used to probe for one.
    authorized_cluster = cluster_id is not None and any(row[0] == cluster_id for row in clusters)

    if authorized_cluster:
        namespaces = [
            str(row[0])
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT DISTINCT namespace
                        FROM inventory_resources
                        WHERE cluster_id = :cluster AND namespace IS NOT NULL
                          AND kind = ANY(:kinds) AND lifecycle = 'active'
                        ORDER BY namespace
                        LIMIT :limit
                        """
                    ),
                    {
                        "cluster": cluster_id,
                        "kinds": sorted(WORKLOAD_KINDS),
                        "limit": MAX_PAGE_SIZE,
                    },
                )
            ).all()
        ]

        if namespace is not None:
            workloads = [
                {
                    "kind": row[0],
                    "name": row[1],
                    "last_seen_at": row[2].isoformat() if row[2] else None,
                }
                for row in (
                    await connection.execute(
                        text(
                            """
                            SELECT kind, name, last_seen_at
                            FROM inventory_resources
                            WHERE cluster_id = :cluster AND namespace = :namespace
                              AND kind = ANY(:kinds) AND lifecycle = 'active'
                            ORDER BY kind, name
                            LIMIT :limit
                            """
                        ),
                        {
                            "cluster": cluster_id,
                            "namespace": namespace,
                            "kinds": sorted(WORKLOAD_KINDS),
                            "limit": MAX_PAGE_SIZE,
                        },
                    )
                ).all()
            ]

    return {
        "clusters": [
            {"id": str(row[0]), "cluster_ref": row[1], "display_name": row[2]} for row in clusters
        ],
        "namespaces": namespaces,
        "workloads": workloads,
        "workload_kinds": sorted(WORKLOAD_KINDS),
    }


async def datasource_state(
    connection: AsyncConnection, environment_service_id: uuid.UUID
) -> dict[str, Any]:
    """Whether a telemetry datasource exists for this service's project.

    Reports state only. There is no URL, no credential and no config ref in
    this payload, and the binding form offers no way to enter one: a
    datasource is configured by an operator with integration access, not
    typed into a health screen.
    """
    row = (
        await connection.execute(
            text(
                """
                SELECT i.integration_type, i.configuration_state, i.observed_state,
                       i.last_success_at
                FROM environment_services es
                JOIN environments e ON e.id = es.environment_id
                JOIN projects p ON p.id = e.project_id
                LEFT JOIN integrations i
                       ON i.scope_id = p.scope_id
                      AND i.integration_type = 'prometheus'
                      AND i.lifecycle = 'active'
                WHERE es.id = :id
                """
            ),
            {"id": environment_service_id},
        )
    ).first()
    if row is None or row[0] is None:
        return {
            "configured": False,
            "integration_type": "prometheus",
            "configuration_state": "not_configured",
            "observed_state": "unknown",
            "last_success_at": None,
        }
    return {
        "configured": row[1] == "configured",
        "integration_type": row[0],
        "configuration_state": row[1],
        "observed_state": row[2],
        "last_success_at": row[3].isoformat() if row[3] else None,
    }


async def refresh_resolution(
    connection: AsyncConnection, binding_id: uuid.UUID
) -> dict[str, Any] | None:
    """Re-check inventory for a binding's workload.

    A workload that disappears from inventory clears the resolution marker
    but keeps the binding: the operator's intent survives an agent outage.
    """
    row = (
        await connection.execute(
            text(
                "SELECT cluster_id, namespace, workload_kind, workload_name "
                "FROM service_workload_bindings WHERE id = :id"
            ),
            {"id": binding_id},
        )
    ).first()
    if row is None:
        return None

    resolved = await resolve_workload(connection, row[0], row[1], row[2], row[3])
    await connection.execute(
        text(
            """
            UPDATE service_workload_bindings
            SET resolved_resource_uid = :uid,
                resolved_at = :resolved_at,
                updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": binding_id,
            "uid": resolved["uid"] if resolved else None,
            "resolved_at": datetime.now(UTC) if resolved else None,
        },
    )
    return {
        "resolved": resolved is not None,
        "resource_uid": resolved["uid"] if resolved else None,
        "inventory_health": resolved["inventory_health"] if resolved else None,
        "observed_at": resolved["observed_at"].isoformat() if resolved else None,
    }
