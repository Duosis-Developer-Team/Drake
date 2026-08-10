"""Registering a cluster — the one catalog write an operator can make.

Everything else in the catalog arrives through onboarding: a manifest in a
repository, a plan, an approval, an apply. A cluster cannot, because it is
what a manifest *refers to*. `clusterRef: duosis-prod-1` names something
that has to exist before any project can be bound into it, and the agent
that reports what is actually running there needs a cluster row before it
can be given an enrolment token.

Until now the only code that created one lived in `catalog.bootstrap` — a
local/test fixture loader that fails closed outside local and test, and is
exposed through no API at all. So a production Drake could not be told about
a cluster by any supported means.

This is deliberately narrow. It creates; it does not update, rename or
delete. A cluster's identity is what workload bindings, inventory and agent
certificates are all anchored to, and changing it later is a migration, not
an edit.
"""

import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from drake_api.audit.service import AuditEventData, record_audit_event_in
from drake_api.auth.dependencies import AuthContext, require_csrf
from drake_api.catalog.authz import visible_scope_ids
from drake_api.catalog.service import CatalogService
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["catalog-clusters"])

#: A cluster ref is a stable identifier that ends up in manifests, in
#: certificate subjects and in URLs. DNS-label shaped, lower case, bounded.
_CLUSTER_REF = re.compile(r"[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?")

#: Uniform for "no such cluster" and "not yours". Which one it is would tell
#: an unauthorized caller whether a name is taken.
_NOT_FOUND = "not found"


class ClusterCreate(BaseModel):
    model_config = {"extra": "forbid"}

    cluster_ref: str = Field(min_length=2, max_length=63)
    display_name: str = Field(min_length=1, max_length=200)
    site: str = Field(default="", max_length=200)


@router.post("/clusters", status_code=201)
async def create_cluster(
    request: Request, payload: ClusterCreate, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """Register a cluster. Idempotent on `cluster_ref`.

    Repeating the call returns the cluster that already exists rather than
    failing, because the caller's intent — "this cluster should be known to
    Drake" — is already satisfied. A repeat with a DIFFERENT display name is
    a conflict: it is a rename wearing a create's clothes, and renaming is
    not what this endpoint does.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)

    if not _CLUSTER_REF.fullmatch(payload.cluster_ref):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_cluster_ref",
                "message": "A cluster ref is a lower-case DNS label.",
            },
        )

    async with engine.begin() as connection:
        # `integration.manage` is what already governs enrolment tokens for
        # a cluster, and registering one is the same authority applied a
        # step earlier. A caller who holds it nowhere holds it here either.
        manage_scopes = await visible_scope_ids(connection, auth.principal, "integration.manage")
        if not manage_scopes:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

        existing = (
            await connection.execute(
                text(
                    "SELECT id, display_name, scope_id, lifecycle "
                    "FROM clusters WHERE cluster_ref = :ref"
                ),
                {"ref": payload.cluster_ref},
            )
        ).first()
        if existing is not None:
            if existing[2] not in manage_scopes:
                # Somebody else's cluster. Same answer as one that does not
                # exist — otherwise this endpoint enumerates cluster names.
                raise HTTPException(status_code=404, detail=_NOT_FOUND)
            if str(existing[1]) != payload.display_name:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "cluster_ref_taken",
                        "message": "That cluster ref already names a different cluster.",
                    },
                )
            return {
                "id": str(existing[0]),
                "cluster_ref": payload.cluster_ref,
                "display_name": str(existing[1]),
                "lifecycle": str(existing[3]),
                "created": False,
            }

        service = CatalogService(connection, source_kind="operator")
        created = await service.create_cluster(
            payload.cluster_ref, payload.display_name, site=payload.site
        )
        # Inside the same transaction as the write. A cluster that appeared
        # with no record of who asked for it is worse than one that never
        # appeared, because nobody can find out afterwards.
        await record_audit_event_in(
            connection,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action="catalog.cluster.create",
                result="success",
                target_type="cluster",
                target_id=str(created.id),
                correlation_id=correlation_id_var.get(),
                metadata={"cluster_ref": payload.cluster_ref, "site": payload.site},
            ),
        )

    return {
        "id": str(created.id),
        "cluster_ref": payload.cluster_ref,
        "display_name": payload.display_name,
        "lifecycle": "active",
        "created": True,
    }


async def register_cluster(
    engine: Any, *, cluster_ref: str, display_name: str, site: str, actor_identity_id: uuid.UUID
) -> dict[str, Any]:
    """The same registration, for an operator who has no browser session.

    A production rollout is done by somebody holding cluster credentials,
    not necessarily a Drake login — and the first cluster has to exist
    before anyone can be shown a UI that lists clusters. So this exists,
    and it is deliberately the SAME service call with the SAME validation,
    the SAME idempotency and the SAME audit event, differing only in how
    the actor is established.

    What it is not: a database shell. It registers a cluster. It cannot
    update, delete, or run a statement somebody passes in.
    """
    if not _CLUSTER_REF.fullmatch(cluster_ref):
        raise ValueError("a cluster ref is a lower-case DNS label")
    async with engine.begin() as connection:
        existing = (
            await connection.execute(
                text("SELECT id, display_name FROM clusters WHERE cluster_ref = :ref"),
                {"ref": cluster_ref},
            )
        ).first()
        if existing is not None:
            if str(existing[1]) != display_name:
                raise ValueError("that cluster ref already names a different cluster")
            return {"id": str(existing[0]), "cluster_ref": cluster_ref, "created": False}
        service = CatalogService(connection, source_kind="operator")
        created = await service.create_cluster(cluster_ref, display_name, site=site)
        await record_audit_event_in(
            connection,
            AuditEventData(
                actor_type="user",
                actor_id=str(actor_identity_id),
                action="catalog.cluster.create",
                result="success",
                target_type="cluster",
                target_id=str(created.id),
                correlation_id=correlation_id_var.get(),
                metadata={"cluster_ref": cluster_ref, "site": site, "channel": "management"},
            ),
        )
    return {"id": str(created.id), "cluster_ref": cluster_ref, "created": True}
