"""Connecting a customer's cluster (ADR-0037 §1), over real PostgreSQL.

- registering a cluster whose telemetry stays in it records that, and wires
  its Prometheus integration to the relay (`agent:<cluster_ref>`);
- the install file carries the cluster's id and name in all three places,
  the public listener URLs, the CA and a fresh one-time token stored as a
  hash only;
- a cluster read from the central store gets no install file, a Tracesis
  without a public listener says so, and a caller without
  `integration.manage` cannot tell the cluster exists.
"""

import hashlib
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_agent_inventory_integration import engine, migrated_db  # noqa: F401 - fixtures
from test_catalog_api_integration import build_users, seed_catalog_world

pytestmark = pytest.mark.integration

CA = "-----BEGIN CERTIFICATE-----\nMIIBtest\n-----END CERTIFICATE-----\n"


def install_settings(tmp_path: Path, **overrides: Any) -> Any:
    ca_file = tmp_path / "server-ca.pem"
    ca_file.write_text(CA, encoding="utf-8")
    values = {
        "agent_public_enrollment_url": "https://tracesis.example.test:30844",
        "agent_public_ingest_url": "https://tracesis.example.test:30843",
        "agent_server_ca_file": str(ca_file),
        "agent_chart_version": "0.1.0",
    }
    values.update(overrides)
    return require_it_settings().model_copy(update=values)


async def owner_post(harness: Any, db: AsyncEngine, path: str, body: Any = None) -> Any:
    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(db, harness.provider.issuer, "user-owner")
        me = (await owner.get("/v1/me")).json()
        return await owner.post(
            path,
            json=body,
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )


async def test_a_kept_cluster_gets_an_install_file_that_names_it_everywhere(
    engine: AsyncEngine,  # noqa: F811
    tmp_path: Path,
) -> None:
    await seed_catalog_world(engine)
    harness = build_harness(install_settings(tmp_path))
    created = await owner_post(
        harness,
        engine,
        "/v1/clusters",
        {"cluster_ref": "acme-prod", "display_name": "Acme", "telemetry_location": "cluster"},
    )
    assert created.status_code == 201, created.text
    cluster = created.json()
    assert cluster["telemetry_location"] == "cluster"
    async with engine.connect() as connection:
        config_ref = (
            await connection.execute(
                text(
                    "SELECT i.config_ref FROM integrations i JOIN clusters c "
                    "ON c.scope_id = i.scope_id WHERE c.id = :id "
                    "AND i.integration_type = 'prometheus'"
                ),
                {"id": cluster["id"]},
            )
        ).scalar_one()
    assert config_ref == "agent:acme-prod"

    response = await owner_post(harness, engine, f"/v1/clusters/{cluster['id']}/agent-install")
    assert response.status_code == 201, response.text
    body = response.json()
    values = yaml.safe_load(body["values_yaml"])
    assert values["cluster"] == {"id": cluster["id"], "name": "acme-prod"}
    assert values["clusterAgent"]["clusterId"] == cluster["id"]
    assert values["clusterAgent"]["clusterName"] == "acme-prod"
    assert values["telemetry"]["clusterName"] == "acme-prod"
    assert values["clusterAgent"]["apiBaseUrl"] == "https://tracesis.example.test:30843"
    assert values["clusterAgent"]["enrollmentBaseUrl"] == "https://tracesis.example.test:30844"
    assert values["tracesis"]["serverCA"] == CA
    assert "PRIVATE KEY" not in body["values_yaml"]
    assert body["file_name"] == "tracesis-agent-acme-prod.yaml"
    assert body["command"] == (
        "helm upgrade --install tracesis-agent "
        "oci://ghcr.io/duosis-developer-team/charts/tracesis-agent --version 0.1.0 "
        "--namespace tracesis --create-namespace -f tracesis-agent-acme-prod.yaml"
    )
    token = values["enrollment"]["token"]
    async with engine.connect() as connection:
        hashes = (
            (
                await connection.execute(
                    text("SELECT token_hash FROM agent_enrollment_tokens WHERE cluster_id = :id"),
                    {"id": cluster["id"]},
                )
            )
            .scalars()
            .all()
        )
    assert hashes == [hashlib.sha256(token.encode()).hexdigest()]


async def test_a_central_cluster_gets_no_install_file(
    engine: AsyncEngine,  # noqa: F811
    tmp_path: Path,
) -> None:
    world = await seed_catalog_world(engine)
    harness = build_harness(install_settings(tmp_path))
    response = await owner_post(
        harness, engine, f"/v1/clusters/{world['cluster_a'].id}/agent-install"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "cluster_reads_central_store"


async def test_without_a_public_listener_nothing_is_printed(
    engine: AsyncEngine,  # noqa: F811
    tmp_path: Path,
) -> None:
    await seed_catalog_world(engine)
    harness = build_harness(install_settings(tmp_path, agent_public_ingest_url=""))
    created = await owner_post(
        harness,
        engine,
        "/v1/clusters",
        {"cluster_ref": "acme-dev", "display_name": "Acme dev", "telemetry_location": "cluster"},
    )
    response = await owner_post(
        harness, engine, f"/v1/clusters/{created.json()['id']}/agent-install"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "agent_install_not_configured"
    async with engine.connect() as connection:
        minted = (
            await connection.execute(text("SELECT count(*) FROM agent_enrollment_tokens"))
        ).scalar_one()
    assert minted == 0


async def test_a_caller_without_integration_manage_cannot_tell_it_exists(
    engine: AsyncEngine,  # noqa: F811
    tmp_path: Path,
) -> None:
    world = await seed_catalog_world(engine)
    harness = await build_users(engine)
    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-cluster")
        me = (await viewer.get("/v1/me")).json()
        response = await viewer.post(
            f"/v1/clusters/{world['cluster_a'].id}/agent-install",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
    assert response.status_code == 404
