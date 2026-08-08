"""Inbox, policy management and delivery audit over HTTP.

The questions here are all about who: whose inbox is this, who may edit a
policy, who may attach whom as a recipient, and what a response is allowed
to contain about any of them.
"""

import uuid as uuidlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from drake_api.notifications.planner import plan_pending
from drake_api.settings import WebhookDestination
from harness_s1 import S1Harness, build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_notification_planner_integration import (
    WEBHOOK_KEY,
    attach,
    make_destination,
    make_policy,
    make_recipient,
    open_incident_world,
)
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

RECEIVER_URL = "https://receiver.invalid/hooks/drake"


def notification_harness() -> S1Harness:
    """A harness with one webhook registered in the operator's registry."""
    settings = require_it_settings().model_copy(
        update={
            "notification_webhooks": {
                WEBHOOK_KEY: WebhookDestination(url=RECEIVER_URL, display_name="Ops primary")
            }
        }
    )
    harness = build_harness(settings)
    user_type = type(harness.provider.users["user-owner"])
    for subject in ("user-env", "user-b-only", "user-cluster"):
        harness.provider.users.setdefault(
            subject,
            user_type(subject, subject.replace("user-", "").title(), f"{subject}@example.test"),
        )
    return harness


@asynccontextmanager
async def signed_in(
    harness: S1Harness, engine: AsyncEngine, subject: str = "user-owner", owner: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    async with harness.api_client() as client:
        await harness.login(client, subject)
        if owner:
            await grant_platform_owner(engine, harness.provider.issuer, subject)
        yield client


async def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    me = (await client.get("/v1/me")).json()
    return {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": f"nt-{uuidlib.uuid4().hex}"}


async def identity_for(engine: AsyncEngine, harness: S1Harness, subject: str) -> uuidlib.UUID:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text("SELECT id FROM identities WHERE issuer = :i AND subject = :s"),
                {"i": harness.provider.issuer, "s": subject},
            )
        ).scalar_one()


async def inbox_world(engine: AsyncEngine, harness: S1Harness, subject: str) -> dict[str, Any]:
    """One delivered in-app notification for the signed-in user."""
    world = await open_incident_world(engine)
    recipient = await identity_for(engine, harness, subject)
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))
    await plan_pending(engine)
    return {**world, "recipient": recipient}


# --- inbox ----------------------------------------------------------------


async def test_the_inbox_returns_only_the_callers_own_notifications(
    engine: AsyncEngine,
) -> None:
    """There is no recipient parameter, so there is nothing to tamper with."""
    harness = notification_harness()
    async with signed_in(harness, engine) as owner:
        world = await inbox_world(engine, harness, "user-owner")
        body = (await owner.get("/v1/notifications")).json()
        assert len(body["items"]) == 1
        assert body["items"][0]["title"].startswith("Incident opened")
        assert body["items"][0]["target_path"] == f"/incidents/{world['incident_id']}"
        assert (await owner.get("/v1/notifications/unread-count")).json()["unread"] == 1

    # A second user with full authority still sees an empty inbox: these
    # notifications are not theirs.
    async with signed_in(harness, engine, "user-plain") as other:
        assert (await other.get("/v1/notifications")).json()["items"] == []
        assert (await other.get("/v1/notifications/unread-count")).json()["unread"] == 0


async def test_unread_filter_and_bounded_pagination(engine: AsyncEngine) -> None:
    harness = notification_harness()
    async with signed_in(harness, engine) as client:
        recipient = await identity_for(engine, harness, "user-owner")
        for _ in range(3):
            world = await open_incident_world(engine)
            policy = await make_policy(engine, world)
            await attach(
                engine, policy, await make_destination(engine, world, identity_id=recipient)
            )
        await plan_pending(engine)

        first = (await client.get("/v1/notifications?limit=2")).json()
        assert len(first["items"]) == 2
        assert first["next_cursor"]
        second = (
            await client.get(f"/v1/notifications?limit=2&cursor={first['next_cursor']}")
        ).json()
        seen = [item["id"] for item in first["items"] + second["items"]]
        assert len(seen) == len(set(seen)) == 3

        # Mark one read, then the unread filter must exclude it.
        marked = await client.post(
            "/v1/notifications/read",
            json={"notification_ids": [first["items"][0]["id"]]},
            headers=await csrf(client),
        )
        assert marked.status_code == 200
        assert marked.json()["marked_read"] == 1
        unread = (await client.get("/v1/notifications?unread_only=true")).json()
        assert len(unread["items"]) == 2
        assert (await client.get("/v1/notifications/unread-count")).json()["unread"] == 2

        assert (await client.get("/v1/notifications?window=99y")).status_code == 422
        assert (await client.get("/v1/notifications?cursor=nope")).status_code == 422


