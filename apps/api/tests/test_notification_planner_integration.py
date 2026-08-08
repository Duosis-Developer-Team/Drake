"""Incident events → policies → notifications, against the real database.

The planner is the part that turns an immutable timeline into things that
reach people. Its failure modes are all of the "quietly wrong" kind: a
duplicate that pages someone twice, a match that reaches a project the
recipient cannot see, or an event that is scanned forever because nothing
ever recorded that it had been considered.
"""

import asyncio
import json
import uuid
from typing import Any

import pytest
from drake_api.notifications.planner import (
    mark_existing_events_planned,
    matching_destinations,
    plan_pending,
    unplanned_events,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_incident_processor_integration import evaluation, make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

WEBHOOK_KEY = "ops-primary"


async def open_incident_world(engine: AsyncEngine) -> dict[str, Any]:
    """A world with one open incident, built through the real processor."""
    from datetime import timedelta

    from drake_api.incidents.processor import process_evaluation
    from test_incident_processor_integration import BASE

    world = await make_world(engine)
    await process_evaluation(engine, evaluation(world))
    await process_evaluation(engine, evaluation(world, computed_at=BASE + timedelta(minutes=1)))
    async with engine.connect() as connection:
        incident = (
            await connection.execute(
                text("SELECT id FROM incidents WHERE binding_id = :b"), {"b": world["binding_id"]}
            )
        ).scalar_one()
        event = (
            await connection.execute(
                text(
                    "SELECT id FROM incident_events WHERE incident_id = :i "
                    "AND event_type = 'opened'"
                ),
                {"i": incident},
            )
        ).scalar_one()
    return {**world, "incident_id": incident, "opened_event_id": event}


async def make_recipient(
    engine: AsyncEngine, service_scope: uuid.UUID, *, grant: bool = True
) -> uuid.UUID:
    """A Drake user, optionally granted visibility of the service."""
    subject = f"recipient-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as connection:
        identity = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, display_name, email) "
                    "VALUES ('https://tests.drake.local/v2.0', :s, 'Recipient', :e) RETURNING id"
                ),
                {"s": subject, "e": f"{subject}@example.test"},
            )
        ).scalar_one()
        if grant:
            await connection.execute(
                text(
                    """
                    INSERT INTO roles (name, description, is_system)
                    VALUES ('Notify Reader', 'test', true)
                    ON CONFLICT (name) DO NOTHING
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO role_permissions (role_id, permission_key)
                    SELECT r.id, 'environment.view' FROM roles r WHERE r.name = 'Notify Reader'
                    ON CONFLICT DO NOTHING
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO grants (identity_id, role_id, scope_id)
                    SELECT :identity, r.id, :scope FROM roles r WHERE r.name = 'Notify Reader'
                    """
                ),
                {"identity": identity, "scope": service_scope},
            )
    return identity


async def make_policy(
    engine: AsyncEngine,
    world: dict[str, Any],
    *,
    event_types: tuple[str, ...] = ("opened", "auto_resolved"),
    environment_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    enabled: bool = True,
) -> uuid.UUID:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    """
                    INSERT INTO notification_policies
                        (display_name, project_id, environment_id, service_id,
                         event_types, severities, enabled)
                    VALUES (:name, :project, :environment, :service,
                            CAST(:events AS jsonb), '["critical"]'::jsonb, :enabled)
                    RETURNING id
                    """
                ),
                {
                    "name": f"policy-{uuid.uuid4().hex[:6]}",
                    "project": world["project_id"],
                    "environment": environment_id,
                    "service": service_id,
                    "events": json.dumps(list(event_types)),
                    "enabled": enabled,
                },
            )
        ).scalar_one()


