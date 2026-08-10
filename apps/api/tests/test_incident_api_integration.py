"""Incident endpoints: scope isolation, bounded filters, safe acknowledge.

The processor decides what an incident is; these tests are about who may
see one, what a response is allowed to contain, and what happens when two
people press acknowledge at the same time.
"""

import uuid as uuidlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from drake_api.incidents.processor import process_evaluation
from harness_s1 import S1Harness, build_harness, grant_platform_owner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, make_role, seed_catalog_world
from test_incident_processor_integration import evaluation, make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

BASE = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)


def incident_harness() -> S1Harness:
    """A harness whose fake identity provider knows the shared fixtures' users.

    `build_harness` ships two subjects; the catalog fixtures grant roles to
    several more, so they have to exist before anyone can log in as one.
    """
    harness = build_harness()
    user_type = type(harness.provider.users["user-owner"])
    for subject in ("user-env", "user-b-only", "user-cluster"):
        harness.provider.users.setdefault(
            subject,
            user_type(subject, subject.replace("user-", "").title(), f"{subject}@example.test"),
        )
    return harness


@asynccontextmanager
async def owner(harness: S1Harness, engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        yield client


async def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    me = (await client.get("/v1/me")).json()
    return {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": f"inc-{uuidlib.uuid4().hex}"}


async def open_one(engine: AsyncEngine) -> dict[str, Any]:
    """A world with one open incident, built through the real processor."""
    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))
    await process_evaluation(engine, evaluation(world, computed_at=BASE + timedelta(minutes=1)))
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT id, version FROM incidents WHERE binding_id = :b"),
                {"b": world["binding_id"]},
            )
        ).first()
    assert row is not None
    return {**world, "incident_id": row[0], "version": row[1]}


async def grant_scope(
    engine: AsyncEngine, harness: S1Harness, subject: str, role: str, scope_id: uuidlib.UUID
) -> None:
    """Grant a role on a scope id directly (the fixtures' scopes are ad hoc)."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO grants (identity_id, role_id, scope_id)
                SELECT i.id, r.id, :scope
                FROM identities i, roles r
                WHERE i.issuer = :issuer AND i.subject = :subject AND r.name = :role
                """
            ),
            {
                "issuer": harness.provider.issuer,
                "subject": subject,
                "role": role,
                "scope": scope_id,
            },
        )


# --- list ----------------------------------------------------------------


