"""Guardrails on agents against the real database (Agent Visibility,
phase 5): who may set them, how they are judged, and the incident a
critical one opens and closes."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from drake_api.ai_agents import guardrails
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_agents_integration import NOW, agents_by_name, redis, seed
from test_catalog_api_integration import grant, login_all, make_role
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db", "redis"]


async def csrf(client: Any) -> dict[str, str]:
    me = (await client.get("/v1/me")).json()
    return {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": f"g-{uuid.uuid4().hex}"}


async def set_usage(engine: AsyncEngine, agent_id: Any, *, calls: int, errors: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE agent_rollups SET inference_calls = :calls, invocations = 0, "
                "tool_calls = 0, errors = :errors WHERE agent_id = :id"
            ),
            {"id": agent_id, "calls": calls, "errors": errors},
        )


async def test_a_critical_guardrail_pages_and_recovers(engine: AsyncEngine, redis: Any) -> None:
    seeded = await seed(engine, redis)
    agents = await agents_by_name(engine)
    triager, helper = agents["ticket-triager"][5], agents["research-helper"][5]
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
        body = (await client.get(f"/v1/agents/{triager}/guardrails")).json()
        assert body["can_manage"] is True and body["pages"] is True
        assert [i["kind"] for i in body["items"]] == list(guardrails.KINDS)
        assert not any(i["set"] for i in body["items"])

        url = f"/v1/agents/{triager}/guardrails"
        saved = await client.post(
            f"{url}/failure_ratio",
            json={"threshold": "0.05", "severity": "critical"},
            headers=await csrf(client),
        )
        assert saved.status_code == 200
        ratio = next(i for i in saved.json()["items"] if i["kind"] == "failure_ratio")
        assert (ratio["set"], ratio["threshold"], ratio["state"]) == (True, 0.05, "pending")
        bad = await client.post(
            f"{url}/failure_ratio",
            json={"threshold": "2", "severity": "critical"},
            headers=await csrf(client),
        )
        assert bad.status_code == 422
        unknown = await client.post(
            f"{url}/vibes",
            json={"threshold": "1", "severity": "warning"},
            headers=await csrf(client),
        )
        assert unknown.status_code == 404
        # An agent outside the catalog: set by an organization-wide owner, never pages.
        outside = await client.post(
            f"/v1/agents/{helper}/guardrails/silence_hours",
            json={"threshold": "1", "severity": "critical"},
            headers=await csrf(client),
        )
        assert outside.status_code == 200 and outside.json()["pages"] is False

    # 10 of 30 calls failed: over 5%.
    await set_usage(engine, triager, calls=30, errors=10)
    report = await guardrails.evaluate(engine, now=NOW)
    assert report.fired >= 1
    async with engine.connect() as connection:
        incident = (
            await connection.execute(
                text(
                    "SELECT id, state, source, project_id, title FROM incidents "
                    "WHERE source = 'agent' AND correlation_key LIKE 'agent-guardrail:%'"
                )
            )
        ).all()
    assert len(incident) == 1  # the outside agent's silence fires, but pages nobody
    assert incident[0][1] == "open" and "ticket-triager" in incident[0][4]
    assert "33.3% failed" in incident[0][4]

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        listed = (await client.get("/v1/agents")).json()
        assert (
            next(a for a in listed["items"] if a["display_name"] == "ticket-triager")[
                "guardrails_firing"
            ]
            == 1
        )
        state = (await client.get(f"/v1/agents/{triager}/guardrails")).json()
        ratio = next(i for i in state["items"] if i["kind"] == "failure_ratio")
        assert ratio["state"] == "firing" and ratio["value"] == pytest.approx(1 / 3)
        assert ratio["incident_id"] == str(incident[0][0])

    # Fixed: the guardrail recovers and so does the incident.
    await set_usage(engine, triager, calls=30, errors=0)
    again = await guardrails.evaluate(engine, now=NOW + timedelta(minutes=5))
    assert again.resolved >= 1
    async with engine.connect() as connection:
        closed = (
            await connection.execute(
                text("SELECT state, resolution_source FROM incidents WHERE id = :id"),
                {"id": incident[0][0]},
            )
        ).one()
        events = [
            r[0]
            for r in (
                await connection.execute(
                    text(
                        "SELECT e.event FROM agent_guardrail_events e JOIN agent_guardrails g "
                        "ON g.id = e.guardrail_id WHERE g.agent_id = :id ORDER BY e.id"
                    ),
                    {"id": triager},
                )
            ).all()
        ]
    assert tuple(closed) == ("resolved", "agent_recovered")
    assert events == ["fired", "resolved"]

    # Seeing an agent is not permission to set what pages people.
    async with harness.api_client() as client:
        await harness.login(client, "user-nobody")
        body = (await client.get(f"/v1/agents/{triager}/guardrails")).json()
        assert body["can_manage"] is False
        refused = await client.post(
            f"/v1/agents/{triager}/guardrails/cost_day",
            json={"threshold": "50", "severity": "critical"},
            headers=await csrf(client),
        )
        assert refused.status_code == 404
        assert (await client.get(f"/v1/agents/{helper}/guardrails")).status_code == 404

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        removed = await client.delete(
            f"/v1/agents/{triager}/guardrails/failure_ratio", headers=await csrf(client)
        )
        assert removed.status_code == 200
        assert not next(i for i in removed.json()["items"] if i["kind"] == "failure_ratio")["set"]


async def test_too_few_calls_is_no_data_not_a_verdict(engine: AsyncEngine, redis: Any) -> None:
    await seed(engine, redis)
    triager = (await agents_by_name(engine))["ticket-triager"][5]
    await set_usage(engine, triager, calls=5, errors=5)
    values = None
    async with engine.connect() as connection:
        values = await guardrails.measure(connection, [triager], NOW)
    assert values[triager]["failure_ratio"] is None
    assert guardrails.judge("failure_ratio", None, 0.05) == "no_data"
    assert values[triager]["silence_hours"] is not None
