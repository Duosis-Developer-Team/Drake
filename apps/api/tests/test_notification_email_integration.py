"""E-mail destinations end to end (ADR-0030): the database admits the third
type, the planner routes an incident into the outbox, and the delivery
worker hands it to Microsoft Graph — a local `httpx.MockTransport` here, so
nothing leaves the process and no real tenant or mailbox exists.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from drake_api.notifications.email import IDEMPOTENCY_MESSAGE_HEADER, clear_token_cache
from drake_api.notifications.model import idempotency_key
from drake_api.notifications.planner import plan_pending
from drake_api.notifications.worker import DELIVERY_LEASE_KEY, run_delivery_cycle
from drake_api.settings import EmailDestination, GraphMailSender
from harness_s1 import build_harness, require_it_settings
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from test_notification_api_integration import csrf, signed_in
from test_notification_planner_integration import (
    attach,
    make_destination,
    make_policy,
    open_incident_world,
)
from test_notification_webhook_integration import attempts_for, delivery_row
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

EMAIL_KEY = "drake-test"
RECIPIENTS = ["one@example.test", "two@example.test"]


class FakeGraph:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.send_status = 202

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(self.send_status)

    @property
    def mails(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith("/sendMail")]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
async def redis() -> Any:
    client = aioredis.from_url(require_it_settings().redis_url)
    await client.delete(DELIVERY_LEASE_KEY)
    clear_token_cache()
    yield client
    await client.delete(DELIVERY_LEASE_KEY)
    clear_token_cache()
    await client.aclose()


def email_settings(tmp_path: Path, *, with_sender: bool = True) -> Any:
    secret = tmp_path / "client-secret"
    secret.write_text("fixture-secret", encoding="utf-8")
    sender = GraphMailSender(
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret_file=str(secret),
        sender="alerts@example.test",
        login_base_url="https://login.example.test",
        graph_base_url="https://graph.example.test",
    )
    return require_it_settings().model_copy(
        update={
            "notification_email_sender": sender if with_sender else None,
            "notification_emails": {
                EMAIL_KEY: EmailDestination(recipients=RECIPIENTS, display_name="Drake test")
            },
        }
    )


async def planned_email(engine: AsyncEngine) -> dict[str, Any]:
    world = await open_incident_world(engine)
    policy = await make_policy(engine, world)
    await attach(
        engine,
        policy,
        await make_destination(engine, world, destination_type="email", key=EMAIL_KEY),
    )
    await plan_pending(engine, base_url="")
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT id, destination_key, idempotency_key, incident_event_id "
                    "FROM webhook_deliveries WHERE incident_id = :i"
                ),
                {"i": world["incident_id"]},
            )
        ).one()
    return {
        **world,
        "delivery_id": row[0],
        "key": row[1],
        "idempotency_key": row[2],
        "event_id": row[3],
    }


# --- the database ------------------------------------------------------------


async def test_an_email_row_must_carry_a_key_and_no_recipient(engine: AsyncEngine) -> None:
    world = await open_incident_world(engine)
    with pytest.raises(IntegrityError):
        await make_destination(engine, world, destination_type="email", key=None)
    with pytest.raises(IntegrityError):
        await make_destination(engine, world, destination_type="sms", key="x")


# --- planning ------------------------------------------------------------------


async def test_an_incident_is_planned_into_the_outbox_for_an_email_key(
    engine: AsyncEngine,
) -> None:
    world = await planned_email(engine)
    assert world["key"] == EMAIL_KEY
    # The channel is part of the key, so the same event to a webhook with
    # the same key could never collide with this one.
    assert world["idempotency_key"] == idempotency_key(world["event_id"], "email", EMAIL_KEY)


# --- delivery --------------------------------------------------------------------


async def test_a_planned_email_is_sent_through_graph_and_marked_delivered(
    engine: AsyncEngine, redis: Any, tmp_path: Path
) -> None:
    world = await planned_email(engine)
    graph = FakeGraph()

    report = await run_delivery_cycle(
        engine, email_settings(tmp_path), redis, transport=graph.transport
    )

    assert report.delivered == 1
    assert len(graph.mails) == 1
    message = graph.mails[0]["message"]
    assert [r["emailAddress"]["address"] for r in message["toRecipients"]] == RECIPIENTS
    assert message["subject"].startswith("[Drake][CRITICAL] Incident opened:")
    assert message["internetMessageHeaders"][0] == {
        "name": IDEMPOTENCY_MESSAGE_HEADER,
        "value": world["idempotency_key"],
    }
    row = await delivery_row(engine, world["delivery_id"])
    assert row[0] == "delivered"
    attempts = await attempts_for(engine, world["delivery_id"])
    assert [(a[1], a[2]) for a in attempts] == [("delivered", 202)]


async def test_a_refused_mailbox_is_dead_lettered_not_retried(
    engine: AsyncEngine, redis: Any, tmp_path: Path
) -> None:
    world = await planned_email(engine)
    graph = FakeGraph()
    graph.send_status = 403

    await run_delivery_cycle(engine, email_settings(tmp_path), redis, transport=graph.transport)

    row = await delivery_row(engine, world["delivery_id"])
    assert (row[0], row[2], row[3]) == ("dead_letter", "http_403", 403)


async def test_recipients_without_a_sender_are_suppressed_and_nothing_is_called(
    engine: AsyncEngine, redis: Any, tmp_path: Path
) -> None:
    world = await planned_email(engine)
    graph = FakeGraph()

    await run_delivery_cycle(
        engine, email_settings(tmp_path, with_sender=False), redis, transport=graph.transport
    )

    row = await delivery_row(engine, world["delivery_id"])
    assert (row[0], row[2]) == ("suppressed", "email_sender_not_configured")
    assert graph.requests == []


async def test_a_removed_email_key_is_suppressed(
    engine: AsyncEngine, redis: Any, tmp_path: Path
) -> None:
    world = await planned_email(engine)
    graph = FakeGraph()
    settings = email_settings(tmp_path).model_copy(update={"notification_emails": {}})

    await run_delivery_cycle(engine, settings, redis, transport=graph.transport)

    row = await delivery_row(engine, world["delivery_id"])
    assert (row[0], row[2]) == ("suppressed", "destination_not_configured")
    assert graph.requests == []


# --- the API ---------------------------------------------------------------------


async def test_an_email_destination_names_a_registered_key_and_hides_addresses(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness = build_harness(email_settings(tmp_path))
    world = await open_incident_world(engine)

    async with signed_in(harness, engine) as client:
        unknown = await client.post(
            "/v1/notification-destinations",
            json={
                "project_id": str(world["project_id"]),
                "destination_type": "email",
                "display_name": "Rogue",
                "destination_key": "not-registered",
            },
            headers=await csrf(client),
        )
        assert unknown.status_code == 422

        smuggled = await client.post(
            "/v1/notification-destinations",
            json={
                "project_id": str(world["project_id"]),
                "destination_type": "email",
                "display_name": "Rogue",
                "destination_key": EMAIL_KEY,
                "recipients": ["attacker@example.invalid"],
            },
            headers=await csrf(client),
        )
        assert smuggled.status_code == 422

        created = await client.post(
            "/v1/notification-destinations",
            json={
                "project_id": str(world["project_id"]),
                "destination_type": "email",
                "display_name": "Drake test",
                "destination_key": EMAIL_KEY,
            },
            headers=await csrf(client),
        )
        assert created.status_code == 201, created.text

        options = (await client.get("/v1/notification-policies/options")).json()
        listed = (await client.get("/v1/notification-destinations")).json()

    assert "email" in options["destination_types"]
    assert options["email_keys"] == [
        {
            "key": EMAIL_KEY,
            "display_name": "Drake test",
            "recipient_count": 2,
            "payload_schema_version": 1,
        }
    ]
    serialized = str(options) + str(listed)
    for address in RECIPIENTS:
        assert address not in serialized
    assert any(
        item["destination_type"] == "email" and item["destination_key"] == EMAIL_KEY
        for item in listed["destinations"]
    )
    assert uuid.UUID(created.json()["id"])
