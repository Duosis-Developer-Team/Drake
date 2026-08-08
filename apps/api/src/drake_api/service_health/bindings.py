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
