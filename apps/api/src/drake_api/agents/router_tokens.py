"""Enrollment token management (user-facing, cluster-scoped)."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from drake_api.audit import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_csrf
from drake_api.catalog.authz import visible_scope_ids
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["agent-enrollment"])

TOKEN_TTL_SECONDS_DEFAULT = 600
TOKEN_TTL_SECONDS_MAX = 900


@router.post("/clusters/{cluster_id}/agent-enrollment-tokens", status_code=201)
async def create_enrollment_token(
    request: Request,
    cluster_id: uuid.UUID,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        # integration.manage resolved at the cluster scope; unknown and
        # out-of-scope clusters are ONE uniform 404.
        manage_scopes = await visible_scope_ids(connection, auth.principal, "integration.manage")
        row = (
            await connection.execute(
                text("SELECT scope_id FROM clusters WHERE id = :id AND lifecycle = 'active'"),
                {"id": cluster_id},
            )
        ).first()
        if row is None or row[0] not in manage_scopes:
            raise HTTPException(status_code=404, detail="not found")

    token = secrets.token_urlsafe(32)  # 256 bits of entropy
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(seconds=TOKEN_TTL_SECONDS_DEFAULT)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO agent_enrollment_tokens
                    (cluster_id, token_hash, created_by, expires_at)
                VALUES (:cluster_id, :token_hash, :created_by, :expires_at)
                """
            ),
            {
                "cluster_id": cluster_id,
                "token_hash": token_hash,
                "created_by": auth.principal.identity_id,
                "expires_at": expires_at,
            },
        )
    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=str(auth.principal.identity_id),
            action="agent.enrollment_token.create",
            result="success",
            scope_type="cluster",
            scope_id=str(cluster_id),
            target_type="agent_enrollment_token",
            target_id="redacted",
        ),
    )
    # The plaintext token appears EXACTLY once: here.
    return {
        "token": token,
        "cluster_id": str(cluster_id),
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": TOKEN_TTL_SECONDS_DEFAULT,
    }
