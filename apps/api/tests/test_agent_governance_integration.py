"""Governance of agents against the real database (Agent Visibility,
phase 5): shadow AI, approval, ownership, purpose and retirement."""

import uuid
from typing import Any

import pytest
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_agents_integration import agents_by_name, redis, seed
from test_catalog_api_integration import grant, login_all, make_role
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db", "redis"]


async def csrf(client: Any) -> dict[str, str]:
    me = (await client.get("/v1/me")).json()
    return {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": f"gv-{uuid.uuid4().hex}"}


async def test_agents_are_reviewed_owned_and_retired(engine: AsyncEngine, redis: Any) -> None:
    seeded = await seed(engine, redis)
    agents = await agents_by_name(engine)
    triager, batch = agents["ticket-triager"][5], agents["batch"][5]
    harness = build_harness(require_it_settings())
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-nobody", user_type("user-nobody", "Nobody", "user-nobody@example.test")
    )
    await login_all(harness, ["user-nobody"])
    await make_role(harness, engine, "Agent viewer", ["agent.view", "project.view"])
    async with engine.connect() as connection:
        scope_ref = (
            await connection.execute(
                text(
                    "SELECT s.external_ref FROM projects p JOIN scopes s ON s.id = p.scope_id "
                    "WHERE p.id = :p"
                ),
                {"p": seeded["world"]["project_id"]},
            )
        ).scalar_one()
    await grant(engine, harness, "user-nobody", "Agent viewer", "project", scope_ref)

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        body = (await client.get("/v1/agents")).json()
        by_name = {a["display_name"]: a for a in body["items"]}
        # In the catalog and named: to review, not shadow. Outside the catalog: shadow.
        assert (by_name["ticket-triager"]["lifecycle"], by_name["ticket-triager"]["shadow"]) == (
            "observed",
            False,
        )
        assert by_name["batch"]["shadow"] is True and by_name["research-helper"]["shadow"] is True
        assert body["summary"]["governance"]["review"] == 3
        assert body["summary"]["governance"]["shadow"] == 2
        assert body["summary"]["can_manage"] is True and by_name["batch"]["can_manage"] is True

        approved = await client.post(
            f"/v1/agents/{triager}/governance",
            json={"lifecycle": "approved", "owner": "me", "purpose": "  Sorts   support tickets "},
            headers=await csrf(client),
        )
        assert approved.status_code == 200
        agent = approved.json()
        assert agent["lifecycle"] == "approved"
        assert agent["purpose"] == "Sorts support tickets"
        assert agent["owner"]["name"] and agent["lifecycle_changed_by"] == agent["owner"]["name"]

        retired = await client.post(
            f"/v1/agents/{batch}/governance",
            json={"lifecycle": "retired"},
            headers=await csrf(client),
        )
        assert retired.status_code == 200
        active = (await client.get("/v1/agents")).json()
        assert "batch" not in {a["display_name"] for a in active["items"]}
        everything = (await client.get("/v1/agents?lifecycle=all")).json()
        gone = next(a for a in everything["items"] if a["display_name"] == "batch")
        assert (gone["lifecycle"], gone["seen_after_retirement"]) == ("retired", False)
        assert everything["summary"]["governance"]["approved"] == 1

        bad = await client.post(
            f"/v1/agents/{batch}/governance",
            json={"lifecycle": "deleted"},
            headers=await csrf(client),
        )
        assert bad.status_code == 422
        long = await client.post(
            f"/v1/agents/{batch}/governance",
            json={"purpose": "x" * 501},
            headers=await csrf(client),
        )
        assert long.status_code == 422

    # A retired agent that shows up again is flagged, not silently revived.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE agents SET last_seen_at = now() + interval '2 hours' WHERE id = :id"),
            {"id": batch},
        )
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        everything = (await client.get("/v1/agents?lifecycle=all")).json()
        gone = next(a for a in everything["items"] if a["display_name"] == "batch")
        assert gone["seen_after_retirement"] is True
        assert everything["summary"]["governance"]["seen_after_retirement"] == 1

    async with harness.api_client() as client:
        await harness.login(client, "user-nobody")
        body = (await client.get("/v1/agents")).json()
        assert body["summary"]["can_manage"] is False
        assert all(a["can_manage"] is False for a in body["items"])
        refused = await client.post(
            f"/v1/agents/{triager}/governance",
            json={"lifecycle": "retired"},
            headers=await csrf(client),
        )
        assert refused.status_code == 404

    async with engine.connect() as connection:
        audited = (
            await connection.execute(
                text("SELECT count(*) FROM audit_events WHERE action = 'agent.governance.set'")
            )
        ).scalar_one()
    assert audited >= 2
