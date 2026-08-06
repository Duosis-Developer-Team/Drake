"""Catalog read authorization helpers.

Visibility = the subtree union of every grant the principal holds for the
given permission. All filtering happens in SQL BEFORE search, counts, and
pagination — unauthorized rows can never leak through totals or cursors
(ADR-0014, Decision 3).
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.rbac.service import Principal, RbacService

# Which read permission guards an integration attached at a given scope type.
INTEGRATION_READ_PERMISSION: dict[str, str] = {
    "organization": "project.view",
    "project": "project.view",
    "environment": "environment.view",
    "service": "environment.view",
    "cluster": "cluster.view",
}


async def visible_scope_ids(
    connection: AsyncConnection, principal: Principal, permission: str
) -> set[uuid.UUID]:
    """All scope ids covered by the principal's grants for *permission*."""
    service = RbacService(connection)
    roots = [
        grant.scope_id
        for grant in await service.effective_grants(principal)
        if grant.permission == permission
    ]
    if not roots:
        return set()
    rows = (
        await connection.execute(
            text(
                """
                WITH RECURSIVE visible AS (
                    SELECT id FROM scopes WHERE id = ANY(:roots)
                    UNION
                    SELECT s.id FROM scopes s JOIN visible v ON s.parent_id = v.id
                )
                SELECT id FROM visible
                """
            ),
            {"roots": roots},
        )
    ).all()
    return {row[0] for row in rows}


def escape_like(term: str) -> str:
    """Escape LIKE wildcards so user input can never widen a match."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
