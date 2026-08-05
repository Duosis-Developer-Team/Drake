"""RBAC service layer: the only place authorization decisions are made.

Every mutation writes its audit row on the SAME database connection and
transaction as the change itself; if the audit insert fails, the mutation
rolls back (fail-closed). Denials raise typed exceptions that routers map to
the standardized 401/403/404 semantics.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Table, insert, text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.audit.models import AuditEvent
from drake_api.audit.service import AuditEventData, validate_event
from drake_api.rbac.scope import ScopeResolver


class AuthorizationDeniedError(Exception):
    """Authenticated but lacking the capability → 403."""

    def __init__(self, audit_action: str = "authz.denied") -> None:
        super().__init__(audit_action)
        self.audit_action = audit_action


class ScopedResourceHiddenError(Exception):
    """Outside the caller's scope → consistent 404 (anti-enumeration)."""


class PreconditionFailedError(Exception):
    """Stale If-Match / concurrent edit → 412."""


class InvariantViolationError(Exception):
    """Refused state transition (e.g. last-owner protection) → 409."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Principal:
    identity_id: uuid.UUID
    issuer: str
    groups: tuple[str, ...] = ()
    groups_overage: bool = False


@dataclass(frozen=True, slots=True)
class EffectiveGrant:
    permission: str
    scope_id: uuid.UUID
    scope_type: str
    scope_ref: str
    via: str  # "identity" | "group"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RbacService:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self.scopes = ScopeResolver(connection)

    # ------------------------------------------------------------------ audit
    async def record_audit(self, event: AuditEventData) -> None:
        """Insert an audit row on THIS connection/transaction (fail-closed:
        an exception here rolls back the surrounding mutation)."""
        values = validate_event(event)
        table = cast(Table, AuditEvent.__table__)
        await self._connection.execute(insert(table).values(**values))

    # -------------------------------------------------------------- identity
    async def upsert_identity_on_login(
        self, issuer: str, subject: str, display_name: str, email: str
    ) -> uuid.UUID:
        row = (
            await self._connection.execute(
                text(
                    """
                    INSERT INTO identities (issuer, subject, display_name, email, last_login_at)
                    VALUES (:issuer, :subject, :display_name, :email, now())
                    ON CONFLICT (issuer, subject) DO UPDATE
                    SET display_name = EXCLUDED.display_name,
                        email = EXCLUDED.email,
                        last_login_at = now()
                    RETURNING id
                    """
                ),
                {
                    "issuer": issuer,
                    "subject": subject,
                    "display_name": display_name,
                    "email": email,
                },
            )
        ).first()
        assert row is not None
        return cast(uuid.UUID, row[0])

    # ------------------------------------------------------- effective authz
    async def effective_grants(self, principal: Principal) -> list[EffectiveGrant]:
        """All (permission, scope) pairs currently held by the principal.

        Group-derived authority requires an explicit group mapping AND is
        disabled entirely under group overage (fail closed). Expired, future,
        or revoked grants and archived roles never contribute.
        """
        groups = list(principal.groups) if not principal.groups_overage else []
        rows = (
            await self._connection.execute(
                text(
                    """
                    SELECT DISTINCT rp.permission_key, s.id, s.scope_type, s.external_ref,
                           CASE WHEN g.identity_id IS NOT NULL THEN 'identity' ELSE 'group' END
                    FROM grants g
                    JOIN roles r ON r.id = g.role_id AND r.status = 'active'
                    JOIN role_permissions rp ON rp.role_id = g.role_id
                    JOIN scopes s ON s.id = g.scope_id
                    LEFT JOIN group_mappings gm ON gm.id = g.group_mapping_id
                    WHERE g.revoked_at IS NULL
                      AND g.valid_from <= now()
                      AND (g.valid_to IS NULL OR g.valid_to > now())
                      AND (
                        g.identity_id = :identity_id
                        OR (
                          gm.issuer = :issuer
                          AND gm.group_object_id = ANY(:groups)
                        )
                      )
                    """
                ),
                {
                    "identity_id": principal.identity_id,
                    "issuer": principal.issuer,
                    "groups": groups,
                },
            )
        ).all()
        return [EffectiveGrant(*row) for row in rows]

    async def has_permission(
        self, principal: Principal, permission: str, scope_id: uuid.UUID
    ) -> bool:
        """Scope-aware check: a grant applies at its own scope and every
        descendant (parent → child inheritance only)."""
        ancestor_ids = await self.scopes.ancestors_of(scope_id)
        if not ancestor_ids:
            return False
        for grant in await self.effective_grants(principal):
            if grant.permission == permission and grant.scope_id in ancestor_ids:
                return True
        return False

    async def require_permission(
        self, principal: Principal, permission: str, scope_id: uuid.UUID
    ) -> None:
        if not await self.has_permission(principal, permission, scope_id):
            raise AuthorizationDeniedError()

    async def require_visible_scope(
        self, principal: Principal, permission: str, scope_id: uuid.UUID
    ) -> None:
        """Resource-level rule: outside-scope resources are a consistent 404."""
        scope = await self.scopes.get(scope_id)
        if scope is None:
            raise ScopedResourceHiddenError()
        if not await self.has_permission(principal, permission, scope_id):
            raise ScopedResourceHiddenError()

    # ------------------------------------------------------------------ roles
    async def _require_org_rbac_manage(self, actor: Principal) -> uuid.UUID:
        root = await self.scopes.organization_root()
        if not await self.has_permission(actor, "rbac.manage", root.id):
            raise AuthorizationDeniedError()
        return root.id

    async def _actor_permissions_at(self, actor: Principal, scope_id: uuid.UUID) -> set[str]:
        ancestor_ids = set(await self.scopes.ancestors_of(scope_id))
        return {
            grant.permission
            for grant in await self.effective_grants(actor)
            if grant.scope_id in ancestor_ids
        }

    async def create_role(
        self, actor: Principal, name: str, description: str, correlation_id: str
    ) -> dict[str, Any]:
        await self._require_org_rbac_manage(actor)
        row = (
            await self._connection.execute(
                text(
                    """
                    INSERT INTO roles (name, description)
                    VALUES (:name, :description)
                    ON CONFLICT (name) DO NOTHING
                    RETURNING id, version
                    """
                ),
                {"name": name, "description": description},
            )
        ).first()
        if row is None:
            raise InvariantViolationError("role_name_exists")
        await self.record_audit(
            AuditEventData(
                actor_type="user",
                actor_id=str(actor.identity_id),
                action="rbac.role.create",
                result="success",
                target_type="role",
                target_id=str(row[0]),
                correlation_id=correlation_id,
                metadata={"name": name},
            )
        )
        return {"id": row[0], "version": row[1]}

    async def _load_role(self, role_id: uuid.UUID) -> dict[str, Any]:
        row = (
            await self._connection.execute(
                text(
                    "SELECT id, name, description, is_system, status, version "
                    "FROM roles WHERE id = :id"
                ),
                {"id": role_id},
            )
        ).first()
        if row is None:
            raise ScopedResourceHiddenError()
        return {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "is_system": row[3],
            "status": row[4],
            "version": row[5],
        }

    async def _bump_role_version(self, role_id: uuid.UUID, expected_version: int) -> int:
        row = (
            await self._connection.execute(
                text(
                    """
                    UPDATE roles SET version = version + 1, updated_at = now()
                    WHERE id = :id AND version = :expected
                    RETURNING version
                    """
                ),
                {"id": role_id, "expected": expected_version},
            )
        ).first()
        if row is None:
            raise PreconditionFailedError()
        return int(row[0])

    async def update_role(
        self,
        actor: Principal,
        role_id: uuid.UUID,
        expected_version: int,
        description: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        await self._require_org_rbac_manage(actor)
        role = await self._load_role(role_id)
        if role["is_system"]:
            raise InvariantViolationError("system_role_immutable")
        if role["status"] != "active":
            raise InvariantViolationError("role_archived")
        version = await self._bump_role_version(role_id, expected_version)
        await self._connection.execute(
            text("UPDATE roles SET description = :description WHERE id = :id"),
            {"description": description, "id": role_id},
        )
        await self.record_audit(
            AuditEventData(
                actor_type="user",
                actor_id=str(actor.identity_id),
                action="rbac.role.update",
                result="success",
                target_type="role",
                target_id=str(role_id),
                correlation_id=correlation_id,
            )
        )
        return {"id": role_id, "version": version}

    async def archive_role(
        self,
        actor: Principal,
        role_id: uuid.UUID,
        expected_version: int,
        correlation_id: str,
    ) -> None:
        await self._require_org_rbac_manage(actor)
        role = await self._load_role(role_id)
        if role["is_system"]:
            raise InvariantViolationError("system_role_immutable")
        if role["status"] != "active":
            raise InvariantViolationError("role_archived")
        await self._bump_role_version(role_id, expected_version)
        await self._connection.execute(
            text("UPDATE roles SET status = 'archived' WHERE id = :id"), {"id": role_id}
        )
        await self.record_audit(
            AuditEventData(
                actor_type="user",
                actor_id=str(actor.identity_id),
                action="rbac.role.archive",
                result="success",
                target_type="role",
                target_id=str(role_id),
                correlation_id=correlation_id,
            )
        )

    async def set_role_permissions(
        self,
        actor: Principal,
        role_id: uuid.UUID,
        expected_version: int,
        permission_keys: list[str],
        correlation_id: str,
    ) -> dict[str, Any]:
        """Replace a role's permission set.

        Anti-escalation: the actor must currently HOLD (at organization
        scope) every permission being ADDED — editing a role you are granted
        can never give you authority you do not already have.
        """
        root_id = await self._require_org_rbac_manage(actor)
        role = await self._load_role(role_id)
        if role["is_system"]:
            raise InvariantViolationError("system_role_immutable")
        if role["status"] != "active":
            raise InvariantViolationError("role_archived")

        known = {
            row[0]
            for row in (await self._connection.execute(text("SELECT key FROM permissions"))).all()
        }
        unknown = [key for key in permission_keys if key not in known]
        if unknown:
            raise InvariantViolationError("unknown_permission")

        current = {
            row[0]
            for row in (
                await self._connection.execute(
                    text("SELECT permission_key FROM role_permissions WHERE role_id = :id"),
                    {"id": role_id},
                )
            ).all()
        }
        added = set(permission_keys) - current
        actor_permissions = await self._actor_permissions_at(actor, root_id)
        escalation = added - actor_permissions
        if escalation:
            await self.record_audit(
                AuditEventData(
                    actor_type="user",
                    actor_id=str(actor.identity_id),
                    action="rbac.self_escalation.denied",
                    result="denied",
                    target_type="role",
                    target_id=str(role_id),
                    correlation_id=correlation_id,
                )
            )
            raise AuthorizationDeniedError("rbac.self_escalation.denied")

        version = await self._bump_role_version(role_id, expected_version)
        await self._connection.execute(
            text("DELETE FROM role_permissions WHERE role_id = :id"), {"id": role_id}
        )
        for key in sorted(set(permission_keys)):
            await self._connection.execute(
                text("INSERT INTO role_permissions (role_id, permission_key) VALUES (:id, :key)"),
                {"id": role_id, "key": key},
            )
        await self.record_audit(
            AuditEventData(
                actor_type="user",
                actor_id=str(actor.identity_id),
                action="rbac.role.permissions.set",
                result="success",
                target_type="role",
                target_id=str(role_id),
                correlation_id=correlation_id,
                metadata={"count": len(set(permission_keys))},
            )
        )
        return {"id": role_id, "version": version}

    # ----------------------------------------------------------------- grants
    async def create_grant(
        self,
        actor: Principal,
        *,
        role_id: uuid.UUID,
        scope_id: uuid.UUID,
        identity_id: uuid.UUID | None = None,
        group_mapping_id: uuid.UUID | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        correlation_id: str,
    ) -> uuid.UUID:
        """Create a scoped grant, enforcing delegation rules:

        - actor needs ``rbac.manage`` covering the TARGET scope;
        - the granted role's permissions must be a subset of the actor's own
          effective permissions at that scope (no delegating what you lack);
        - self-grants are refused outright (self-escalation).
        """
        if (identity_id is None) == (group_mapping_id is None):
            raise InvariantViolationError("exactly_one_principal")

        scope = await self.scopes.get(scope_id)
        if scope is None:
            raise ScopedResourceHiddenError()
        if not await self.has_permission(actor, "rbac.manage", scope_id):
            raise ScopedResourceHiddenError()

        if identity_id == actor.identity_id:
            await self.record_audit(
                AuditEventData(
                    actor_type="user",
                    actor_id=str(actor.identity_id),
                    action="rbac.self_escalation.denied",
                    result="denied",
                    target_type="grant",
                    correlation_id=correlation_id,
                )
            )
            raise AuthorizationDeniedError("rbac.self_escalation.denied")

        role = await self._load_role(role_id)
        if role["status"] != "active":
            raise InvariantViolationError("role_archived")

        role_permissions = {
            row[0]
            for row in (
                await self._connection.execute(
                    text("SELECT permission_key FROM role_permissions WHERE role_id = :id"),
                    {"id": role_id},
                )
            ).all()
        }
        actor_permissions = await self._actor_permissions_at(actor, scope_id)
        if not role_permissions <= actor_permissions:
            await self.record_audit(
                AuditEventData(
                    actor_type="user",
                    actor_id=str(actor.identity_id),
                    action="rbac.delegation.denied",
                    result="denied",
                    target_type="grant",
                    correlation_id=correlation_id,
                )
            )
            raise AuthorizationDeniedError("rbac.delegation.denied")

        if identity_id is not None:
            exists = (
                await self._connection.execute(
                    text("SELECT 1 FROM identities WHERE id = :id"), {"id": identity_id}
                )
            ).first()
            if exists is None:
                raise ScopedResourceHiddenError()
        if group_mapping_id is not None:
            exists = (
                await self._connection.execute(
                    text("SELECT 1 FROM group_mappings WHERE id = :id"),
                    {"id": group_mapping_id},
                )
            ).first()
            if exists is None:
                raise ScopedResourceHiddenError()

        row = (
            await self._connection.execute(
                text(
                    """
                    INSERT INTO grants
                        (identity_id, group_mapping_id, role_id, scope_id,
                         valid_from, valid_to, created_by)
                    VALUES
                        (:identity_id, :group_mapping_id, :role_id, :scope_id,
                         COALESCE(:valid_from, now()), :valid_to, :created_by)
                    RETURNING id
                    """
                ),
                {
                    "identity_id": identity_id,
                    "group_mapping_id": group_mapping_id,
                    "role_id": role_id,
                    "scope_id": scope_id,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "created_by": actor.identity_id,
                },
            )
        ).first()
        assert row is not None
        await self.record_audit(
            AuditEventData(
                actor_type="user",
                actor_id=str(actor.identity_id),
                action="rbac.grant.create",
                result="success",
                scope_type=scope.scope_type,
                scope_id=scope.external_ref,
                target_type="grant",
                target_id=str(row[0]),
                correlation_id=correlation_id,
            )
        )
        return uuid.UUID(str(row[0]))

    async def revoke_grant(
        self, actor: Principal, grant_id: uuid.UUID, correlation_id: str
    ) -> None:
        grant = (
            await self._connection.execute(
                text(
                    "SELECT id, identity_id, role_id, scope_id, revoked_at "
                    "FROM grants WHERE id = :id"
                ),
                {"id": grant_id},
            )
        ).first()
        if grant is None:
            raise ScopedResourceHiddenError()
        scope_id = grant[3]
        if not await self.has_permission(actor, "rbac.manage", scope_id):
            # Consistent 404: the grant's existence is not revealed.
            raise ScopedResourceHiddenError()
        if grant[4] is not None:
            raise InvariantViolationError("grant_already_revoked")

        await self._assert_not_last_owner(grant_id)

        await self._connection.execute(
            text("UPDATE grants SET revoked_at = now(), revoked_by = :actor WHERE id = :id"),
            {"actor": actor.identity_id, "id": grant_id},
        )
        scope = await self.scopes.get(scope_id)
        await self.record_audit(
            AuditEventData(
                actor_type="user",
                actor_id=str(actor.identity_id),
                action="rbac.grant.revoke",
                result="success",
                scope_type=scope.scope_type if scope else None,
                scope_id=scope.external_ref if scope else None,
                target_type="grant",
                target_id=str(grant_id),
                correlation_id=correlation_id,
            )
        )

    async def _assert_not_last_owner(self, grant_id_being_revoked: uuid.UUID) -> None:
        """The platform must always retain at least one active identity grant
        at the organization root whose role carries ``rbac.manage``.

        Permission-based (not role-name-based) so renaming roles cannot
        bypass it.
        """
        root = await self.scopes.organization_root()
        row = (
            await self._connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM grants g
                    JOIN roles r ON r.id = g.role_id AND r.status = 'active'
                    JOIN role_permissions rp
                      ON rp.role_id = g.role_id AND rp.permission_key = 'rbac.manage'
                    WHERE g.scope_id = :root
                      AND g.identity_id IS NOT NULL
                      AND g.revoked_at IS NULL
                      AND g.valid_from <= now()
                      AND (g.valid_to IS NULL OR g.valid_to > now())
                      AND g.id != :grant_id
                    """
                ),
                {"root": root.id, "grant_id": grant_id_being_revoked},
            )
        ).first()
        remaining = int(row[0]) if row else 0
        if remaining == 0:
            raise InvariantViolationError("last_platform_owner_protected")

    # -------------------------------------------------------- group mappings
    async def create_group_mapping(
        self,
        actor: Principal,
        issuer: str,
        group_object_id: str,
        display_name: str,
        correlation_id: str,
    ) -> uuid.UUID:
        await self._require_org_rbac_manage(actor)
        row = (
            await self._connection.execute(
                text(
                    """
                    INSERT INTO group_mappings (issuer, group_object_id, display_name)
                    VALUES (:issuer, :group_object_id, :display_name)
                    ON CONFLICT (issuer, group_object_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "issuer": issuer,
                    "group_object_id": group_object_id,
                    "display_name": display_name,
                },
            )
        ).first()
        if row is None:
            raise InvariantViolationError("group_mapping_exists")
        await self.record_audit(
            AuditEventData(
                actor_type="user",
                actor_id=str(actor.identity_id),
                action="rbac.group_mapping.create",
                result="success",
                target_type="group_mapping",
                target_id=str(row[0]),
                correlation_id=correlation_id,
            )
        )
        return uuid.UUID(str(row[0]))


__all__ = [
    "AuthorizationDeniedError",
    "EffectiveGrant",
    "InvariantViolationError",
    "PreconditionFailedError",
    "Principal",
    "RbacService",
    "ScopedResourceHiddenError",
]
