"""Transactional idempotency (integration, disposable local stack).

Proves PostgreSQL is the single idempotency authority: replay, conflict,
operation separation, concurrency (single mutation + single success audit),
rollback-with-audit-failure, retry-after-failure, lost-response replay,
actor separation, expiry, and stored-response hygiene.
"""

import asyncio
import uuid as uuidlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from drake_api.db import dispose_engines
from drake_api.rbac.catalog import seed_catalog
from harness_s1 import (
    S1Harness,
    build_harness,
    grant_platform_owner,
    require_it_settings,
    reset_rbac_state,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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
    await reset_rbac_state(eng)
    async with eng.begin() as connection:
        await seed_catalog(connection)
    yield eng
    await eng.dispose()
    await dispose_engines()


async def owner_client_me(
    harness: S1Harness, engine: AsyncEngine, client: httpx.AsyncClient
) -> dict[str, Any]:
    await harness.login(client, "user-owner")
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
    me: dict[str, Any] = (await client.get("/v1/me")).json()
    return me


def headers(me: dict[str, Any], key: str) -> dict[str, str]:
    return {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": key}


async def role_count(engine: AsyncEngine, name: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM roles WHERE name = :name"), {"name": name}
                )
            ).scalar_one()
        )


async def test_sequential_replay_same_payload(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_client_me(harness, engine, client)
        key = f"seq-{uuidlib.uuid4().hex}"
        body = {"name": "Replay Role", "description": "d"}

        first = await client.post("/v1/roles", json=body, headers=headers(me, key))
        assert first.status_code == 201
        second = await client.post("/v1/roles", json=body, headers=headers(me, key))
        assert second.status_code == 201
        assert second.json() == first.json()  # byte-for-byte replay

    assert await role_count(engine, "Replay Role") == 1


async def test_same_key_different_payload_conflicts(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_client_me(harness, engine, client)
        key = f"conflict-{uuidlib.uuid4().hex}"

        first = await client.post(
            "/v1/roles",
            json={"name": "Conflict A", "description": ""},
            headers=headers(me, key),
        )
        assert first.status_code == 201

        other = await client.post(
            "/v1/roles",
            json={"name": "Conflict B", "description": ""},
            headers=headers(me, key),
        )
        assert other.status_code == 409
        assert other.json()["error"]["message"] == "idempotency_conflict"

    assert await role_count(engine, "Conflict B") == 0


async def test_same_key_different_operation_is_independent(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_client_me(harness, engine, client)
        key = f"crossop-{uuidlib.uuid4().hex}"

        created = await client.post(
            "/v1/roles",
            json={"name": "CrossOp Role", "description": ""},
            headers=headers(me, key),
        )
        assert created.status_code == 201
        role_id = created.json()["id"]

        # Same key, DIFFERENT operation: must not replay the create response.
        updated = await client.put(
            f"/v1/roles/{role_id}",
            json={"description": "changed"},
            headers={**headers(me, key), "If-Match": 'W/"role-1"'},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2  # a real update, not a replayed create


async def test_concurrent_same_request_single_mutation(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_client_me(harness, engine, client)
        key = f"conc-{uuidlib.uuid4().hex}"
        body = {"name": "Concurrent Role", "description": ""}

        results = await asyncio.gather(
            client.post("/v1/roles", json=body, headers=headers(me, key)),
            client.post("/v1/roles", json=body, headers=headers(me, key)),
        )
        codes = sorted(response.status_code for response in results)
        assert codes == [201, 201]
        ids = {response.json()["id"] for response in results}
        assert len(ids) == 1  # both callers see the SAME created role

    assert await role_count(engine, "Concurrent Role") == 1
    async with engine.connect() as connection:
        audits = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action = 'rbac.role.create' AND result = 'success' "
                    "AND target_id = :rid"
                ),
                {"rid": next(iter(ids))},
            )
        ).scalar_one()
    assert audits == 1  # single success audit for the single mutation


async def test_concurrent_grant_create_single_grant_single_audit(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as plain_client:
        await harness.login(plain_client, "user-plain")
    async with engine.connect() as connection:
        plain_id = str(
            (
                await connection.execute(
                    text("SELECT id FROM identities WHERE subject = 'user-plain'")
                )
            ).scalar_one()
        )
        root_id = str(
            (
                await connection.execute(
                    text("SELECT id FROM scopes WHERE scope_type = 'organization'")
                )
            ).scalar_one()
        )

    harness2 = build_harness()
    async with harness2.api_client() as client:
        me = await owner_client_me(harness2, engine, client)
        developer = next(
            role["id"]
            for role in (await client.get("/v1/roles")).json()["roles"]
            if role["name"] == "Developer"
        )
        key = f"conc-grant-{uuidlib.uuid4().hex}"
        body = {"role_id": developer, "scope_id": root_id, "identity_id": plain_id}

        results = await asyncio.gather(
            client.post("/v1/grants", json=body, headers=headers(me, key)),
            client.post("/v1/grants", json=body, headers=headers(me, key)),
        )
        assert sorted(r.status_code for r in results) == [201, 201]
        grant_ids = {r.json()["id"] for r in results}
        assert len(grant_ids) == 1

    async with engine.connect() as connection:
        grant_rows = (
            await connection.execute(
                text("SELECT count(*) FROM grants WHERE identity_id = :iid AND role_id = :rid"),
                {"iid": plain_id, "rid": developer},
            )
        ).scalar_one()
        audit_rows = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action = 'rbac.grant.create' AND result = 'success' "
                    "AND target_id = :gid"
                ),
                {"gid": next(iter(grant_ids))},
            )
        ).scalar_one()
    assert int(grant_rows) == 1
    assert int(audit_rows) == 1


