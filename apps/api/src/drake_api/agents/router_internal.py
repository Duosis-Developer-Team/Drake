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
from drake_api.agents.identity import (
    AgentPrincipal,
    authenticate_agent,
    verify_pop_signature,
)
from drake_api.audit import AuditEventData, record_audit_event
from drake_api.db import get_engine
from drake_api.settings import Settings

#: The enrollment surface. It is the ONE internal endpoint an agent can
#: reach before it holds a client certificate, so in production it is served
#: by its own listener — server-authenticated TLS, no client certificate
#: asked for — and that listener carries nothing else.
enrollment_router = APIRouter(prefix="/internal/v1/agent", tags=["agent-enrollment"])

#: Everything an ENROLLED agent does. In production this is served only by
#: the mutual-TLS listener, so "no client certificate" is refused during the
#: handshake rather than by a check some future edit could forget.
certificate_router = APIRouter(prefix="/internal/v1/agent", tags=["agent-internal"])

#: Both surfaces on one router, for local, test and CI where a single
#: listener is simpler and the transport is not the thing under test.
router = APIRouter()


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
    renewal_id: uuid.UUID
    csr_pem: str = Field(min_length=200, max_length=8192)


class ActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    renewal_id: uuid.UUID


# Pending renewals are short-lived: long enough for retries, short enough
# that abandoned prepares cannot linger as usable material.
_PENDING_RENEWAL_TTL_SECONDS = 900


def _enrollment_refused() -> HTTPException:
    # Used, expired, unknown, wrong-cluster, and malformed inputs are all
    # indistinguishable: one generic refusal.
    return HTTPException(status_code=403, detail="enrollment refused")


@enrollment_router.post("/enroll", status_code=201)
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
        # The newest enrolled agent becomes the ONE active inventory
        # writer for the cluster; any previous agent is superseded and
        # can no longer touch the projection (ADR-0017).
        await connection.execute(
            text(
                """
                INSERT INTO cluster_inventory_state (cluster_id, active_agent_id)
                VALUES (:cluster_id, :agent_id)
                ON CONFLICT (cluster_id) DO UPDATE
                SET active_agent_id = EXCLUDED.active_agent_id, updated_at = now()
                """
            ),
            {"cluster_id": body.cluster_id, "agent_id": agent_id},
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


@certificate_router.post("/certificates/renew")
async def renew_certificate(
    request: Request,
    body: RenewalRequest,
    principal: AgentPrincipal = Depends(authenticate_agent),
) -> dict[str, Any]:
    """PREPARE phase of the two-phase renewal (crash/retry safe).

    Signs the CSR into PENDING material only — the current key stays fully
    valid until the agent proves possession of the new key via /activate.
    A lost response is recovered by retrying the SAME (renewal_id, CSR),
    which returns the SAME pending certificate; the same renewal_id with a
    DIFFERENT CSR is refused. Only public material is ever stored.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    try:
        csr = load_csr(body.csr_pem)
    except CsrError as error:
        raise HTTPException(status_code=403, detail="agent authentication failed") from error
    csr_hash = hashlib.sha256(body.csr_pem.encode()).hexdigest()

    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT pending_renewal_id, pending_csr_hash, pending_certificate_pem,
                           pending_certificate_not_after, pending_expires_at,
                           pending_public_key_pem
                    FROM cluster_agents
                    WHERE id = :id AND lifecycle = 'active'
                    FOR UPDATE
                    """
                ),
                {"id": principal.agent_id},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=403, detail="agent authentication failed")
        pending_live = (
            row[0] is not None
            and row[4] is not None
            and row[4].timestamp() > _utcnow().timestamp()
            and row[5] is not None
        )
        if pending_live and row[0] == body.renewal_id:
            if row[1] != csr_hash:
                # Same renewal id, different key material: never ambiguous.
                raise HTTPException(status_code=403, detail="agent authentication failed")
            # Idempotent retry after a lost response: same pending result.
            ca_chain = _ca(request).ca_pem
            return _renewal_response(principal.agent_id, str(row[2]), ca_chain, row[3])

        # New renewal (or an expired/superseded pending): the row lock makes
        # concurrent prepares deterministic — the last one fully replaces
        # the pending slot; nothing touches the CURRENT key.
        issued = _ca(request).sign(csr, principal.cluster_id, principal.agent_id)
        await connection.execute(
            text(
                """
                UPDATE cluster_agents
                SET pending_renewal_id = :renewal_id,
                    pending_csr_hash = :csr_hash,
                    pending_public_key_pem = :public_key,
                    pending_certificate_pem = :certificate,
                    pending_certificate_serial = :serial,
                    pending_certificate_not_after = :not_after,
                    pending_expires_at = now() + make_interval(secs => :ttl)
                WHERE id = :id
                """
            ),
            {
                "renewal_id": body.renewal_id,
                "csr_hash": csr_hash,
                "public_key": issued.public_key_pem,
                "certificate": issued.certificate_pem,
                "serial": issued.serial,
                "not_after": issued.not_after,
                "ttl": _PENDING_RENEWAL_TTL_SECONDS,
                "id": principal.agent_id,
            },
        )
    return _renewal_response(
        principal.agent_id, issued.certificate_pem, issued.ca_chain_pem, issued.not_after
    )


