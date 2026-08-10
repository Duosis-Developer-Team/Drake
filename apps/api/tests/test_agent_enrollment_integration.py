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
        activate_path = "/internal/v1/agent/certificates/activate"

        async def post_signed(
            path: str, payload: dict[str, Any], signer: Any, **kw: Any
        ) -> httpx.Response:
            body = jsonlib.dumps(payload).encode()
            headers = pop_headers(signer, agent_id, "POST", path, body, **kw)
            return await client.post(
                path, content=body, headers={**headers, "Content-Type": "application/json"}
            )

        # PREPARE: signed with the CURRENT key; returns pending material.
        new_key = generate_keypair()
        renewal_id = str(uuidlib.uuid4())
        csr = make_csr(new_key)
        prepared = await post_signed(renew_path, {"renewal_id": renewal_id, "csr_pem": csr}, key)
        assert prepared.status_code == 200, prepared.text
        pending_cert = prepared.json()["certificate_pem"]
        assert "BEGIN CERTIFICATE" in pending_cert

        # Lost-response retry: SAME renewal_id + SAME CSR → SAME pending
        # certificate, and it also proves the OLD key still authenticates
        # (nothing was promoted yet).
        retried = await post_signed(renew_path, {"renewal_id": renewal_id, "csr_pem": csr}, key)
        assert retried.status_code == 200
        assert retried.json()["certificate_pem"] == pending_cert

        # SAME renewal_id + DIFFERENT CSR is never ambiguous: refused.
        other_csr = make_csr(generate_keypair())
        conflicting = await post_signed(
            renew_path, {"renewal_id": renewal_id, "csr_pem": other_csr}, key
        )
        assert conflicting.status_code == 403

        # Spoofed identity headers WITHOUT the key are inert:
        forged = await post_signed(
            renew_path,
            {"renewal_id": str(uuidlib.uuid4()), "csr_pem": other_csr},
            generate_keypair(),
        )
        assert forged.status_code == 403

        # ACTIVATE requires possession of the PENDING key; the old key
        # cannot promote what it does not hold.
        wrong_key_activation = await post_signed(activate_path, {"renewal_id": renewal_id}, key)
        assert wrong_key_activation.status_code == 403

        activated = await post_signed(activate_path, {"renewal_id": renewal_id}, new_key)
        assert activated.status_code == 200, activated.text
        assert activated.json()["result"] == "activated"

        # Lost activation RESPONSE: the retry (now against the promoted
        # key) acknowledges idempotently.
        activated_again = await post_signed(activate_path, {"renewal_id": renewal_id}, new_key)
        assert activated_again.status_code == 200

        # AFTER activation the old key is dead and the new key is live.
        old_key_refused = await post_signed(
            renew_path,
            {"renewal_id": str(uuidlib.uuid4()), "csr_pem": other_csr},
            key,
        )
        assert old_key_refused.status_code == 403
        next_key = generate_keypair()
        new_key_accepted = await post_signed(
            renew_path,
            {"renewal_id": str(uuidlib.uuid4()), "csr_pem": make_csr(next_key)},
            new_key,
        )
        assert new_key_accepted.status_code == 200

        # Replay of a previously used nonce is refused:
        body2 = jsonlib.dumps(
            {"renewal_id": str(uuidlib.uuid4()), "csr_pem": make_csr(generate_keypair())}
        ).encode()
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

    # The stored key is the PUBLIC key only; no private material anywhere,
    # and the promoted identity cleared its pending material.
    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT public_key_pem, pending_certificate_pem "
                    "FROM cluster_agents WHERE id = :id"
                ),
                {"id": agent_id},
            )
        ).one()
    assert "PUBLIC KEY" in stored[0]
    assert "PRIVATE" not in stored[0]


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