async def make_destination(
    engine: AsyncEngine,
    world: dict[str, Any],
    *,
    destination_type: str = "in_app_user",
    identity_id: uuid.UUID | None = None,
    key: str | None = None,
    enabled: bool = True,
) -> uuid.UUID:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text(
                    """
                    INSERT INTO notification_destinations
                        (destination_type, display_name, project_id, identity_id,
                         destination_key, enabled)
                    VALUES (:type, :name, :project, :identity, :key, :enabled)
                    RETURNING id
                    """
                ),
                {
                    "type": destination_type,
                    "name": f"dest-{uuid.uuid4().hex[:6]}",
                    "project": world["project_id"],
                    "identity": identity_id,
                    "key": key,
                    "enabled": enabled,
                },
            )
        ).scalar_one()


async def attach(engine: AsyncEngine, policy_id: uuid.UUID, destination_id: uuid.UUID) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO notification_policy_destinations (policy_id, destination_id) "
                "VALUES (:p, :d) ON CONFLICT DO NOTHING"
            ),
            {"p": policy_id, "d": destination_id},
        )


async def notifications_for(engine: AsyncEngine, recipient: uuid.UUID) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT id, title, body, target_path, event_type, read_at, "
                        "metadata_snapshot FROM in_app_notifications "
                        "WHERE recipient_identity_id = :r ORDER BY created_at"
                    ),
                    {"r": recipient},
                )
            ).all()
        )


async def deliveries_for(engine: AsyncEngine, incident_id: uuid.UUID) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT id, state, idempotency_key, payload, destination_key "
                        "FROM webhook_deliveries WHERE incident_id = :i"
                    ),
                    {"i": incident_id},
                )
            ).all()
        )


# --- the vertical slice ---------------------------------------------------


async def test_an_opened_event_reaches_a_matching_recipient(engine: AsyncEngine) -> None:
    """Incident opened → policy → in-app notification."""
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    destination = await make_destination(engine, world, identity_id=recipient)
    await attach(engine, policy, destination)

    report = await plan_pending(engine, base_url="https://drake.example.test")

    assert report.in_app_created == 1
    rows = await notifications_for(engine, recipient)
    assert len(rows) == 1
    # Every word is composed by the server from reason codes and catalog
    # keys; there is no endpoint through which a person could write either.
    assert "Incident opened" in rows[0][1]
    assert rows[0][3] == f"/incidents/{world['incident_id']}"
    assert rows[0][4] == "opened"
    assert rows[0][5] is None
    assert rows[0][6]["primary_reason"] == "no_ready_replicas"


async def test_replanning_the_same_event_creates_nothing_new(engine: AsyncEngine) -> None:
    """The plan row is what makes the planner safe to run on a timer."""
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    first = await plan_pending(engine)
    second = await plan_pending(engine)

    assert first.in_app_created == 1
    # Nothing left to plan: not "planned again and deduplicated", but not
    # picked up at all.
    assert second.events_planned == 0
    assert second.in_app_created == 0
    assert len(await notifications_for(engine, recipient)) == 1


async def test_an_event_with_no_matching_policy_is_not_rescanned_forever(
    engine: AsyncEngine,
) -> None:
    """"Matched nothing" is a finished decision, and is recorded as one."""
    world = await open_incident_world(engine)

    report = await plan_pending(engine)
    assert report.events_planned >= 1
    assert report.in_app_created == 0

    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text(
                    "SELECT state, matched_destinations FROM notification_event_plans "
                    "WHERE incident_event_id = :e"
                ),
                {"e": world["opened_event_id"]},
            )
        ).first()
        remaining = await unplanned_events(connection, 10)
    assert state is not None
    assert state[0] == "planned"
    assert state[1] == 0
    assert remaining == []


async def test_two_policies_naming_one_person_send_one_notification(
    engine: AsyncEngine,
) -> None:
    """Overlapping policies are a configuration, not a reason to spam."""
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    destination = await make_destination(engine, world, identity_id=recipient)
    for _ in range(3):
        await attach(engine, await make_policy(engine, world), destination)

    async with engine.connect() as connection:
        events = await unplanned_events(connection, 10)
        matches = await matching_destinations(connection, events[0])
    # One destination, with all three policies recorded against it.
    assert len(matches) == 1
    assert len(matches[0]["policy_ids"]) == 3

    await plan_pending(engine)
    assert len(await notifications_for(engine, recipient)) == 1


