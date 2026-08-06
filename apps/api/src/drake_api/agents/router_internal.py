"""Internal agent API (enrollment + certificate lifecycle).

Served ONLY by the dedicated internal listener (ADR-0016): server TLS for
enrollment, CERT_REQUIRED mTLS + proof-of-possession for everything else.
No sessions, cookies, or CSRF identities exist here; failures are one
generic refusal (no oracle).
"""

import datetime as dt
import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from drake_api.agents.ca import AgentCertificateAuthority, CsrError, load_csr
from drake_api.agents.identity import AgentPrincipal, authenticate_agent
from drake_api.audit import AuditEventData, record_audit_event
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/internal/v1/agent", tags=["agent-internal"])


def _ca(request: Request) -> AgentCertificateAuthority:
    ca: AgentCertificateAuthority = request.app.state.agent_ca
    return ca


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_version: str = Field(pattern="^drake\\.duosis\\.com/agent/v1$")
    kind: str = Field(pattern="^enrollment_request$")
    token: str = Field(min_length=32, max_length=128)
    csr_pem: str = Field(min_length=200, max_length=8192)
    cluster_id: uuid.UUID
    agent_version: str = Field(min_length=1, max_length=64)


class RenewalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csr_pem: str = Field(min_length=200, max_length=8192)


def _enrollment_refused() -> HTTPException:
    # Used, expired, unknown, wrong-cluster, and malformed inputs are all
    # indistinguishable: one generic refusal.
    return HTTPException(status_code=403, detail="enrollment refused")


@router.post("/enroll", status_code=201)
async def enroll(request: Request, body: EnrollmentRequest) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        csr = load_csr(body.csr_pem)
    except CsrError as error:
        raise _enrollment_refused() from error

    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    agent_id = uuid.uuid4()
    async with engine.begin() as connection:
        consumed = (
            await connection.execute(
                text(
                    """
                    UPDATE agent_enrollment_tokens
                    SET used_at = now(), used_by_agent = :agent_id
                    WHERE token_hash = :token_hash
                      AND cluster_id = :cluster_id
                      AND used_at IS NULL
                      AND expires_at > now()
                    RETURNING id
                    """
                ),
                {
                    "token_hash": token_hash,
                    "cluster_id": body.cluster_id,
                    "agent_id": agent_id,
                },
            )
        ).first()
        if consumed is None:
            raise _enrollment_refused()

        issued = _ca(request).sign(csr, body.cluster_id, agent_id)
        await connection.execute(
            text(
                """
                INSERT INTO cluster_agents
                    (id, cluster_id, agent_version, public_key_pem,
                     certificate_serial, certificate_not_after)
                VALUES (:id, :cluster_id, :version, :public_key, :serial, :not_after)
                """
            ),
            {
                "id": agent_id,
                "cluster_id": body.cluster_id,
                "version": body.agent_version,
                "public_key": issued.public_key_pem,
                "serial": issued.serial,
                "not_after": issued.not_after,
            },
        )
    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="service",
            actor_id=f"agent:{agent_id}",
            action="agent.enrollment.consume",
            result="success",
            scope_type="cluster",
            scope_id=str(body.cluster_id),
            target_type="cluster_agent",
            target_id=str(agent_id),
        ),
    )
    return {
        "api_version": "drake.duosis.com/agent/v1",
        "kind": "enrollment_response",
        "agent_id": str(agent_id),
        "cluster_id": str(body.cluster_id),
        "certificate_pem": issued.certificate_pem,
        "ca_chain_pem": issued.ca_chain_pem,
        "certificate_not_after": issued.not_after.isoformat(),
    }


@router.post("/certificates/renew")
async def renew_certificate(
    request: Request,
    body: RenewalRequest,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        csr = load_csr(body.csr_pem)
    except CsrError as error:
        raise HTTPException(status_code=403, detail="agent authentication failed") from error

    # Identity comes from the VERIFIED principal — never from claimed IDs.
    issued = _ca(request).sign(csr, principal.cluster_id, principal.agent_id)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE cluster_agents
                SET public_key_pem = :public_key,
                    certificate_serial = :serial,
                    certificate_not_after = :not_after
                WHERE id = :id AND lifecycle = 'active'
                """
            ),
            {
                "id": principal.agent_id,
                "public_key": issued.public_key_pem,
                "serial": issued.serial,
                "not_after": issued.not_after,
            },
        )
    return {
        "api_version": "drake.duosis.com/agent/v1",
        "kind": "renewal_response",
        "agent_id": str(principal.agent_id),
        "certificate_pem": issued.certificate_pem,
        "ca_chain_pem": issued.ca_chain_pem,
        "certificate_not_after": issued.not_after.isoformat(),
    }


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
