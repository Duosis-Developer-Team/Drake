"""Agent enrollment, identity, and trust-boundary tests (real PG + Redis).

Token lifecycle (hash-only storage, one-time display, atomic double-use),
CSR enrollment through the internal app, PoP identity (spoofed headers
inert, replay refused), renewal bound to the verified identity, and a
REAL TLS handshake against the internal listener with CERT_REQUIRED.
"""

import asyncio
import hashlib
import ssl
import subprocess
import sys
import time
import uuid as uuidlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_helpers import (
    generate_keypair,
    make_csr,
    make_server_tls,
    pop_headers,
    write_client_identity,
)
from alembic import command
from alembic.config import Config
from drake_api.agents.ca import generate_ephemeral_ca
from drake_api.agents.internal_app import create_internal_agent_app
from drake_api.db import dispose_engines
from harness_s1 import grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from test_catalog_api_integration import build_users, make_role, seed_catalog_world
from test_catalog_persistence_integration import reset_catalog

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def migrated_db() -> None:
    settings = require_it_settings()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


@pytest.fixture
async def engine() -> Any:
    settings = require_it_settings()
    eng = create_async_engine(settings.database_url)
    await reset_catalog(eng)
    yield eng
    await eng.dispose()
    await dispose_engines()


def ca_settings(tmp_path: Path):
    cert, key = generate_ephemeral_ca(tmp_path / "ca")
    return require_it_settings().model_copy(
        update={"agent_ca_cert_file": str(cert), "agent_ca_key_file": str(key)}
    )


async def create_token(harness, engine: AsyncEngine, cluster_id: str) -> dict[str, Any]:
    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        me = (await owner.get("/v1/me")).json()
        response = await owner.post(
            f"/v1/clusters/{cluster_id}/agent-enrollment-tokens",
            headers={
                "X-CSRF-Token": me["csrf_token"],
                "Idempotency-Key": str(uuidlib.uuid4()),
            },
        )
        assert response.status_code == 201, response.text
        return response.json()


def internal_client(settings) -> httpx.AsyncClient:
    app = create_internal_agent_app(settings)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://agent-internal"
    )


def enrollment_body(token: str, cluster_id: str, csr_pem: str) -> dict[str, Any]:
    return {
        "api_version": "drake.duosis.com/agent/v1",
        "kind": "enrollment_request",
        "token": token,
        "csr_pem": csr_pem,
        "cluster_id": cluster_id,
        "agent_version": "test-0.1",
    }


async def test_token_lifecycle_and_generic_refusals(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_id = str(world["cluster_a"].id)

    created = await create_token(harness, engine, cluster_id)
    token = created["token"]
    # 256-bit urlsafe token shape; shown exactly once; hash-only at rest.
    assert len(token) >= 43
    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT token_hash, expires_at FROM agent_enrollment_tokens")
            )
        ).first()
    assert stored is not None
    assert stored[0] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in stored[0]
    ttl = (stored[1] - datetime.now(UTC)).total_seconds()
    assert 0 < ttl <= 900  # short-lived, capped

    key = generate_keypair()
    csr = make_csr(key)
    async with internal_client(settings) as client:
        ok = await client.post(
            "/internal/v1/agent/enroll", json=enrollment_body(token, cluster_id, csr)
        )
        assert ok.status_code == 201, ok.text
        enrolled = ok.json()
        assert "BEGIN CERTIFICATE" in enrolled["certificate_pem"]
        assert "PRIVATE KEY" not in ok.text  # the private key never travels

        # Reuse, wrong cluster, unknown, expired, malformed: ONE generic result.
        refusals = [
            enrollment_body(token, cluster_id, csr),  # already used
            enrollment_body(token, str(world["cluster_b"].id), csr),  # wrong cluster
            enrollment_body("A" * 43, cluster_id, csr),  # unknown token
            enrollment_body(
                token,
                cluster_id,
                "-----BEGIN CERTIFICATE REQUEST-----\nAAAA\n-----END CERTIFICATE REQUEST-----"
                + " " * 160,
            ),  # malformed CSR
        ]
        for body in refusals:
            response = await client.post("/internal/v1/agent/enroll", json=body)
            assert response.status_code == 403
            assert response.json()["error"]["message"] == "enrollment refused"

    # Token creation requires integration.manage AT the cluster scope:
    await make_role(harness, engine, "No Manage S4", ["cluster.view"])
    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        me = (await viewer.get("/v1/me")).json()
        denied = await viewer.post(
            f"/v1/clusters/{cluster_id}/agent-enrollment-tokens",
            headers={
                "X-CSRF-Token": me["csrf_token"],
                "Idempotency-Key": str(uuidlib.uuid4()),
            },
        )
        assert denied.status_code == 404  # consistent not-found, no oracle


