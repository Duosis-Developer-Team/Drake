"""Agent identity: proof-of-possession request authentication.

Transport mTLS (handshake-verified against the Drake Agent CA on the
internal listener) gates who can connect; THIS layer decides who the
caller IS. Every post-enrollment request carries a detached ECDSA
signature over `method\npath\nsha256(body)\ntimestamp\nnonce` made with
the enrolled private key. Identity derives exclusively from verifying
that signature against the stored PUBLIC key — claimed headers are never
trusted on their own, so spoofed identity headers are inert even inside
the TLS perimeter (ADR-0016).
"""

import base64
import hashlib
import time
import uuid
from dataclasses import dataclass

import redis.asyncio as aioredis
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException, Request
from sqlalchemy import text

from drake_api.db import get_engine
from drake_api.settings import Settings

HEADER_AGENT_ID = "X-Drake-Agent-Id"
HEADER_TIMESTAMP = "X-Drake-Agent-Timestamp"
HEADER_NONCE = "X-Drake-Agent-Nonce"
HEADER_SIGNATURE = "X-Drake-Agent-Signature"

_FRESHNESS_SECONDS = 120


@dataclass(frozen=True)
class AgentPrincipal:
    agent_id: uuid.UUID
    cluster_id: uuid.UUID
    public_key_pem: str


def canonical_string(method: str, path: str, body: bytes, timestamp: str, nonce: str) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{method}\n{path}\n{body_hash}\n{timestamp}\n{nonce}".encode()


def _refuse() -> HTTPException:
    # One generic refusal for every failure mode: no oracle.
    return HTTPException(status_code=403, detail="agent authentication failed")


async def authenticate_agent(request: Request) -> AgentPrincipal:
    settings: Settings = request.app.state.settings
    agent_id_raw = request.headers.get(HEADER_AGENT_ID, "")
    timestamp = request.headers.get(HEADER_TIMESTAMP, "")
    nonce = request.headers.get(HEADER_NONCE, "")
    signature_b64 = request.headers.get(HEADER_SIGNATURE, "")
    if not (agent_id_raw and timestamp and nonce and signature_b64):
        raise _refuse()
    try:
        agent_id = uuid.UUID(agent_id_raw)
        nonce_id = uuid.UUID(nonce)
        issued = int(timestamp)
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as error:
        raise _refuse() from error
    if abs(time.time() - issued) > _FRESHNESS_SECONDS:
        raise _refuse()

    engine = get_engine(settings)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT cluster_id, public_key_pem, certificate_not_after
                    FROM cluster_agents
                    WHERE id = :id AND lifecycle = 'active'
                    """
                ),
                {"id": agent_id},
            )
        ).first()
    if row is None:
        raise _refuse()
    if row[2].timestamp() < time.time():
        raise _refuse()  # expired identity cannot act

    public_key = serialization.load_pem_public_key(str(row[1]).encode())
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise _refuse()
    body = await request.body()
    message = canonical_string(request.method, request.url.path, body, timestamp, nonce)
    try:
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as error:
        raise _refuse() from error

    # Nonce replay protection, bounded by the freshness window.
    redis = aioredis.from_url(settings.redis_url)
    try:
        fresh = await redis.set(
            f"agent:nonce:{agent_id}:{nonce_id}", "1", nx=True, ex=_FRESHNESS_SECONDS * 2
        )
    finally:
        await redis.aclose()
    if not fresh:
        raise _refuse()

    return AgentPrincipal(agent_id=agent_id, cluster_id=row[0], public_key_pem=str(row[1]))