async def test_mark_read_is_idempotent_and_scoped_to_the_caller(
    engine: AsyncEngine,
) -> None:
    harness = notification_harness()
    async with signed_in(harness, engine) as owner:
        await inbox_world(engine, harness, "user-owner")
        item = (await owner.get("/v1/notifications")).json()["items"][0]

        first = await owner.post(
            "/v1/notifications/read",
            json={"notification_ids": [item["id"]]},
            headers=await csrf(owner),
        )
        repeat = await owner.post(
            "/v1/notifications/read",
            json={"notification_ids": [item["id"]]},
            headers=await csrf(owner),
        )
    assert first.json()["marked_read"] == 1
    # A safe retry changes nothing and says so.
    assert repeat.json()["marked_read"] == 0

    async with signed_in(harness, engine, "user-plain") as other:
        # Someone else's id is refused exactly like an unknown one, so the
        # response cannot be used to probe for either.
        stolen = await other.post(
            "/v1/notifications/read",
            json={"notification_ids": [item["id"]]},
            headers=await csrf(other),
        )
        unknown = await other.post(
            "/v1/notifications/read",
            json={"notification_ids": [str(uuidlib.uuid4())]},
            headers=await csrf(other),
        )
    assert stolen.status_code == unknown.status_code == 404
    assert stolen.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_mark_read_requires_csrf(engine: AsyncEngine) -> None:
    harness = notification_harness()
    async with signed_in(harness, engine) as client:
        await inbox_world(engine, harness, "user-owner")
        item = (await client.get("/v1/notifications")).json()["items"][0]
        response = await client.post(
            "/v1/notifications/read", json={"notification_ids": [item["id"]]}
        )
    assert response.status_code in (401, 403)


async def test_a_revoked_scope_removes_the_notification_from_the_api(
    engine: AsyncEngine,
) -> None:
    """Gone from the list, the count and mark-read — not redacted.

    A redacted placeholder still answers "an incident exists here you may
    not see", which is the enumeration the scope filter exists to prevent.
    The row survives in the database; only its visibility ends.
    """
    harness = notification_harness()
    async with signed_in(harness, engine) as client:
        world = await inbox_world(engine, harness, "user-owner")
        visible = (await client.get("/v1/notifications")).json()["items"][0]
        assert visible["title"].startswith("Incident opened")
        assert (await client.get("/v1/notifications/unread-count")).json()["unread"] == 1

        # Revoke every grant the reader holds.
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE grants SET revoked_at = now() WHERE identity_id = :id"),
                {"id": world["recipient"]},
            )

        listed = (await client.get("/v1/notifications")).json()
        count = (await client.get("/v1/notifications/unread-count")).json()
        refused = await client.post(
            "/v1/notifications/read",
            json={"notification_ids": [visible["id"]]},
            headers=await csrf(client),
        )

    assert listed["items"] == []
    assert count["unread"] == 0
    assert refused.status_code == 404
    # Nothing about the service survived anywhere in the responses.
    assert "pilot-api" not in (str(listed) + str(count) + refused.text)


# --- policies -------------------------------------------------------------


async def test_policy_management_needs_manage_not_just_read(
    engine: AsyncEngine,
) -> None:
    """Read authority is not write authority."""
    from test_catalog_api_integration import grant, make_role

    harness = notification_harness()
    world = await open_incident_world(engine)
    await make_role(harness, engine, "Notify Viewer", ["notification.view", "environment.view"])

    async with harness.api_client() as reader:
        await harness.login(reader, "user-plain")
        async with engine.begin() as connection:
            project_scope = (
                await connection.execute(
                    text("SELECT scope_id FROM projects WHERE id = :p"),
                    {"p": world["project_id"]},
                )
            ).scalar_one()
            await connection.execute(
                text(
                    """
                    INSERT INTO grants (identity_id, role_id, scope_id)
                    SELECT i.id, r.id, :scope FROM identities i, roles r
                    WHERE i.issuer = :issuer AND i.subject = 'user-plain'
                      AND r.name = 'Notify Viewer'
                    """
                ),
                {"scope": project_scope, "issuer": harness.provider.issuer},
            )

        listed = await reader.get("/v1/notification-policies")
        assert listed.status_code == 200

        refused = await reader.post(
            "/v1/notification-policies",
            json={
                "display_name": "Should not be created",
                "project_id": str(world["project_id"]),
                "event_types": ["opened"],
            },
            headers=await csrf(reader),
        )
    assert refused.status_code == 404
    del grant


