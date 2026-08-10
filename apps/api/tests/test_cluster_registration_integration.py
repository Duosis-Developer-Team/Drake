"""Registering a cluster: the one catalog write an operator can make.

Every other catalog row arrives through onboarding — manifest, plan,
approval, apply. A cluster cannot, because it is what a manifest *refers
to*: `clusterRef: duosis-prod-1` names something that must already exist,
and an agent cannot be given an enrolment token for a cluster Drake has
never heard of.

Before this, the only code that created one was `catalog.bootstrap`, which
fails closed outside local and test and is exposed through no API. A
production Drake could not be told about a cluster by any supported means.

So the tests here are mostly about the boundary rather than the row: who
may call it, what a repeat does, and what is written down afterwards.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
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


async def _post(harness: Any, subject: str, payload: dict[str, Any]) -> Any:
    async with harness.api_client() as client:
        await harness.login(client, subject)
        me = (await client.get("/v1/me")).json()
        return await client.post(
            "/v1/clusters",
            json=payload,
            headers={"X-CSRF-Token": me["csrf_token"]},
        )


@pytest.mark.anyio
async def test_a_caller_without_integration_manage_cannot_register_a_cluster(
    engine: AsyncEngine,
) -> None:
    """And is told nothing about what exists.

    A 403 here would confirm the endpoint is real and the name is free;
    both are facts a caller with no authority over any cluster should not
    be able to collect.
    """
    await seed_catalog_world(engine)
    harness = await build_users(engine)
    response = await _post(
        harness, "user-plain", {"cluster_ref": "unauthorized-1", "display_name": "Nope"}
    )
    assert response.status_code == 404, response.text

    async with engine.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM clusters WHERE cluster_ref = 'unauthorized-1'")
            )
        ).scalar_one()
    assert count == 0
    del harness


@pytest.mark.anyio
async def test_registering_a_cluster_is_idempotent_and_audited(engine: AsyncEngine) -> None:
    await seed_catalog_world(engine)
    harness = await build_users(engine)
    await make_role(harness, engine, "Cluster Registrar", ["integration.manage"])
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

    payload = {"cluster_ref": "duosis-prod-1", "display_name": "Duosis Production"}
    first = await _post(harness, "user-owner", payload)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True
    assert body["cluster_ref"] == "duosis-prod-1"
    # Nothing credential-shaped travels back.
    rendered = first.text
    for forbidden in ("ghs_", "ghp_", "BEGIN", "password", "token"):
        assert forbidden not in rendered, forbidden

    # The same intent again is already satisfied, so it is not an error.
    second = await _post(harness, "user-owner", payload)
    assert second.status_code == 201, second.text
    assert second.json()["created"] is False
    assert second.json()["id"] == body["id"]

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT count(*) FROM clusters WHERE cluster_ref = 'duosis-prod-1'")
            )
        ).scalar_one()
        assert rows == 1, "a repeat must not create a second cluster"
        audited = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action = 'catalog.cluster.create' AND target_id = :id"
                ),
                {"id": body["id"]},
            )
        ).scalar_one()
    assert audited == 1, "the create is audited exactly once"
    del harness


@pytest.mark.anyio
async def test_the_same_ref_with_a_different_name_is_a_conflict(engine: AsyncEngine) -> None:
    """A rename wearing a create's clothes.

    A cluster ref anchors workload bindings, inventory and agent
    certificates. Quietly accepting a new display name for one would make
    the audit trail describe something that no longer exists.
    """
    await seed_catalog_world(engine)
    harness = await build_users(engine)
    await make_role(harness, engine, "Cluster Registrar", ["integration.manage"])
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

    assert (
        await _post(harness, "user-owner", {"cluster_ref": "dup-1", "display_name": "First"})
    ).status_code == 201
    clash = await _post(harness, "user-owner", {"cluster_ref": "dup-1", "display_name": "Second"})
    assert clash.status_code == 409, clash.text
    del harness


@pytest.mark.anyio
async def test_a_cluster_ref_must_be_a_dns_label(engine: AsyncEngine) -> None:
    """It ends up in manifests, certificate subjects and URLs."""
    await seed_catalog_world(engine)
    harness = await build_users(engine)
    await make_role(harness, engine, "Cluster Registrar", ["integration.manage"])
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")

    for bad in ("Duosis-Prod", "prod cluster", "prod/1", "-leading", "a" * 64):
        response = await _post(harness, "user-owner", {"cluster_ref": bad, "display_name": "X"})
        assert response.status_code == 422, f"{bad}: {response.text}"
    del harness


@pytest.mark.anyio
async def test_the_management_command_uses_the_same_service_and_fails_closed(
    engine: AsyncEngine,
) -> None:
    """A rollout operator holds cluster credentials, not always a Drake
    login — and the first cluster has to exist before any UI can list one.

    So there is a command, and it is deliberately the SAME call: same
    validation, same idempotency, same audit event. What it must not be is
    a way to write things nobody can trace, so an unknown actor is refused.
    """
    from drake_api.catalog.router_clusters import register_cluster

    await seed_catalog_world(engine)
    # A real identity: an audit row pointing at an actor who does not exist
    # records nothing anybody can follow up on, and the command refuses one.
    harness = await build_users(engine)
    async with engine.connect() as connection:
        actor = (await connection.execute(text("SELECT id FROM identities LIMIT 1"))).scalar_one()

    created = await register_cluster(
        engine,
        cluster_ref="managed-1",
        display_name="Managed",
        site="dc1",
        actor_identity_id=uuidlib.UUID(str(actor)),
    )
    assert created["created"] is True

    # Idempotent in exactly the same way as the endpoint.
    again = await register_cluster(
        engine,
        cluster_ref="managed-1",
        display_name="Managed",
        site="dc1",
        actor_identity_id=uuidlib.UUID(str(actor)),
    )
    assert again["created"] is False
    assert again["id"] == created["id"]

    # A rename through the back door is refused too.
    with pytest.raises(ValueError):
        await register_cluster(
            engine,
            cluster_ref="managed-1",
            display_name="Renamed",
            site="dc1",
            actor_identity_id=uuidlib.UUID(str(actor)),
        )

    # And it is not a shell: a malformed ref never reaches the database.
    with pytest.raises(ValueError):
        await register_cluster(
            engine,
            cluster_ref="Not A Label",
            display_name="X",
            site="",
            actor_identity_id=uuidlib.UUID(str(actor)),
        )

    async with engine.connect() as connection:
        audited = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action = 'catalog.cluster.create' AND target_id = :id"
                ),
                {"id": created["id"]},
            )
        ).scalar_one()
    assert audited == 1, "the command writes the same audit event the endpoint does"
    del harness