@pytest.mark.parametrize(
    ("label", "policy_kwargs", "destination_kwargs"),
    [
        ("disabled policy", {"enabled": False}, {}),
        ("disabled destination", {}, {"enabled": False}),
        ("event not subscribed", {"event_types": ("auto_resolved",)}, {}),
    ],
)
async def test_nothing_is_delivered_without_an_active_match(
    engine: AsyncEngine,
    label: str,
    policy_kwargs: dict[str, Any],
    destination_kwargs: dict[str, Any],
) -> None:
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world, **policy_kwargs)
    destination = await make_destination(
        engine, world, identity_id=recipient, **destination_kwargs
    )
    await attach(engine, policy, destination)

    await plan_pending(engine)
    assert await notifications_for(engine, recipient) == [], label


async def test_a_policy_narrowed_to_another_environment_does_not_match(
    engine: AsyncEngine,
) -> None:
    world = await open_incident_world(engine)
    other = await make_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world, environment_id=other["environment_id"])
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    await plan_pending(engine)
    assert await notifications_for(engine, recipient) == []


async def test_a_policy_in_another_project_does_not_match(engine: AsyncEngine) -> None:
    """Project scope is the outer boundary, and it is checked in SQL."""
    await open_incident_world(engine)
    other = await make_world(engine)
    recipient = await make_recipient(engine, other["service_scope"])
    policy = await make_policy(engine, other)
    await attach(engine, policy, await make_destination(engine, other, identity_id=recipient))

    await plan_pending(engine)
    assert await notifications_for(engine, recipient) == []


async def test_a_new_policy_does_not_notify_about_past_events(engine: AsyncEngine) -> None:
    """Enabling notifications must not replay the incident history.

    Delivering every past incident on the day someone turns this on is how
    a notification system loses everyone's trust immediately.
    """
    world = await open_incident_world(engine)
    baselined = await mark_existing_events_planned(engine)
    assert baselined >= 1

    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    report = await plan_pending(engine)
    assert report.events_planned == 0
    assert await notifications_for(engine, recipient) == []


async def test_recovery_progress_events_never_notify(engine: AsyncEngine) -> None:
    """`recovery_started` would tell someone twice about one recovery."""
    world = await open_incident_world(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO incident_events (incident_id, event_type, occurred_at) "
                "VALUES (:i, 'recovery_started', now())"
            ),
            {"i": world["incident_id"]},
        )
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(
        engine, world, event_types=("opened", "acknowledged", "auto_resolved")
    )
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    await plan_pending(engine)
    rows = await notifications_for(engine, recipient)
    assert [row[4] for row in rows] == ["opened"]


async def test_concurrent_planners_produce_one_row_per_destination(
    engine: AsyncEngine,
) -> None:
    """Unique constraints, not timing, are what makes this true."""
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    await asyncio.gather(plan_pending(engine), plan_pending(engine), plan_pending(engine))
    assert len(await notifications_for(engine, recipient)) == 1


# --- webhook planning ------------------------------------------------------


async def test_a_webhook_destination_produces_a_frozen_delivery(
    engine: AsyncEngine,
) -> None:
    """The payload is snapshotted at plan time, and carries no query material."""
    world = await open_incident_world(engine)
    policy = await make_policy(engine, world)
    destination = await make_destination(
        engine, world, destination_type="webhook", key=WEBHOOK_KEY
    )
    await attach(engine, policy, destination)

    report = await plan_pending(engine, base_url="https://drake.example.test")
    assert report.webhooks_created == 1

    rows = await deliveries_for(engine, world["incident_id"])
    assert len(rows) == 1
    _delivery_id, state, key, payload, destination_key = rows[0]
    assert state == "pending"
    assert destination_key == WEBHOOK_KEY
    assert len(key) == 48

    assert payload["schema_version"] == 1
    assert payload["event_type"] == "opened"
    assert payload["incident"]["severity"] == "critical"
    assert payload["incident"]["primary_reason"] == "no_ready_replicas"
    assert payload["incident"]["url"].startswith("https://drake.example.test/incidents/")
    assert payload["service"]["service_key"]

    serialized = json.dumps(payload).lower()
    for forbidden in ("sum(rate(", "kube_workload", "promql", "password", "token", "secret"):
        assert forbidden not in serialized, forbidden