async def test_creating_and_updating_a_policy_with_version_control(
    engine: AsyncEngine,
) -> None:
    harness = notification_harness()
    world = await open_incident_world(engine)

    async with signed_in(harness, engine) as client:
        created = await client.post(
            "/v1/notification-policies",
            json={
                "display_name": "Critical incidents",
                "project_id": str(world["project_id"]),
                "environment_id": str(world["environment_id"]),
                "event_types": ["opened", "auto_resolved"],
            },
            headers=await csrf(client),
        )
        assert created.status_code == 201, created.text
        policy_id = created.json()["id"]
        version = created.json()["version"]

        updated = await client.post(
            f"/v1/notification-policies/{policy_id}",
            json={
                "display_name": "Critical incidents",
                "environment_id": str(world["environment_id"]),
                "event_types": ["opened"],
                "enabled": False,
                "expected_version": version,
            },
            headers=await csrf(client),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == version + 1

        stale = await client.post(
            f"/v1/notification-policies/{policy_id}",
            json={
                "display_name": "Critical incidents",
                "event_types": ["opened"],
                "expected_version": version,
            },
            headers=await csrf(client),
        )
        assert stale.status_code == 409

        detail = (await client.get(f"/v1/notification-policies/{policy_id}")).json()
    assert detail["enabled"] is False
    assert detail["event_types"] == ["opened"]


async def test_a_policy_cannot_be_narrowed_to_another_projects_catalog(
    engine: AsyncEngine,
) -> None:
    """Otherwise a rule scoped to one project becomes a bridge to another."""
    harness = notification_harness()
    world = await open_incident_world(engine)
    other = await open_incident_world(engine)

    async with signed_in(harness, engine) as client:
        response = await client.post(
            "/v1/notification-policies",
            json={
                "display_name": "Cross project",
                "project_id": str(world["project_id"]),
                "environment_id": str(other["environment_id"]),
                "event_types": ["opened"],
            },
            headers=await csrf(client),
        )
    assert response.status_code == 404


async def test_unsupported_event_types_are_refused(engine: AsyncEngine) -> None:
    harness = notification_harness()
    world = await open_incident_world(engine)

    async with signed_in(harness, engine) as client:
        for event_types in (["recovery_started"], ["opened", "not-an-event"], []):
            response = await client.post(
                "/v1/notification-policies",
                json={
                    "display_name": "Bad events",
                    "project_id": str(world["project_id"]),
                    "event_types": event_types,
                },
                headers=await csrf(client),
            )
            assert response.status_code == 422, event_types


# --- destinations ----------------------------------------------------------


async def test_a_recipient_must_be_able_to_see_the_project(engine: AsyncEngine) -> None:
    """A notification is a side channel; this is where it is closed."""
    harness = notification_harness()
    world = await open_incident_world(engine)
    outsider = await make_recipient(engine, world["service_scope"], grant=False)

    async with signed_in(harness, engine) as client:
        refused = await client.post(
            "/v1/notification-destinations",
            json={
                "project_id": str(world["project_id"]),
                "destination_type": "in_app_user",
                "display_name": "Outsider",
                "identity_id": str(outsider),
            },
            headers=await csrf(client),
        )
        assert refused.status_code == 422
        assert "cannot see" in refused.json()["error"]["message"]

        insider = await make_recipient(engine, world["service_scope"])
        allowed = await client.post(
            "/v1/notification-destinations",
            json={
                "project_id": str(world["project_id"]),
                "destination_type": "in_app_user",
                "display_name": "Insider",
                "identity_id": str(insider),
            },
            headers=await csrf(client),
        )
    assert allowed.status_code == 201, allowed.text


async def test_a_webhook_destination_must_name_a_registered_key(
    engine: AsyncEngine,
) -> None:
    """The operator's registry is the allowlist — there is no other way in."""
    harness = notification_harness()
    world = await open_incident_world(engine)

    async with signed_in(harness, engine) as client:
        unknown = await client.post(
            "/v1/notification-destinations",
            json={
                "project_id": str(world["project_id"]),
                "destination_type": "webhook",
                "display_name": "Rogue",
                "destination_key": "not-registered",
            },
            headers=await csrf(client),
        )
        assert unknown.status_code == 422

        # And there is no field in which a URL could arrive instead.
        smuggled = await client.post(
            "/v1/notification-destinations",
            json={
                "project_id": str(world["project_id"]),
                "destination_type": "webhook",
                "display_name": "Rogue",
                "destination_key": WEBHOOK_KEY,
                "url": "https://attacker.invalid/collect",
            },
            headers=await csrf(client),
        )
        assert smuggled.status_code == 422

        created = await client.post(
            "/v1/notification-destinations",
            json={
                "project_id": str(world["project_id"]),
                "destination_type": "webhook",
                "display_name": "Ops primary",
                "destination_key": WEBHOOK_KEY,
            },
            headers=await csrf(client),
        )
        assert created.status_code == 201, created.text

        options = (await client.get("/v1/notification-policies/options")).json()
        listed = (await client.get("/v1/notification-destinations")).json()

    # The registry is offered as key + display name, never as a target.
    assert [entry["key"] for entry in options["webhook_keys"]] == [WEBHOOK_KEY]
    serialized = str(options) + str(listed)
    assert RECEIVER_URL not in serialized
    assert "receiver.invalid" not in serialized
    assert "url" not in str(options["webhook_keys"])


async def test_policies_and_deliveries_are_scope_filtered(engine: AsyncEngine) -> None:
    from test_catalog_api_integration import grant, make_role, seed_catalog_world

    harness = notification_harness()
    world = await open_incident_world(engine)
    await seed_catalog_world(engine)
    policy = await make_policy(engine, world)
    await attach(
        engine,
        policy,
        await make_destination(engine, world, destination_type="webhook", key=WEBHOOK_KEY),
    )
    await plan_pending(engine)
    await make_role(harness, engine, "Beta Notify", ["notification.view", "environment.view"])

    async with harness.api_client() as outsider:
        await harness.login(outsider, "user-b-only")
        await grant(engine, harness, "user-b-only", "Beta Notify", "project", "beta")
        policies = (await outsider.get("/v1/notification-policies")).json()
        deliveries = (await outsider.get("/v1/notification-deliveries")).json()
    assert policies["policies"] == []
    assert deliveries["items"] == []


# --- delivery audit --------------------------------------------------------


async def test_delivery_audit_exposes_state_without_target_or_body(
    engine: AsyncEngine,
) -> None:
    harness = notification_harness()
    world = await open_incident_world(engine)
    policy = await make_policy(engine, world)
    await attach(
        engine,
        policy,
        await make_destination(engine, world, destination_type="webhook", key=WEBHOOK_KEY),
    )
    await plan_pending(engine)

    async with engine.connect() as connection:
        delivery_id = (
            await connection.execute(
                text("SELECT id FROM webhook_deliveries WHERE incident_id = :i"),
                {"i": world["incident_id"]},
            )
        ).scalar_one()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO webhook_delivery_attempts
                    (delivery_id, attempt_number, started_at, completed_at, outcome,
                     http_status, error_code, duration_ms)
                VALUES (:d, 1, now(), now(), 'retryable', 503, 'http_503', 42)
                """
            ),
            {"d": delivery_id},
        )

    async with signed_in(harness, engine) as client:
        listed = await client.get("/v1/notification-deliveries")
        attempts = await client.get(f"/v1/notification-deliveries/{delivery_id}/attempts")

    body = listed.json()
    assert len(body["items"]) == 1
    entry = body["items"][0]
    assert entry["state"] == "pending"
    assert entry["destination_display_name"]
    assert entry["event_type"] == "opened"

    timeline = attempts.json()["attempts"]
    assert timeline[0]["outcome"] == "retryable"
    assert timeline[0]["http_status"] == 503
    assert timeline[0]["error_code"] == "http_503"

    serialized = listed.text + attempts.text
    for forbidden in ("receiver.invalid", RECEIVER_URL, "signing", "secret", "traceback"):
        assert forbidden not in serialized.lower(), forbidden


async def test_delivery_attempts_are_not_found_out_of_scope(engine: AsyncEngine) -> None:
    from test_catalog_api_integration import grant, make_role, seed_catalog_world

    harness = notification_harness()
    world = await open_incident_world(engine)
    await seed_catalog_world(engine)
    policy = await make_policy(engine, world)
    await attach(
        engine,
        policy,
        await make_destination(engine, world, destination_type="webhook", key=WEBHOOK_KEY),
    )
    await plan_pending(engine)
    async with engine.connect() as connection:
        delivery_id = (
            await connection.execute(
                text("SELECT id FROM webhook_deliveries WHERE incident_id = :i"),
                {"i": world["incident_id"]},
            )
        ).scalar_one()

    await make_role(harness, engine, "Beta Notify 2", ["notification.view", "environment.view"])
    async with harness.api_client() as outsider:
        await harness.login(outsider, "user-b-only")
        await grant(engine, harness, "user-b-only", "Beta Notify 2", "project", "beta")
        hidden = await outsider.get(f"/v1/notification-deliveries/{delivery_id}/attempts")
        missing = await outsider.get(
            f"/v1/notification-deliveries/{uuidlib.uuid4()}/attempts"
        )
    assert hidden.status_code == 404
    assert missing.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


async def test_no_endpoint_can_trigger_a_delivery(engine: AsyncEngine) -> None:
    """A user-callable send/replay/retry would be a way to drive Drake's
    outbound traffic on demand."""
    harness = notification_harness()
    async with signed_in(harness, engine) as client:
        for path in (
            "/v1/notifications/test",
            "/v1/notification-deliveries/retry",
            "/v1/notification-deliveries/replay",
        ):
            response = await client.post(path, json={}, headers=await csrf(client))
            assert response.status_code == 404, path
