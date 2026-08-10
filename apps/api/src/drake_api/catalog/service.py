"""Catalog application service.

The ONLY write path into catalog tables. Each create inserts the entity and
its RBAC scope node in the caller's transaction (atomic by construction),
validates bounded JSON metadata, and rejects credential-shaped content.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.logging import redact
from drake_api.rbac.scope import ScopeResolver

_MAX_BOUNDED_JSON_BYTES = 4_096
_SAFE_HEALTH_KEYS = {"livePath", "readyPath", "metricsPath"}


class CatalogValidationError(ValueError):
    """Invalid catalog input; message never echoes offending values."""


def _bounded_safe_json(value: dict[str, Any], what: str) -> dict[str, Any]:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(serialized.encode()) > _MAX_BOUNDED_JSON_BYTES:
        raise CatalogValidationError(f"{what} exceeds the bounded size")
    if redact(serialized) != serialized:
        raise CatalogValidationError(f"{what} contains credential-shaped content")
    return value


def _validate_health(health: dict[str, Any]) -> dict[str, Any]:
    for key, path in health.items():
        if key not in _SAFE_HEALTH_KEYS:
            raise CatalogValidationError("health metadata contains an unknown field")
        if not isinstance(path, str) or not path.startswith("/") or "://" in path:
            raise CatalogValidationError("health entries must be plain path metadata")
    return _bounded_safe_json(health, "health metadata")


@dataclass(frozen=True, slots=True)
class CreatedEntity:
    id: uuid.UUID
    scope_id: uuid.UUID


class CatalogService:
    def __init__(self, connection: AsyncConnection, source_kind: str = "manifest") -> None:
        self._connection = connection
        self._scopes = ScopeResolver(connection)
        self._source_kind = source_kind

    async def _insert(self, statement: str, params: dict[str, Any]) -> uuid.UUID:
        row = (await self._connection.execute(text(statement), params)).first()
        assert row is not None
        return uuid.UUID(str(row[0]))

    # ---------------------------------------------------------------- cluster
    async def create_cluster(
        self,
        cluster_ref: str,
        display_name: str,
        site: str = "",
        source_ref: str = "",
        source_revision: str = "",
    ) -> CreatedEntity:
        scope = await self._scopes.ensure("cluster", cluster_ref, display_name)
        cluster_id = await self._insert(
            """
            INSERT INTO clusters
                (cluster_ref, display_name, site, catalog_source_kind,
                 catalog_source_ref, source_revision, scope_id)
            VALUES (:ref, :name, :site, :kind, :source_ref, :revision, :scope_id)
            RETURNING id
            """,
            {
                "ref": cluster_ref,
                "name": display_name,
                "site": site,
                "kind": self._source_kind,
                "source_ref": source_ref,
                "revision": source_revision,
                "scope_id": scope.id,
            },
        )
        return CreatedEntity(cluster_id, scope.id)

    # ---------------------------------------------------------------- project
    async def create_project(
        self,
        project_key: str,
        display_name: str,
        *,
        repo_provider: str,
        repo_owner: str,
        repo_name: str,
        default_branch: str = "",
        criticality: str = "medium",
        tenant_model: str = "none",
        owners: list[tuple[str, str]] | None = None,
        source_ref: str = "",
        source_revision: str = "",
    ) -> CreatedEntity:
        scope = await self._scopes.ensure("project", project_key, display_name)
        project_id = await self._insert(
            """
            INSERT INTO projects
                (project_key, display_name, repo_provider, repo_owner, repo_name,
                 default_branch, criticality, tenant_model, catalog_source_kind,
                 catalog_source_ref, source_revision, scope_id)
            VALUES (:key, :name, :provider, :owner, :repo, :branch, :criticality,
                    :tenant_model, :kind, :source_ref, :revision, :scope_id)
            RETURNING id
            """,
            {
                "key": project_key,
                "name": display_name,
                "provider": repo_provider,
                "owner": repo_owner,
                "repo": repo_name,
                "branch": default_branch,
                "criticality": criticality,
                "tenant_model": tenant_model,
                "kind": self._source_kind,
                "source_ref": source_ref,
                "revision": source_revision,
                "scope_id": scope.id,
            },
        )
        for team_key, owner_role in owners or []:
            await self._connection.execute(
                text(
                    """
                    INSERT INTO project_owners (project_id, team_key, owner_role)
                    VALUES (:project_id, :team, :role)
                    ON CONFLICT (project_id, team_key, owner_role) DO NOTHING
                    """
                ),
                {"project_id": project_id, "team": team_key, "role": owner_role},
            )
        return CreatedEntity(project_id, scope.id)

    # ------------------------------------------------------------ environment
    async def _project_identity(self, project_id: uuid.UUID) -> tuple[str, uuid.UUID]:
        """Authoritative (project_key, scope_id) from the database."""
        row = (
            await self._connection.execute(
                text("SELECT project_key, scope_id FROM projects WHERE id = :id"),
                {"id": project_id},
            )
        ).first()
        if row is None:
            raise CatalogValidationError("unknown project")
        return str(row[0]), row[1]

    async def create_environment(
        self,
        project_id: uuid.UUID,
        environment_key: str,
        *,
        runtime: str,
        branch: str = "",
        criticality: str = "medium",
        cluster_id: uuid.UUID | None = None,
        namespace: str | None = None,
        hosting_provider: str | None = None,
        source_ref: str = "",
        source_revision: str = "",
    ) -> CreatedEntity:
        # Scope refs derive from AUTHORITATIVE records only — callers cannot
        # supply a parallel key identity for the same relationship.
        project_key, project_scope_id = await self._project_identity(project_id)
        scope = await self._scopes.ensure(
            "environment",
            f"{project_key}/{environment_key}",
            environment_key,
            parent_id=project_scope_id,
        )
        environment_id = await self._insert(
            """
            INSERT INTO environments
                (project_id, environment_key, runtime, branch, criticality,
                 cluster_id, namespace, hosting_provider, catalog_source_kind,
                 catalog_source_ref, source_revision, scope_id)
            VALUES (:project_id, :key, :runtime, :branch, :criticality,
                    :cluster_id, :namespace, :hosting_provider, :kind, :source_ref,
                    :revision, :scope_id)
            RETURNING id
            """,
            {
                "project_id": project_id,
                "key": environment_key,
                "runtime": runtime,
                "branch": branch,
                "criticality": criticality,
                "cluster_id": cluster_id,
                "namespace": namespace,
                # A database check constraint refuses a provider on a
                # Kubernetes environment; this keeps the write honest rather
                # than relying on the caller to remember.
                "hosting_provider": hosting_provider or None,
                "kind": self._source_kind,
                "source_ref": source_ref,
                "revision": source_revision,
                "scope_id": scope.id,
            },
        )
        return CreatedEntity(environment_id, scope.id)

    # ------------------------------------------------------------- dependency

    async def create_dependency(
        self,
        project_id: uuid.UUID,
        dependency_key: str,
        *,
        dependency_class: str,
        engine: str,
        store_scope: str,
        display_name: str = "",
        provider: str | None = None,
        verification: str = "repository_intent",
        source_ref: str = "",
        source_revision: str = "",
    ) -> uuid.UUID:
        """A dependency the project has and Drake does not necessarily run.

        No scope row of its own: a dependency is visible exactly when its
        PROJECT is, which is the boundary the manifest already draws. Giving
        it a separate scope would invent an authority nobody granted.
        """
        return await self._insert(
            """
            INSERT INTO project_dependencies
                (project_id, dependency_key, display_name, dependency_class, engine,
                 store_scope, provider, verification, catalog_source_kind,
                 catalog_source_ref, source_revision)
            VALUES (:project_id, :key, :display_name, :dependency_class, :engine,
                    :store_scope, :provider, :verification, :kind, :source_ref, :revision)
            RETURNING id
            """,
            {
                "project_id": project_id,
                "key": dependency_key,
                "display_name": display_name or dependency_key,
                "dependency_class": dependency_class,
                "engine": engine,
                "store_scope": store_scope,
                # A check constraint refuses a provider on an in-cluster row.
                "provider": provider or None,
                "verification": verification,
                "kind": self._source_kind,
                "source_ref": source_ref,
                "revision": source_revision,
            },
        )

    async def update_dependency(
        self,
        dependency_id: uuid.UUID,
        *,
        store_scope: str,
        display_name: str = "",
        provider: str | None = None,
        verification: str = "repository_intent",
        source_ref: str = "",
        source_revision: str = "",
    ) -> None:
        """Only the mutable fields. Class and engine are what the dependency
        IS; changing either is a different dependency wearing the same name,
        which the planner reports as a conflict rather than applying."""
        await self._connection.execute(
            text(
                """
                UPDATE project_dependencies
                   SET display_name = :display_name,
                       store_scope = :store_scope,
                       provider = :provider,
                       verification = :verification,
                       catalog_source_ref = :source_ref,
                       source_revision = :revision,
                       version = version + 1,
                       updated_at = now()
                 WHERE id = :id
                """
            ),
            {
                "id": dependency_id,
                "display_name": display_name,
                "store_scope": store_scope,
                "provider": provider or None,
                "verification": verification,
                "source_ref": source_ref,
                "revision": source_revision,
            },
        )

    # ---------------------------------------------------------------- service
    async def create_service_definition(
        self,
        project_id: uuid.UUID,
        service_key: str,
        *,
        component: str,
        runtime: str,
        # None means no metrics source. It is stored as NULL and reported as
        # `not_configured`, never filled in to satisfy a NOT NULL column.
        metrics_profile: str | None = None,
        display_name: str = "",
        workload_selector: dict[str, Any] | None = None,
        health: dict[str, Any] | None = None,
        source_ref: str = "",
        source_revision: str = "",
    ) -> uuid.UUID:
        selector = _bounded_safe_json(workload_selector or {}, "workload selector")
        health_meta = _validate_health(health or {})
        return await self._insert(
            """
            INSERT INTO service_definitions
                (project_id, service_key, display_name, component, runtime,
                 metrics_profile, workload_selector, health, catalog_source_kind,
                 catalog_source_ref, source_revision)
            VALUES (:project_id, :key, :name, :component, :runtime, :profile,
                    CAST(:selector AS jsonb), CAST(:health AS jsonb), :kind,
                    :source_ref, :revision)
            RETURNING id
            """,
            {
                "project_id": project_id,
                "key": service_key,
                "name": display_name or service_key,
                "component": component,
                "runtime": runtime,
                "profile": metrics_profile or None,
                "selector": json.dumps(selector),
                "health": json.dumps(health_meta),
                "kind": self._source_kind,
                "source_ref": source_ref,
                "revision": source_revision,
            },
        )

    async def bind_service(self, environment_id: uuid.UUID, service_id: uuid.UUID) -> CreatedEntity:
        """Bind a service definition to an environment OF THE SAME PROJECT.

        All identity (project/environment/service keys, scope parents) is
        derived from authoritative rows; the same-project invariant is
        enforced here AND by composite foreign keys in PostgreSQL (0005).
        """
        environment = (
            await self._connection.execute(
                text(
                    """
                    SELECT e.project_id, e.environment_key, e.scope_id, p.project_key
                    FROM environments e JOIN projects p ON p.id = e.project_id
                    WHERE e.id = :id
                    """
                ),
                {"id": environment_id},
            )
        ).first()
        if environment is None:
            raise CatalogValidationError("unknown environment")
        service = (
            await self._connection.execute(
                text("SELECT project_id, service_key FROM service_definitions WHERE id = :id"),
                {"id": service_id},
            )
        ).first()
        if service is None:
            raise CatalogValidationError("unknown service definition")
        if environment[0] != service[0]:
            raise CatalogValidationError(
                "service definition belongs to a different project than the environment"
            )

        project_key = str(environment[3])
        environment_key = str(environment[1])
        service_key = str(service[1])
        scope = await self._scopes.ensure(
            "service",
            f"{project_key}/{environment_key}/{service_key}",
            service_key,
            parent_id=environment[2],
        )
        binding_id = await self._insert(
            """
            INSERT INTO environment_services
                (environment_id, service_id, project_id, scope_id)
            VALUES (:environment_id, :service_id, :project_id, :scope_id)
            RETURNING id
            """,
            {
                "environment_id": environment_id,
                "service_id": service_id,
                "project_id": environment[0],
                "scope_id": scope.id,
            },
        )
        return CreatedEntity(binding_id, scope.id)

    @staticmethod
    def validate_error_code(code: str | None) -> str | None:
        """Bounded machine-readable integration error code (also DB-enforced)."""
        if code is None:
            return None
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", code):
            raise CatalogValidationError("last_error_code must be a bounded machine-readable code")
        return code

    # ------------------------------------------------------------ integration
    async def register_integration(
        self, integration_type: str, scope_id: uuid.UUID, config_ref: str = ""
    ) -> uuid.UUID:
        if redact(config_ref) != config_ref:
            raise CatalogValidationError("config_ref must be a reference name, not a credential")
        return await self._insert(
            """
            INSERT INTO integrations (integration_type, scope_id, config_ref,
                                      configuration_state)
            VALUES (:type, :scope_id, :config_ref,
                    CASE WHEN :config_ref = '' THEN 'not_configured' ELSE 'configured' END)
            ON CONFLICT (integration_type, scope_id) DO UPDATE SET updated_at = now()
            RETURNING id
            """,
            {"type": integration_type, "scope_id": scope_id, "config_ref": config_ref},
        )