async def test_the_list_is_filtered_to_the_callers_scope(engine: AsyncEngine) -> None:
    """An incident outside your scope is not a shorter list — it is absent."""
    world = await open_one(engine)
    await seed_catalog_world(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        body = (await client.get("/v1/incidents")).json()
    assert [item["id"] for item in body["items"]] == [str(world["incident_id"])]

    # A user granted only on an unrelated project sees nothing, and the
    # total says nothing either.
    await make_role(harness, engine, "Env Reader INC", ["environment.view"])
    async with harness.api_client() as outsider:
        await harness.login(outsider, "user-b-only")
        await grant(engine, harness, "user-b-only", "Env Reader INC", "project", "beta")
        hidden = (await outsider.get("/v1/incidents")).json()
    assert hidden["items"] == []
    assert hidden["total"] == 0


async def test_filters_are_an_allowlist(engine: AsyncEngine) -> None:
    world = await open_one(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        assert (await client.get("/v1/incidents?state=open")).json()["total"] == 1
        assert (await client.get("/v1/incidents?state=resolved")).json()["total"] == 0
        assert (await client.get("/v1/incidents?opened_within=30d")).json()["total"] == 1
        scoped = await client.get(
            f"/v1/incidents?environment_id={world['environment_id']}&severity=critical"
        )
        assert scoped.json()["total"] == 1

        # Anything outside the vocabulary is refused rather than ignored: a
        # silently dropped filter returns MORE than the caller asked for.
        for query in ("state=deleted", "severity=warning", "opened_within=99y"):
            assert (await client.get(f"/v1/incidents?{query}")).status_code == 422, query
        assert (await client.get("/v1/incidents?cursor=not-a-cursor")).status_code == 422


async def test_pagination_is_bounded_and_keyset_based(engine: AsyncEngine) -> None:
    for _ in range(3):
        await open_one(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        first = (await client.get("/v1/incidents?limit=2")).json()
        assert len(first["items"]) == 2
        assert first["next_cursor"]
        second = (await client.get(f"/v1/incidents?limit=2&cursor={first['next_cursor']}")).json()

    seen = [item["id"] for item in first["items"]] + [item["id"] for item in second["items"]]
    assert len(seen) == len(set(seen)) == 3
    assert second["next_cursor"] is None


# --- detail and timeline --------------------------------------------------


async def test_detail_carries_the_decision_and_none_of_the_query(
    engine: AsyncEngine,
) -> None:
    world = await open_one(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        response = await client.get(f"/v1/incidents/{world['incident_id']}")
        assert response.status_code == 200, response.text
        body = response.json()
        events = (await client.get(f"/v1/incidents/{world['incident_id']}/events")).json()

    assert body["state"] == "open"
    assert body["severity"] == "critical"
    assert body["primary_reason"] == "no_ready_replicas"
    assert body["opening_reasons"] == ["no_ready_replicas"]
    assert body["binding"]["workload_name"] == "pilot-api"
    assert body["current_health"]["status"] == "critical"
    assert body["acknowledged_by"] is None
    assert body["can_acknowledge"] is True

    assert [event["event_type"] for event in events["events"]] == ["opened"]

    serialized = response.text + str(events)
    for forbidden in ("sum(rate(", "kube_workload", "promql", "config_ref", "password"):
        assert forbidden not in serialized.lower(), forbidden


async def test_detail_and_events_are_uniformly_not_found_out_of_scope(
    engine: AsyncEngine,
) -> None:
    world = await open_one(engine)
    await seed_catalog_world(engine)
    harness = incident_harness()
    await make_role(harness, engine, "Env Reader INC2", ["environment.view"])
    absent = uuidlib.uuid4()

    async with harness.api_client() as outsider:
        await harness.login(outsider, "user-b-only")
        await grant(engine, harness, "user-b-only", "Env Reader INC2", "project", "beta")
        for path in ("/v1/incidents/{}", "/v1/incidents/{}/events"):
            hidden = await outsider.get(path.format(world["incident_id"]))
            missing = await outsider.get(path.format(absent))
            assert hidden.status_code == 404, path
            assert missing.status_code == 404, path
            assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


# --- acknowledge ----------------------------------------------------------


async def test_acknowledge_requires_the_ack_permission_not_just_read(
    engine: AsyncEngine,
) -> None:
    """Read authority is not write authority."""
    world = await open_one(engine)
    harness = incident_harness()
    await make_role(harness, engine, "Reader Only INC", ["environment.view"])

    async with harness.api_client() as reader:
        await harness.login(reader, "user-plain")
        await grant_scope(engine, harness, "user-plain", "Reader Only INC", world["service_scope"])
        detail = (await reader.get(f"/v1/incidents/{world['incident_id']}")).json()
        assert detail["can_acknowledge"] is False

        refused = await reader.post(
            f"/v1/incidents/{world['incident_id']}/acknowledge",
            json={"expected_version": world["version"]},
            headers=await csrf(reader),
        )
    # 404, not 403: a caller who may not act on it learns nothing about it.
    assert refused.status_code == 404


async def test_acknowledge_without_csrf_is_refused(engine: AsyncEngine) -> None:
    world = await open_one(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        response = await client.post(
            f"/v1/incidents/{world['incident_id']}/acknowledge",
            json={"expected_version": world["version"]},
        )
    assert response.status_code in (401, 403)


async def test_acknowledge_succeeds_and_is_idempotent_on_retry(engine: AsyncEngine) -> None:
    world = await open_one(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        first = await client.post(
            f"/v1/incidents/{world['incident_id']}/acknowledge",
            json={"expected_version": world["version"]},
            headers=await csrf(client),
        )
        assert first.status_code == 200, first.text
        assert first.json()["state"] == "acknowledged"
        assert first.json()["changed"] is True

        # The same call again — a client that lost the response.
        retry = await client.post(
            f"/v1/incidents/{world['incident_id']}/acknowledge",
            json={"expected_version": world["version"]},
            headers=await csrf(client),
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["changed"] is False
        assert retry.json()["version"] == first.json()["version"]

        events = (await client.get(f"/v1/incidents/{world['incident_id']}/events")).json()
    assert [event["event_type"] for event in events["events"]] == ["opened", "acknowledged"]

    # One acknowledgement, one audit row.
    async with engine.connect() as connection:
        count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE action = 'incident.acknowledge' "
                    "AND target_id = :id"
                ),
                {"id": str(world["incident_id"])},
            )
        ).scalar_one()
    assert count == 1


async def test_a_stale_version_conflicts_instead_of_overwriting(engine: AsyncEngine) -> None:
    world = await open_one(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        response = await client.post(
            f"/v1/incidents/{world['incident_id']}/acknowledge",
            json={"expected_version": world["version"] + 7},
            headers=await csrf(client),
        )
    assert response.status_code == 409


async def test_acknowledge_accepts_no_actor_or_free_text(engine: AsyncEngine) -> None:
    """The actor comes from the session; a client that could name one
    could name someone else."""
    world = await open_one(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        rejected = await client.post(
            f"/v1/incidents/{world['incident_id']}/acknowledge",
            json={
                "expected_version": world["version"],
                "actor_id": str(uuidlib.uuid4()),
                "note": "on it",
            },
            headers=await csrf(client),
        )
    assert rejected.status_code == 422


# --- service-health integration -------------------------------------------


async def test_service_detail_endpoints_expose_incidents_and_transitions(
    engine: AsyncEngine,
) -> None:
    world = await open_one(engine)
    harness = incident_harness()

    async with owner(harness, engine) as client:
        incidents = (
            await client.get(f"/v1/service-health/bindings/{world['binding_id']}/incidents")
        ).json()
        transitions = (
            await client.get(f"/v1/service-health/bindings/{world['binding_id']}/transitions")
        ).json()

    assert [item["id"] for item in incidents["items"]] == [str(world["incident_id"])]
    assert transitions["transitions"][0]["new_status"] == "critical"
    assert transitions["transitions"][0]["previous_status"] is None
    # History is a record of decisions, not a copy of the metrics.
    assert set(transitions["transitions"][0]) == {
        "previous_status",
        "new_status",
        "reasons",
        "computed_at",
        "recorded_at",
        "binding_revision",
    }


async def test_reading_incidents_never_creates_one(engine: AsyncEngine) -> None:
    """GETs are reads. Otherwise the incident history would depend on who
    opened which page."""
    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))
    harness = incident_harness()

    async with owner(harness, engine) as client:
        for _ in range(3):
            await client.get("/v1/incidents")
            await client.get(f"/v1/service-health/bindings/{world['binding_id']}/incidents")
            await client.get(f"/v1/service-health/bindings/{world['binding_id']}/transitions")

    async with engine.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM incidents WHERE binding_id = :b"),
                {"b": world["binding_id"]},
            )
        ).scalar_one()
    assert count == 0