async def test_editing_a_policy_does_not_rewrite_a_planned_delivery(
    engine: AsyncEngine,
) -> None:
    """A rule change applies to the future, never to what was decided."""
    world = await open_incident_world(engine)
    policy = await make_policy(engine, world)
    await attach(
        engine,
        policy,
        await make_destination(engine, world, destination_type="webhook", key=WEBHOOK_KEY),
    )
    await plan_pending(engine, base_url="https://drake.example.test")
    before = await deliveries_for(engine, world["incident_id"])

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE notification_policies SET enabled = false, "
                "event_types = '[\"acknowledged\"]'::jsonb, version = version + 1 WHERE id = :p"
            ),
            {"p": policy},
        )
    await plan_pending(engine)

    after = await deliveries_for(engine, world["incident_id"])
    assert len(after) == 1
    assert after[0][2] == before[0][2]  # same idempotency key
    assert after[0][3] == before[0][3]  # byte-identical payload snapshot


async def test_one_broken_destination_does_not_block_the_others(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planner that stops at the first bad row silently drops the rest."""
    world = await open_incident_world(engine)
    good_recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=good_recipient))

    # A webhook destination whose planning blows up. Injected rather than
    # forced through the schema, because the schema is doing its job.
    broken = await make_destination(
        engine, world, destination_type="webhook", key=WEBHOOK_KEY
    )
    await attach(engine, policy, broken)

    import drake_api.notifications.planner as planner_module

    original = planner_module._plan_destination

    async def flaky(connection: Any, event: Any, destination: Any, base_url: str) -> str | None:
        if destination["id"] == broken:
            raise RuntimeError("this destination cannot be planned")
        result: str | None = await original(connection, event, destination, base_url)
        return result

    monkeypatch.setattr(planner_module, "_plan_destination", flaky)

    report = await plan_pending(engine)
    assert report.destinations_failed == 1
    assert report.in_app_created == 1
    assert len(await notifications_for(engine, good_recipient)) == 1
    # The event is recorded as attempted-with-failure, not silently done.
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text(
                    "SELECT state, error_code FROM notification_event_plans "
                    "WHERE incident_event_id = :e"
                ),
                {"e": world["opened_event_id"]},
            )
        ).first()
    assert state is not None
    assert state[0] == "failed"
    assert state[1] == "destination_planning_failed"


async def test_notification_planning_never_changes_the_incident(
    engine: AsyncEngine,
) -> None:
    """An outbox exists precisely so delivery cannot disturb the record."""
    world = await open_incident_world(engine)
    policy = await make_policy(engine, world)
    await attach(
        engine,
        policy,
        await make_destination(engine, world, destination_type="webhook", key=WEBHOOK_KEY),
    )
    async with engine.connect() as connection:
        before = (
            await connection.execute(
                text(
                    "SELECT state, version, resolved_at FROM incidents WHERE id = :i"
                ),
                {"i": world["incident_id"]},
            )
        ).first()
        events_before = (
            await connection.execute(
                text("SELECT count(*) FROM incident_events WHERE incident_id = :i"),
                {"i": world["incident_id"]},
            )
        ).scalar_one()

    await plan_pending(engine)

    async with engine.connect() as connection:
        after = (
            await connection.execute(
                text("SELECT state, version, resolved_at FROM incidents WHERE id = :i"),
                {"i": world["incident_id"]},
            )
        ).first()
        events_after = (
            await connection.execute(
                text("SELECT count(*) FROM incident_events WHERE incident_id = :i"),
                {"i": world["incident_id"]},
            )
        ).scalar_one()
    assert after == before
    assert events_after == events_before
