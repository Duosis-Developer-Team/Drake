"""ScopeResolver: the boundary future catalog entities plug into.

RBAC never duplicates domain tables; it resolves scope nodes and their
ancestry. Inheritance is strictly parent → child: a grant at a scope covers
that scope and its descendants, never parents or siblings.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.rbac.catalog import ORGANIZATION_ROOT_REF

SCOPE_TYPES = ("organization", "cluster", "project", "environment", "service", "tenant")


@dataclass(frozen=True, slots=True)
class ScopeNode:
    id: uuid.UUID
    scope_type: str
    external_ref: str
    display_name: str
    parent_id: uuid.UUID | None


class ScopeResolver:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def get(self, scope_id: uuid.UUID) -> ScopeNode | None:
        row = (
            await self._connection.execute(
                text(
                    "SELECT id, scope_type, external_ref, display_name, parent_id "
                    "FROM scopes WHERE id = :id"
                ),
                {"id": scope_id},
            )
        ).first()
        if row is None:
            return None
        return ScopeNode(*row)

    async def organization_root(self) -> ScopeNode:
        row = (
            await self._connection.execute(
                text(
                    "SELECT id, scope_type, external_ref, display_name, parent_id "
                    "FROM scopes WHERE scope_type = 'organization' AND external_ref = :ref"
                ),
                {"ref": ORGANIZATION_ROOT_REF},
            )
        ).first()
        if row is None:
            raise RuntimeError("organization root scope is not seeded")
        return ScopeNode(*row)

    async def ensure(
        self,
        scope_type: str,
        external_ref: str,
        display_name: str = "",
        parent_id: uuid.UUID | None = None,
    ) -> ScopeNode:
        """Idempotently create/fetch a scope node (used by tests and, later,
        by catalog synchronization)."""
        if scope_type not in SCOPE_TYPES:
            raise ValueError("invalid scope type")
        if parent_id is None and scope_type != "organization":
            parent_id = (await self.organization_root()).id
        await self._connection.execute(
            text(
                """
                INSERT INTO scopes (scope_type, external_ref, display_name, parent_id)
                VALUES (:scope_type, :external_ref, :display_name, :parent_id)
                ON CONFLICT (scope_type, external_ref) DO NOTHING
                """
            ),
            {
                "scope_type": scope_type,
                "external_ref": external_ref,
                "display_name": display_name,
                "parent_id": parent_id,
            },
        )
        row = (
            await self._connection.execute(
                text(
                    "SELECT id, scope_type, external_ref, display_name, parent_id "
                    "FROM scopes WHERE scope_type = :scope_type AND external_ref = :external_ref"
                ),
                {"scope_type": scope_type, "external_ref": external_ref},
            )
        ).first()
        assert row is not None
        return ScopeNode(*row)

    async def ancestors_of(self, scope_id: uuid.UUID) -> list[uuid.UUID]:
        """The scope itself plus every ancestor up to the organization root."""
        rows = (
            await self._connection.execute(
                text(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT id, parent_id, 0 AS depth FROM scopes WHERE id = :id
                        UNION ALL
                        SELECT s.id, s.parent_id, chain.depth + 1
                        FROM scopes s JOIN chain ON s.id = chain.parent_id
                        WHERE chain.depth < 32
                    )
                    SELECT id FROM chain
                    """
                ),
                {"id": scope_id},
            )
        ).all()
        return [row[0] for row in rows]

    async def is_within(self, scope_id: uuid.UUID, ancestor_id: uuid.UUID) -> bool:
        """True when ``scope_id`` equals or descends from ``ancestor_id``."""
        return ancestor_id in await self.ancestors_of(scope_id)