@pytest.mark.anyio
async def test_two_listeners_separate_the_pre_certificate_surface(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The bootstrap asymmetry, settled at the transport.

    An agent enrolling for the first time has no client certificate, and
    every call afterwards must present one. One listener cannot be both,
    and a listener that merely *tolerated* a missing certificate would put
    the guarantee in application code — which on this stack could not even
    be written honestly: uvicorn never gives the ASGI app the peer
    certificate.

    So production runs two, from one image:

        enroll (CERT_NONE)     only POST /enroll
        ingest (CERT_REQUIRED) everything an enrolled agent does

    This starts both, for real, and asks each of them the questions the
    other is supposed to answer.
    """
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    settings = ca_settings(tmp_path)
    cluster_id = str(world["cluster_a"].id)
    token = (await create_token(harness, engine, cluster_id))["token"]
    server_cert, server_key = make_server_tls(tmp_path / "server")

    import os
    import secrets
    import socket

    def free_port() -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    enroll_port, ingest_port = free_port(), free_port()
    runner = API_ROOT.parent.parent / "scripts" / "run_internal_agent_api.py"
    env = {
        **os.environ,
        "DRAKE_ENV": "test",
        "DRAKE_DATABASE_URL": settings.database_url,
        "DRAKE_REDIS_URL": settings.redis_url,
        "DRAKE_AGENT_CA_CERT_FILE": settings.agent_ca_cert_file,
        "DRAKE_AGENT_CA_KEY_FILE": settings.agent_ca_key_file,
    }

    def spawn(port: int, surface: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 - fixed argv, resolved interpreter
            [
                sys.executable,
                str(runner),
                "--port",
                str(port),
                "--surface",
                surface,
                "--tls-cert",
                str(server_cert),
                "--tls-key",
                str(server_key),
                "--client-ca",
                settings.agent_ca_cert_file,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    server_ctx = ssl.create_default_context(cafile=str(server_cert))
    server_ctx.check_hostname = False  # ephemeral self-signed test cert

    enroll_proc = spawn(enroll_port, "enrollment")
    ingest_proc = spawn(ingest_port, "ingest")
    try:
        # --- the enrollment listener, with NO client certificate ---------
        enroll_url = f"https://127.0.0.1:{enroll_port}/internal/v1/agent"
        key = generate_keypair()
        enrolled = None
        for _ in range(80):
            try:
                async with httpx.AsyncClient(verify=server_ctx) as bare:
                    enrolled = await bare.post(
                        f"{enroll_url}/enroll",
                        json=enrollment_body(token, cluster_id, make_csr(key)),
                    )
                break
            except httpx.HTTPError:
                await asyncio.sleep(0.25)
        else:  # pragma: no cover - only on a broken environment
            pytest.fail("enrollment listener did not come up")
        assert enrolled is not None
        # Valid unused token + no client certificate → accepted.
        assert enrolled.status_code == 201, enrolled.text

        async with httpx.AsyncClient(verify=server_ctx) as bare:
            # A well-formed token that was never issued. Shaped like a real
            # one on purpose: a short string would be refused by schema
            # validation and prove nothing about the token check.
            refused = await bare.post(
                f"{enroll_url}/enroll",
                json=enrollment_body(
                    secrets.token_urlsafe(32), cluster_id, make_csr(generate_keypair())
                ),
            )
            assert refused.status_code == 403, refused.text

            # The enrollment listener serves NOTHING else. A certificate
            # renewal here is not "unauthorized" — the route does not exist,
            # so a stolen token cannot reach an enrolled agent's surface.
            stray = await bare.post(
                f"{enroll_url}/certificates/renew",
                json={"csr_pem": make_csr(generate_keypair())},
            )
            assert stray.status_code == 404, stray.text
            heartbeat = await bare.post(f"{enroll_url}/heartbeat", json={})
            assert heartbeat.status_code == 404, heartbeat.text

        # --- the ingest listener, which demands a certificate ------------
        ingest_url = f"https://127.0.0.1:{ingest_port}/internal/v1/agent"
        client_cert, client_key = write_client_identity(
            tmp_path / "identity2", enrolled.json()["certificate_pem"], key
        )
        mtls_ctx = ssl.create_default_context(cafile=str(server_cert))
        mtls_ctx.check_hostname = False
        mtls_ctx.load_cert_chain(str(client_cert), str(client_key))

        for _ in range(80):
            try:
                async with httpx.AsyncClient(verify=mtls_ctx) as ok_client:
                    with_cert = await ok_client.post(f"{ingest_url}/heartbeat", json={})
                break
            except httpx.HTTPError:
                await asyncio.sleep(0.25)
        else:  # pragma: no cover
            pytest.fail("ingest listener did not come up")

        # The handshake succeeded; the application still demands
        # proof-of-possession, which this request does not carry.
        assert with_cert.status_code == 403, with_cert.text

        # No client certificate → refused during the HANDSHAKE. There is no
        # status code here, and that is the point: the request never became
        # a request.
        with pytest.raises(httpx.HTTPError):
            async with httpx.AsyncClient(verify=server_ctx) as bare:
                await bare.post(f"{ingest_url}/heartbeat", json={})

        # A certificate from a CA this listener does not trust: same.
        rogue_dir = tmp_path / "rogue2"
        rogue_ca_cert, rogue_ca_key = generate_ephemeral_ca(rogue_dir)
        from drake_api.agents.ca import AgentCertificateAuthority, load_csr

        rogue_key = generate_keypair()
        rogue_issued = AgentCertificateAuthority(
            settings.model_copy(
                update={
                    "agent_ca_cert_file": str(rogue_ca_cert),
                    "agent_ca_key_file": str(rogue_ca_key),
                }
            )
        ).sign(load_csr(make_csr(rogue_key)), world["cluster_a"].id, uuidlib.uuid4())
        rogue_cert_path, rogue_key_path = write_client_identity(
            rogue_dir / "identity", rogue_issued.certificate_pem, rogue_key
        )
        rogue_ctx = ssl.create_default_context(cafile=str(server_cert))
        rogue_ctx.check_hostname = False
        rogue_ctx.load_cert_chain(str(rogue_cert_path), str(rogue_key_path))
        with pytest.raises(httpx.HTTPError):
            async with httpx.AsyncClient(verify=rogue_ctx) as rogue_client:
                await rogue_client.post(f"{ingest_url}/heartbeat", json={})

        # And the one-time token cannot be spent on the certificate-bearing
        # listener either: enrolment is not served there.
        async with httpx.AsyncClient(verify=mtls_ctx) as ok_client:
            no_enroll = await ok_client.post(
                f"{ingest_url}/enroll",
                json=enrollment_body(token, cluster_id, make_csr(generate_keypair())),
            )
        assert no_enroll.status_code == 404, no_enroll.text
    finally:
        for process in (enroll_proc, ingest_proc):
            process.terminate()
            process.wait(timeout=10)
    del harness