async def test_audit_failure_rolls_back_mutation_and_claim(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_client_me(harness, engine, client)
        key = f"rollback-{uuidlib.uuid4().hex}"
        body = {"name": "Rollback Role", "description": ""}

        import drake_api.rbac.service as service_module

        original_validate = service_module.validate_event

        def broken_validate(_event: object) -> dict[str, object]:
            raise RuntimeError("audit backend rejected the event")

        monkeypatch.setattr(service_module, "validate_event", broken_validate)
        failed = await client.post("/v1/roles", json=body, headers=headers(me, key))
        assert failed.status_code == 500
        monkeypatch.setattr(service_module, "validate_event", original_validate)

        # Neither the role nor the idempotency claim survived the rollback:
        assert await role_count(engine, "Rollback Role") == 0
        async with engine.connect() as connection:
            claims = (
                await connection.execute(
                    text("SELECT count(*) FROM idempotency_records WHERE idempotency_key = :k"),
                    {"k": key},
                )
            ).scalar_one()
        assert int(claims) == 0

        # Safe retry with the SAME key performs the mutation for real.
        retried = await client.post("/v1/roles", json=body, headers=headers(me, key))
        assert retried.status_code == 201
    assert await role_count(engine, "Rollback Role") == 1


async def test_lost_response_replays_without_new_mutation(engine: AsyncEngine) -> None:
    """Commit succeeded but the client never saw the response → the retry
    replays the stored response instead of mutating again."""
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_client_me(harness, engine, client)
        key = f"lost-{uuidlib.uuid4().hex}"
        body = {"name": "Lost Response Role", "description": ""}

        first = await client.post("/v1/roles", json=body, headers=headers(me, key))
        assert first.status_code == 201
        # The client "lost" the response; it retries identically:
        retry = await client.post("/v1/roles", json=body, headers=headers(me, key))
        assert retry.status_code == 201
        assert retry.json() == first.json()
    assert await role_count(engine, "Lost Response Role") == 1


async def test_actors_use_same_key_independently(engine: AsyncEngine) -> None:
    harness = build_harness()
    shared_key = f"shared-{uuidlib.uuid4().hex}"

    async with harness.api_client() as owner_a:
        me_a = await owner_client_me(harness, engine, owner_a)
        first = await owner_a.post(
            "/v1/roles",
            json={"name": "Actor A Role", "description": ""},
            headers=headers(me_a, shared_key),
        )
        assert first.status_code == 201

    # Second actor: bootstrap a second owner identity.
    harness.provider.users["user-owner2"] = type(harness.provider.users["user-owner"])(
        "user-owner2", "Owner Two", "owner2@example.test"
    )
    async with harness.api_client() as owner_b:
        await harness.login(owner_b, "user-owner2")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner2")
        me_b = (await owner_b.get("/v1/me")).json()
        second = await owner_b.post(
            "/v1/roles",
            json={"name": "Actor B Role", "description": ""},
            headers=headers(me_b, shared_key),
        )
        # Same key, different actor: fully independent (no replay, no conflict).
        assert second.status_code == 201

    assert await role_count(engine, "Actor A Role") == 1
    assert await role_count(engine, "Actor B Role") == 1


async def test_expired_record_is_reclaimed(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_client_me(harness, engine, client)
        key = f"expire-{uuidlib.uuid4().hex}"

        first = await client.post(
            "/v1/roles",
            json={"name": "Expiry Role A", "description": ""},
            headers=headers(me, key),
        )
        assert first.status_code == 201

        # Force-expire the record, then reuse the key with a DIFFERENT payload:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE idempotency_records SET expires_at = now() - interval '1 minute' "
                    "WHERE idempotency_key = :k"
                ),
                {"k": key},
            )
        second = await client.post(
            "/v1/roles",
            json={"name": "Expiry Role B", "description": ""},
            headers=headers(me, key),
        )
        assert second.status_code == 201  # reclaimed, not a conflict

    assert await role_count(engine, "Expiry Role B") == 1


async def test_stored_responses_are_credential_free(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_client_me(harness, engine, client)
        await client.post(
            "/v1/roles",
            json={"name": "Hygiene Role", "description": ""},
            headers=headers(me, f"hyg-{uuidlib.uuid4().hex}"),
        )

    async with engine.connect() as connection:
        rows = (
            await connection.execute(text("SELECT response_body::text FROM idempotency_records"))
        ).all()
    from drake_api.logging import redact

    for (body_text,) in rows:
        if body_text is None:
            continue
        assert redact(body_text) == body_text  # no credential shapes stored
        for forbidden in ("csrf", "cookie", "session", "token", "authorization"):
            assert forbidden not in body_text.lower()
