"""Grant-creation options: the safe read surface behind the grant form.

Returns ONLY what the caller can actually manage:
- scopes: the subtree(s) under the caller's ``rbac.manage`` grants;
- roles: active roles, with per-scope delegability precomputed from the
  caller's own effective permissions (superset roles are not offered);
- principals: opaque IDs + display names only — no issuer/subject, no email.
  Organization-root managers see the full identity directory (excluding
  themselves); narrower managers see only principals that already hold
  grants inside their visible subtree (new-identity onboarding beyond that
  arrives with the directory/catalog integration and is honestly labeled).

This endpoint shapes the UI; the create endpoint re-validates everything
server-side regardless of what the client sends.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.db import get_engine
from drake_api.rbac.service import RbacService
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["rbac"])


@router.get("/grant-options")
async def grant_options(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)

    async with engine.connect() as connection:
        service = RbacService(connection)
        effective = await service.effective_grants(auth.principal)
        manage_roots = [g.scope_id for g in effective if g.permission == "rbac.manage"]
        if not manage_roots:
            raise HTTPException(status_code=403, detail="not permitted")

        org_root = await service.scopes.organization_root()
        is_org_manager = any(root == org_root.id for root in manage_roots)

        # Visible scope subtree(s) with parent links for permission propagation.
        scope_rows = (
            await connection.execute(
                text(
                    """
                    WITH RECURSIVE visible AS (
                        SELECT id, scope_type, external_ref, display_name, parent_id
                        FROM scopes WHERE id = ANY(:roots)
                        UNION
                        SELECT s.id, s.scope_type, s.external_ref, s.display_name, s.parent_id
                        FROM scopes s JOIN visible v ON s.parent_id = v.id
                    )
                    SELECT id, scope_type, external_ref, display_name, parent_id FROM visible
                    """
                ),
                {"roots": manage_roots},
            )
        ).all()
        visible_ids = {row[0] for row in scope_rows}

        # Actor's effective permissions per visible scope: seed the roots,
        # then propagate parent → child, adding direct grants at each scope.
        direct: dict[uuid.UUID, set[str]] = {}
        for grant in effective:
            direct.setdefault(grant.scope_id, set()).add(grant.permission)

        perms_by_scope: dict[uuid.UUID, set[str]] = {}
        for root in manage_roots:
            perms_by_scope[root] = await service.actor_permissions_at(auth.principal, root)
        children_of: dict[uuid.UUID | None, list[Any]] = {}
        for row in scope_rows:
            children_of.setdefault(row[4], []).append(row)
        frontier = [row for row in scope_rows if row[0] in perms_by_scope]
        while frontier:
            next_frontier = []
            for row in frontier:
                parent_perms = perms_by_scope[row[0]]
                for child in children_of.get(row[0], []):
                    if child[0] in perms_by_scope:
                        continue
                    perms_by_scope[child[0]] = parent_perms | direct.get(child[0], set())
                    next_frontier.append(child)
            frontier = next_frontier

        role_rows = (
            await connection.execute(
                text(
                    """
                    SELECT r.id, r.name,
                           coalesce(array_agg(rp.permission_key)
                                    FILTER (WHERE rp.permission_key IS NOT NULL), '{}')
                    FROM roles r
                    LEFT JOIN role_permissions rp ON rp.role_id = r.id
                    WHERE r.status = 'active'
                    GROUP BY r.id
                    ORDER BY r.name
                    """
                )
            )
        ).all()

        scopes_payload = []
        for row in scope_rows:
            scope_perms = perms_by_scope.get(row[0], set())
            delegable = [
                str(role_row[0])
                for role_row in role_rows
                if set(role_row[2]) and set(role_row[2]) <= scope_perms
            ]
            scopes_payload.append(
                {
                    "id": str(row[0]),
                    "scope_type": row[1],
                    "scope_ref": row[2],
                    "display_name": row[3],
                    "delegable_role_ids": delegable,
                }
            )

        if is_org_manager:
            identity_rows = (
                await connection.execute(
                    text(
                        """
                        SELECT id, display_name FROM identities
                        WHERE status = 'active' AND id != :actor
                        ORDER BY display_name
                        LIMIT 500
                        """
                    ),
                    {"actor": auth.principal.identity_id},
                )
            ).all()
            mapping_rows = (
                await connection.execute(
                    text("SELECT id, display_name FROM group_mappings ORDER BY display_name")
                )
            ).all()
        else:
            # Narrow managers: only principals ALREADY present in their
            # visible subtree — never the global directory.
            identity_rows = (
                await connection.execute(
                    text(
                        """
                        SELECT DISTINCT i.id, i.display_name
                        FROM grants g JOIN identities i ON i.id = g.identity_id
                        WHERE g.scope_id = ANY(:visible)
                          AND i.status = 'active' AND i.id != :actor
                        ORDER BY i.display_name
                        """
                    ),
                    {"visible": list(visible_ids), "actor": auth.principal.identity_id},
                )
            ).all()
            mapping_rows = (
                await connection.execute(
                    text(
                        """
                        SELECT DISTINCT gm.id, gm.display_name
                        FROM grants g JOIN group_mappings gm ON gm.id = g.group_mapping_id
                        WHERE g.scope_id = ANY(:visible)
                        ORDER BY gm.display_name
                        """
                    ),
                    {"visible": list(visible_ids)},
                )
            ).all()

    return {
        "directory_scope": "organization" if is_org_manager else "subtree",
        "scopes": scopes_payload,
        "roles": [
            {"id": str(row[0]), "name": row[1], "permissions": sorted(row[2])} for row in role_rows
        ],
        "identities": [{"id": str(row[0]), "display_name": row[1]} for row in identity_rows],
        "group_mappings": [{"id": str(row[0]), "display_name": row[1]} for row in mapping_rows],
    }