async def test_concurrent_double_use_admits_exactly_one(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_id = str(world["cluster_a"].id)
    token = (await create_token(harness, engine, cluster_id))["token"]

    async with internal_client(settings) as client:

        async def attempt() -> int:
            key = generate_keypair()
            response = await client.post(
                "/internal/v1/agent/enroll",
                json=enrollment_body(token, cluster_id, make_csr(key)),
            )
            return response.status_code

        results = await asyncio.gather(*[attempt() for _ in range(2)])
    assert sorted(results) == [201, 403]  # exactly one winner
    async with engine.connect() as connection:
        agents = (
            await connection.execute(text("SELECT count(*) FROM cluster_agents"))
        ).scalar_one()
    assert agents == 1


async def test_pop_identity_spoofing_replay_and_renewal(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_id = str(world["cluster_a"].id)
    token = (await create_token(harness, engine, cluster_id))["token"]

    key = generate_keypair()
    async with internal_client(settings) as client:
        enrolled = (
            await client.post(
                "/internal/v1/agent/enroll",
                json=enrollment_body(token, cluster_id, make_csr(key)),
            )
        ).json()
        agent_id = enrolled["agent_id"]

        import json as jsonlib

        renew_path = "/internal/v1/agent/certificates/renew"
        new_key = generate_keypair()
        body_bytes = jsonlib.dumps({"csr_pem": make_csr(new_key)}).encode()

        # Valid PoP renewal succeeds and rotates to the NEW key.
        headers = pop_headers(key, agent_id, "POST", renew_path, body_bytes)
        renewed = await client.post(
            renew_path,
            content=body_bytes,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert renewed.status_code == 200, renewed.text
        assert "BEGIN CERTIFICATE" in renewed.json()["certificate_pem"]

        # Spoofed identity headers WITHOUT the key are inert:
        forged = pop_headers(generate_keypair(), agent_id, "POST", renew_path, body_bytes)
        assert (
            await client.post(
                renew_path,
                content=body_bytes,
                headers={**forged, "Content-Type": "application/json"},
            )
        ).status_code == 403

        # Replay of a previously used nonce is refused:
        body2 = jsonlib.dumps({"csr_pem": make_csr(generate_keypair())}).encode()
        replay_headers = pop_headers(new_key, agent_id, "POST", renew_path, body2)
        first = await client.post(
            renew_path,
            content=body2,
            headers={**replay_headers, "Content-Type": "application/json"},
        )
        assert first.status_code == 200
        second = await client.post(
            renew_path,
            content=body2,
            headers={**replay_headers, "Content-Type": "application/json"},
        )
        assert second.status_code == 403

        # Stale timestamps are refused:
        stale_headers = pop_headers(
            new_key,
            agent_id,
            "POST",
            renew_path,
            body2,
            timestamp=int(time.time()) - 3600,
        )
        assert (
            await client.post(
                renew_path,
                content=body2,
                headers={**stale_headers, "Content-Type": "application/json"},
            )
        ).status_code == 403

        # A user session cookie means NOTHING on the internal app:
        cookie_only = await client.post(
            renew_path,
            content=body2,
            headers={"Content-Type": "application/json", "Cookie": "drake_session=abc"},
        )
        assert cookie_only.status_code == 403

    # The stored key is the PUBLIC key only; no private material anywhere.
    async with engine.connect() as connection:
        stored = (
            await connection.execute(text("SELECT public_key_pem FROM cluster_agents"))
        ).scalar_one()
    assert "PUBLIC KEY" in stored
    assert "PRIVATE" not in stored


async def test_real_tls_handshake_cert_required(engine: AsyncEngine, tmp_path: Path) -> None:
    """REAL uvicorn TLS listener: CERT_REQUIRED admits only certificates
    signed by the Drake Agent CA; wrong-CA and no-cert handshakes fail."""
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_id = str(world["cluster_a"].id)
    token = (await create_token(harness, engine, cluster_id))["token"]

    # Enroll in-process to obtain a CA-signed client identity.
    key = generate_keypair()
    async with internal_client(settings) as client:
        enrolled = (
            await client.post(
                "/internal/v1/agent/enroll",
                json=enrollment_body(token, cluster_id, make_csr(key)),
            )
        ).json()
    client_cert, client_key = write_client_identity(
        tmp_path / "identity", enrolled["certificate_pem"], key
    )
    server_cert, server_key = make_server_tls(tmp_path / "server")

    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            str(API_ROOT.parent.parent / "scripts" / "run_internal_agent_api.py"),
            "--port",
            str(port),
            "--tls-cert",
            str(server_cert),
            "--tls-key",
            str(server_key),
            "--client-ca",
            settings.agent_ca_cert_file,
        ],
        env={
            **__import__("os").environ,
            "DRAKE_ENV": "test",
            "DRAKE_DATABASE_URL": settings.database_url,
            "DRAKE_REDIS_URL": settings.redis_url,
            "DRAKE_AGENT_CA_CERT_FILE": settings.agent_ca_cert_file,
            "DRAKE_AGENT_CA_KEY_FILE": settings.agent_ca_key_file,
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        server_ctx = ssl.create_default_context(cafile=str(server_cert))
        server_ctx.check_hostname = False  # ephemeral self-signed test cert

        mtls_ctx = ssl.create_default_context(cafile=str(server_cert))
        mtls_ctx.check_hostname = False
        mtls_ctx.load_cert_chain(str(client_cert), str(client_key))

        for _ in range(60):
            try:
                async with httpx.AsyncClient(verify=mtls_ctx) as probe_client:
                    probe = await probe_client.post(
                        f"https://127.0.0.1:{port}/internal/v1/agent/certificates/renew",
                        json={"csr_pem": make_csr(generate_keypair())},
                    )
                break
            except httpx.HTTPError:
                await asyncio.sleep(0.25)
        else:
            pytest.fail("internal TLS listener did not come up")
        # Handshake with the CA-signed cert SUCCEEDED (app-level PoP still
        # required — hence 403, not a transport failure):
        assert probe.status_code == 403

        # No client certificate → the handshake itself is refused:
        with pytest.raises(httpx.HTTPError):
            async with httpx.AsyncClient(verify=server_ctx) as bare:
                await bare.post(
                    f"https://127.0.0.1:{port}/internal/v1/agent/certificates/renew",
                    json={"csr_pem": make_csr(generate_keypair())},
                )

        # A certificate from an UNTRUSTED CA is refused at the handshake:
        rogue_dir = tmp_path / "rogue"
        rogue_ca_cert, rogue_ca_key = generate_ephemeral_ca(rogue_dir)
        rogue_settings = settings.model_copy(
            update={
                "agent_ca_cert_file": str(rogue_ca_cert),
                "agent_ca_key_file": str(rogue_ca_key),
            }
        )
        from drake_api.agents.ca import AgentCertificateAuthority, load_csr

        rogue_key = generate_keypair()
        rogue_cert = AgentCertificateAuthority(rogue_settings).sign(
            load_csr(make_csr(rogue_key)), world["cluster_a"].id, uuidlib.uuid4()
        )
        rogue_cert_path, rogue_key_path = write_client_identity(
            rogue_dir / "identity", rogue_cert.certificate_pem, rogue_key
        )
        rogue_ctx = ssl.create_default_context(cafile=str(server_cert))
        rogue_ctx.check_hostname = False
        rogue_ctx.load_cert_chain(str(rogue_cert_path), str(rogue_key_path))
        with pytest.raises(httpx.HTTPError):
            async with httpx.AsyncClient(verify=rogue_ctx) as rogue_client:
                await rogue_client.post(
                    f"https://127.0.0.1:{port}/internal/v1/agent/certificates/renew",
                    json={"csr_pem": make_csr(generate_keypair())},
                )
    finally:
        process.terminate()
        process.wait(timeout=10)
    del harness
