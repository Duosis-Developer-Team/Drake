"""Infrastructure endpoints (ADR-0032).

`cluster.view` decides which clusters a caller can open, uniformly 404
otherwise; the numbers then come through the telemetry broker, which checks
`telemetry.query` on the same cluster. Every endpoint here is a read.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from drake_api.auth.dependencies import AuthContext, require_auth
from drake_api.db import get_engine
from drake_api.infrastructure.service import InfrastructureService, visible_clusters
from drake_api.settings import Settings

router = APIRouter(prefix="/v1/infrastructure", tags=["infrastructure"])

_NAMESPACE = r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"


def _service(request: Request) -> InfrastructureService:
    service = getattr(request.app.state, "infrastructure", None)
    if not isinstance(service, InfrastructureService):
        raise HTTPException(status_code=503, detail="infrastructure unavailable")
    return service


@router.get("/clusters")
async def clusters(request: Request, auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    return {"clusters": await visible_clusters(get_engine(settings), auth.principal)}


@router.get("/clusters/{cluster_id}")
async def snapshot(
    request: Request, cluster_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    return await _service(request).snapshot(auth.principal, cluster_id)


@router.get("/clusters/{cluster_id}/namespaces/{namespace}")
async def namespace_pods(
    request: Request,
    cluster_id: uuid.UUID,
    namespace: str = Path(pattern=_NAMESPACE, max_length=63),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    return await _service(request).namespace_pods(auth.principal, cluster_id, namespace)