def _renewal_response(
    agent_id: uuid.UUID, certificate_pem: str, ca_chain_pem: str, not_after: dt.datetime
) -> dict[str, Any]:
    return {
        "api_version": "drake.duosis.com/agent/v1",
        "kind": "renewal_response",
        "agent_id": str(agent_id),
        "certificate_pem": certificate_pem,
        "ca_chain_pem": ca_chain_pem,
        "certificate_not_after": not_after.isoformat(),
    }


@certificate_router.post("/certificates/activate")
async def activate_certificate(request: Request, body: ActivationRequest) -> dict[str, Any]:
    """ACTIVATE phase: proof of possession of the NEW key promotes it.

    The request is signed with the PENDING key — that signature IS the
    activation proof. Idempotent: if the promotion already happened and
    the response was lost, a retry signed with the (now current) new key
    for the same renewal_id acknowledges success. All failures share the
    generic agent refusal.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    raw_body = await request.body()
    async with engine.begin() as connection:
        agent_id = _header_agent_id(request)
        row = (
            await connection.execute(
                text(
                    """
                    SELECT public_key_pem, certificate_serial,
                           pending_renewal_id, pending_public_key_pem,
                           pending_certificate_serial, pending_certificate_not_after,
                           pending_expires_at
                    FROM cluster_agents
                    WHERE id = :id AND lifecycle = 'active'
                    FOR UPDATE
                    """
                ),
                {"id": agent_id},
            )
        ).first()
        if row is None or row[2] != body.renewal_id:
            raise HTTPException(status_code=403, detail="agent authentication failed")

        pending_live = (
            row[3] is not None and row[6] is not None and row[6].timestamp() > _utcnow().timestamp()
        )
        if pending_live:
            # Possession of the pending private key is the promotion proof.
            await verify_pop_signature(request, raw_body, str(row[3]), settings)
            await connection.execute(
                text(
                    """
                    UPDATE cluster_agents
                    SET public_key_pem = pending_public_key_pem,
                        certificate_serial = pending_certificate_serial,
                        certificate_not_after = pending_certificate_not_after,
                        pending_csr_hash = NULL,
                        pending_public_key_pem = NULL,
                        pending_certificate_pem = NULL,
                        pending_certificate_serial = NULL,
                        pending_certificate_not_after = NULL,
                        pending_expires_at = NULL
                    WHERE id = :id
                    """
                ),
                {"id": agent_id},
            )
            return {
                "api_version": "drake.duosis.com/agent/v1",
                "kind": "activation_response",
                "result": "activated",
            }
        if row[3] is None and row[2] == body.renewal_id:
            # Already promoted (lost activation response): the new key is
            # now the CURRENT key — verifying against it acknowledges.
            await verify_pop_signature(request, raw_body, str(row[0]), settings)
            return {
                "api_version": "drake.duosis.com/agent/v1",
                "kind": "activation_response",
                "result": "activated",
            }
    raise HTTPException(status_code=403, detail="agent authentication failed")


def _header_agent_id(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(request.headers.get("X-Drake-Agent-Id", ""))
    except ValueError as error:
        raise HTTPException(status_code=403, detail="agent authentication failed") from error


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


router.include_router(enrollment_router)
router.include_router(certificate_router)
