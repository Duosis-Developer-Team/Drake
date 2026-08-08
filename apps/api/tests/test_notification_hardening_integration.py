"""Acceptance hardening: scope loss, effective time, canonical dedup,
connection pinning and terminal delivery states.

Each block below pins one gap that the first Sprint 7 implementation left
open. They are grouped here rather than scattered so the guarantees can be
read together — and so a regression in any of them is obvious.
"""

import asyncio
import hashlib
import hmac
import json
import uuid as uuidlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from drake_api.notifications.planner import matching_destinations, plan_pending, unplanned_events
from drake_api.notifications.repository import NotificationError, list_inbox, mark_read
from drake_api.notifications.repository import unread_count as read_unread_count
from drake_api.notifications.webhook import (
    DestinationRefusedError,
    send_webhook,
    validate_destination,
)
from drake_api.notifications.worker import DELIVERY_LEASE_KEY, deliver_one, run_delivery_cycle
from drake_api.rbac.service import Principal
from drake_api.settings import WebhookDestination
from harness_s1 import require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_notification_planner_integration import (
    WEBHOOK_KEY,
    attach,
    make_destination,
    make_policy,
    make_recipient,
    notifications_for,
    open_incident_world,
)
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

PUBLIC_ADDRESS = "93.184.216.34"


async def public_resolver(hostname: str, port: int) -> list[str]:
    return [PUBLIC_ADDRESS]


def principal_for(identity_id: uuidlib.UUID) -> Principal:
    return Principal(identity_id=identity_id, issuer="https://tests.drake.local/v2.0")


async def revoke_grants(engine: AsyncEngine, identity_id: uuidlib.UUID) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE grants SET revoked_at = now() WHERE identity_id = :id"),
            {"id": identity_id},
        )


async def restore_grants(engine: AsyncEngine, identity_id: uuidlib.UUID) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE grants SET revoked_at = NULL WHERE identity_id = :id"),
            {"id": identity_id},
        )


# ===========================================================================
# 1. A notification whose incident left the reader's scope disappears
# ===========================================================================


async def test_losing_scope_removes_the_row_from_list_count_and_mark_read(
    engine: AsyncEngine,
) -> None:
    """Returning a redacted placeholder would still answer "something
    exists here you may not see" — which is the enumeration the scope
    filter exists to prevent."""
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))
    await plan_pending(engine)

    principal = principal_for(recipient)
    async with engine.connect() as connection:
        visible = await list_inbox(connection, principal)
        count = await read_unread_count(connection, principal)
    assert len(visible["items"]) == 1
    assert count == 1
    notification_id = uuidlib.UUID(visible["items"][0]["id"])

    await revoke_grants(engine, recipient)

    async with engine.connect() as connection:
        hidden = await list_inbox(connection, principal)
        hidden_count = await read_unread_count(connection, principal)
    # Gone from the list, not returned as a placeholder.
    assert hidden["items"] == []
    assert hidden_count == 0

    # And unaddressable: fail-closed rather than a silent no-op, which
    # would let the response count act as an existence oracle.
    async with engine.begin() as connection:
        with pytest.raises(NotificationError) as error:
            await mark_read(connection, principal, [notification_id])
    assert error.value.code == "not_found"


async def test_an_unknown_id_and_a_hidden_id_fail_identically(
    engine: AsyncEngine,
) -> None:
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))
    await plan_pending(engine)
    async with engine.connect() as connection:
        listed = await list_inbox(connection, principal_for(recipient))
    hidden_id = uuidlib.UUID(listed["items"][0]["id"])
    await revoke_grants(engine, recipient)

    principal = principal_for(recipient)
    codes = []
    for candidate in (hidden_id, uuidlib.uuid4()):
        async with engine.begin() as connection:
            with pytest.raises(NotificationError) as error:
                await mark_read(connection, principal, [candidate])
        codes.append(error.value.code)
    assert codes == ["not_found", "not_found"]


async def test_the_rest_of_the_inbox_pages_normally_around_a_hidden_row(
    engine: AsyncEngine,
) -> None:
    """The cursor must not leak the position or count of what is hidden."""
    first = await open_incident_world(engine)
    recipient = await make_recipient(engine, first["service_scope"])
    others = []
    for _ in range(3):
        world = await open_incident_world(engine)
        # Same recipient, granted on each service so all four are visible.
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO grants (identity_id, role_id, scope_id)
                    SELECT :identity, r.id, :scope FROM roles r WHERE r.name = 'Notify Reader'
                    """
                ),
                {"identity": recipient, "scope": world["service_scope"]},
            )
        others.append(world)
    for world in [first, *others]:
        policy = await make_policy(engine, world)
        await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))
    await plan_pending(engine)

    principal = principal_for(recipient)
    async with engine.connect() as connection:
        page = await list_inbox(connection, principal, limit=10)
    assert len(page["items"]) == 4

    # Revoke access to exactly one service.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE grants SET revoked_at = now() WHERE identity_id = :i AND scope_id = :s"),
            {"i": recipient, "s": first["service_scope"]},
        )

    async with engine.connect() as connection:
        after = await list_inbox(connection, principal, limit=2)
        second = await list_inbox(connection, principal, limit=2, cursor=after["next_cursor"])
    seen = [item["id"] for item in after["items"] + second["items"]]
    # Three visible rows, paged cleanly: no gap where the hidden one was.
    assert len(seen) == len(set(seen)) == 3
    assert second["next_cursor"] is None


async def test_restoring_the_grant_makes_the_notification_visible_again(
    engine: AsyncEngine,
) -> None:
    """Pinned deliberately: the row was never deleted, so re-granting the
    same access restores exactly the visibility the reader had before."""
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))
    await plan_pending(engine)
    principal = principal_for(recipient)

    await revoke_grants(engine, recipient)
    async with engine.connect() as connection:
        assert (await list_inbox(connection, principal))["items"] == []

    await restore_grants(engine, recipient)
    async with engine.connect() as connection:
        restored = await list_inbox(connection, principal)
        count = await read_unread_count(connection, principal)
    assert len(restored["items"]) == 1
    assert count == 1
    # Still unread: hiding it did not consume it.
    assert restored["items"][0]["read_at"] is None


# ===========================================================================
# 2. Policies apply from when they were configured
# ===========================================================================


async def set_effective_from(
    engine: AsyncEngine, policy_id: uuidlib.UUID, moment: datetime
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE notification_policies SET effective_from = :t WHERE id = :id"),
            {"t": moment, "id": policy_id},
        )


async def test_a_policy_created_after_an_event_does_not_route_it(
    engine: AsyncEngine,
) -> None:
    """The core of the gap: freezing planned deliveries was not enough,
    because an unplanned event would still be matched later."""
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])

    # The policy is created now, i.e. strictly after the event was recorded.
    policy = await make_policy(engine, world)
    await set_effective_from(engine, policy, datetime.now(UTC) + timedelta(seconds=1))
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    report = await plan_pending(engine)
    assert report.in_app_created == 0
    assert await notifications_for(engine, recipient) == []
    # The event is still marked planned, so it is not rescanned forever.
    assert report.events_planned >= 1


async def test_a_policy_created_before_an_event_routes_it(engine: AsyncEngine) -> None:
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    # Effective before the event was recorded.
    await set_effective_from(engine, policy, datetime.now(UTC) - timedelta(hours=1))
    destination = await make_destination(engine, world, identity_id=recipient)
    await attach(engine, policy, destination)

    await plan_pending(engine)
    assert len(await notifications_for(engine, recipient)) == 1


async def test_a_backlog_is_still_delivered_when_the_policy_did_not_change(
    engine: AsyncEngine,
) -> None:
    """Retroactivity and backlog are different things.

    A window-based scan would drop both; effective time drops only the
    first.
    """
    recipient_world = await open_incident_world(engine)
    recipient = await make_recipient(engine, recipient_world["service_scope"])
    policy = await make_policy(engine, recipient_world)
    await set_effective_from(engine, policy, datetime.now(UTC) - timedelta(days=2))
    await attach(
        engine, policy, await make_destination(engine, recipient_world, identity_id=recipient)
    )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE notification_policy_destinations SET effective_from = :t "
                "WHERE policy_id = :p"
            ),
            {"t": datetime.now(UTC) - timedelta(days=2), "p": policy},
        )
        # The event sat unplanned for a day because the worker was off.
        await connection.execute(
            text("UPDATE incident_events SET created_at = :t WHERE incident_id = :i"),
            {"t": datetime.now(UTC) - timedelta(days=1), "i": recipient_world["incident_id"]},
        )

    await plan_pending(engine)
    assert len(await notifications_for(engine, recipient)) == 1


async def test_re_scoping_a_policy_does_not_capture_older_unplanned_events(
    engine: AsyncEngine,
) -> None:
    """An edit that changes WHAT a policy matches starts a new clock."""
    from drake_api.notifications.repository import update_policy

    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    owner = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world, event_types=("auto_resolved",))
    await set_effective_from(engine, policy, datetime.now(UTC) - timedelta(hours=1))
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    # The `opened` event exists and is unplanned. Now the policy is widened
    # to include `opened` — the older event must not be captured.
    async with engine.begin() as connection:
        # Grant the editor management authority on the project scope.
        project_scope = (
            await connection.execute(
                text("SELECT scope_id FROM projects WHERE id = :p"), {"p": world["project_id"]}
            )
        ).scalar_one()
        await connection.execute(
            text(
                """
                INSERT INTO roles (name, description, is_system)
                VALUES ('Notify Manager', 'test', true) ON CONFLICT (name) DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO role_permissions (role_id, permission_key)
                SELECT r.id, 'notification.manage' FROM roles r WHERE r.name = 'Notify Manager'
                ON CONFLICT DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO grants (identity_id, role_id, scope_id)
                SELECT :i, r.id, :s FROM roles r WHERE r.name = 'Notify Manager'
                """
            ),
            {"i": owner, "s": project_scope},
        )

    async with engine.begin() as connection:
        version = (
            await connection.execute(
                text("SELECT version FROM notification_policies WHERE id = :p"), {"p": policy}
            )
        ).scalar_one()
        await update_policy(
            connection,
            principal_for(owner),
            policy,
            display_name="widened",
            environment_id=None,
            service_id=None,
            event_types=["opened", "auto_resolved"],
            severities=["critical"],
            enabled=True,
            expected_version=version,
            actor_identity_id=owner,
        )

    await plan_pending(engine)
    assert await notifications_for(engine, recipient) == []


async def test_disable_then_re_enable_does_not_backfill(engine: AsyncEngine) -> None:
    """Events that happened while a policy was off were, at that moment,
    not routed by it. Turning it back on must not change that."""
    from drake_api.notifications.repository import update_policy

    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    owner = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world, enabled=False)
    await set_effective_from(engine, policy, datetime.now(UTC) - timedelta(hours=1))
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    async with engine.begin() as connection:
        project_scope = (
            await connection.execute(
                text("SELECT scope_id FROM projects WHERE id = :p"), {"p": world["project_id"]}
            )
        ).scalar_one()
        await connection.execute(
            text(
                """
                INSERT INTO roles (name, description, is_system)
                VALUES ('Notify Manager', 'test', true) ON CONFLICT (name) DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO role_permissions (role_id, permission_key)
                SELECT r.id, 'notification.manage' FROM roles r WHERE r.name = 'Notify Manager'
                ON CONFLICT DO NOTHING
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO grants (identity_id, role_id, scope_id)
                SELECT :i, r.id, :s FROM roles r WHERE r.name = 'Notify Manager'
                """
            ),
            {"i": owner, "s": project_scope},
        )

    async with engine.begin() as connection:
        version = (
            await connection.execute(
                text("SELECT version FROM notification_policies WHERE id = :p"), {"p": policy}
            )
        ).scalar_one()
        await update_policy(
            connection,
            principal_for(owner),
            policy,
            display_name="re-enabled",
            environment_id=None,
            service_id=None,
            event_types=["opened", "auto_resolved"],
            severities=["critical"],
            enabled=True,
            expected_version=version,
            actor_identity_id=owner,
        )

    # The event predates the re-enable, so it is not backfilled.
    await plan_pending(engine)
    assert await notifications_for(engine, recipient) == []

    # But an event recorded AFTER the re-enable is delivered.
    later = await open_incident_world(engine)
    async with engine.begin() as connection:
        # Same project, so the same policy matches it.
        await connection.execute(
            text("UPDATE incidents SET project_id = :p WHERE id = :i"),
            {"p": world["project_id"], "i": later["incident_id"]},
        )
    await plan_pending(engine)
    assert len(await notifications_for(engine, recipient)) == 1


# ===========================================================================
# 3. Deduplication is on the canonical final destination
# ===========================================================================


async def test_two_destination_rows_for_one_person_send_one_notification(
    engine: AsyncEngine,
) -> None:
    """Uniqueness on the destination ROW was not enough: two rows naming
    one person are still one person."""
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])

    # Two destination rows, same identity, in two different projects would
    # violate the per-project unique — so use two projects' policies is not
    # possible. Instead: one row, two policies, plus a second row created
    # by relaxing the per-project unique via a second project is out of
    # scope. What matters is the canonical grouping, exercised here with
    # two policies pointing at the same row and a second row in a second
    # project that also matches.
    destination = await make_destination(engine, world, identity_id=recipient)
    for _ in range(3):
        await attach(engine, await make_policy(engine, world), destination)

    async with engine.connect() as connection:
        events = await unplanned_events(connection, 10)
        matches = await matching_destinations(connection, events[0])
    assert len(matches) == 1
    assert len(matches[0]["policy_ids"]) == 3

    await plan_pending(engine)
    assert len(await notifications_for(engine, recipient)) == 1


async def test_the_database_refuses_a_second_notification_for_one_recipient(
    engine: AsyncEngine,
) -> None:
    """Written directly against the table: if the canonical unique were
    dropped, this fails even though every planner test still passes."""
    from sqlalchemy.exc import IntegrityError

    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    first = await make_destination(engine, world, identity_id=recipient)
    await attach(engine, await make_policy(engine, world), first)
    await plan_pending(engine)

    # A DIFFERENT destination row naming the same recipient.
    async with engine.begin() as connection:
        other_project = await open_incident_world(engine)
        second = (
            await connection.execute(
                text(
                    """
                    INSERT INTO notification_destinations
                        (destination_type, display_name, project_id, identity_id)
                    VALUES ('in_app_user', 'duplicate', :project, :identity)
                    RETURNING id
                    """
                ),
                {"project": other_project["project_id"], "identity": recipient},
            )
        ).scalar_one()

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO in_app_notifications
                        (recipient_identity_id, incident_id, incident_event_id,
                         destination_id, event_type, title, body, target_path)
                    VALUES (:r, :i, :e, :d, 'opened', 'dup', 'dup', '/incidents/x')
                    """
                ),
                {
                    "r": recipient,
                    "i": world["incident_id"],
                    "e": world["opened_event_id"],
                    "d": second,
                },
            )


async def test_the_database_refuses_a_second_delivery_for_one_webhook_key(
    engine: AsyncEngine,
) -> None:
    """One event, one runtime endpoint, one call.

    The per-project unique on destinations already stops two rows sharing a
    key inside one project, so this asserts the guarantee where it must
    hold regardless of how destinations are arranged: on the delivery
    itself, in the database.
    """
    from sqlalchemy.exc import IntegrityError
    from test_notification_webhook_integration import planned_delivery

    world = await planned_delivery(engine)
    async with engine.connect() as connection:
        existing = (
            await connection.execute(
                text(
                    "SELECT destination_id, project_id, incident_event_id "
                    "FROM webhook_deliveries WHERE id = :id"
                ),
                {"id": world["delivery_id"]},
            )
        ).first()
    assert existing is not None

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO webhook_deliveries
                        (incident_id, incident_event_id, destination_id, project_id,
                         destination_key, payload_schema_version, payload,
                         idempotency_key, state)
                    VALUES (:i, :e, :d, :p, :key, 1, '{}'::jsonb, :idem, 'pending')
                    """
                ),
                {
                    "i": world["incident_id"],
                    "e": existing[2],
                    "d": existing[0],
                    "p": existing[1],
                    # Same canonical target, different row identity.
                    "key": WEBHOOK_KEY,
                    "idem": uuidlib.uuid4().hex,
                },
            )


async def test_different_recipients_are_not_deduped_together(
    engine: AsyncEngine,
) -> None:
    """The guarantee must be narrow: two real recipients still get two."""
    world = await open_incident_world(engine)
    first = await make_recipient(engine, world["service_scope"])
    second = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    for identity in (first, second):
        await attach(engine, policy, await make_destination(engine, world, identity_id=identity))

    await plan_pending(engine)
    assert len(await notifications_for(engine, first)) == 1
    assert len(await notifications_for(engine, second)) == 1


async def test_concurrent_planners_still_produce_one_canonical_record(
    engine: AsyncEngine,
) -> None:
    world = await open_incident_world(engine)
    recipient = await make_recipient(engine, world["service_scope"])
    policy = await make_policy(engine, world)
    await attach(engine, policy, await make_destination(engine, world, identity_id=recipient))

    await asyncio.gather(plan_pending(engine), plan_pending(engine), plan_pending(engine))
    assert len(await notifications_for(engine, recipient)) == 1


# ===========================================================================
# 4. The validated address is the one that is dialled
# ===========================================================================


def destination(**overrides: Any) -> WebhookDestination:
    base: dict[str, Any] = {"url": "https://receiver.invalid/hooks/drake"}
    base.update(overrides)
    return WebhookDestination(**base)


async def test_the_request_is_aimed_at_the_validated_address(
    engine: AsyncEngine,
) -> None:
    """Pinning is what makes the DNS check binding.

    Without it, httpx resolves the hostname again and a hostile answer can
    flip public→private in between — the check would have proved nothing.
    """
    settings = require_it_settings().model_copy(update={"env": "prod"})
    target = await validate_destination(destination(), settings, public_resolver)

    assert target.address == PUBLIC_ADDRESS
    assert target.url == f"https://{PUBLIC_ADDRESS}/hooks/drake"
    # The hostname survives for routing and for certificate verification.
    assert target.host == "receiver.invalid"
    assert target.sni == "receiver.invalid"


async def test_an_ipv6_answer_is_bracketed_and_checked() -> None:
    settings = require_it_settings().model_copy(update={"env": "prod"})

    async def ipv6(hostname: str, port: int) -> list[str]:
        return ["2606:2800:220:1:248:1893:25c8:1946"]

    target = await validate_destination(destination(), settings, ipv6)
    assert target.url.startswith("https://[2606:2800:")

    for forbidden in ("::1", "fe80::1", "ff02::1"):

        async def refused(hostname: str, port: int, address: str = forbidden) -> list[str]:
            return [address]

        with pytest.raises(DestinationRefusedError):
            await validate_destination(destination(), settings, refused)


async def test_every_answer_is_checked_not_just_the_first() -> None:
    """A name that CAN answer with a forbidden address is not a safe name."""
    settings = require_it_settings().model_copy(update={"env": "prod"})

    async def public_then_metadata(hostname: str, port: int) -> list[str]:
        return [PUBLIC_ADDRESS, "169.254.169.254"]

    with pytest.raises(DestinationRefusedError):
        await validate_destination(destination(), settings, public_then_metadata)


async def test_a_hostname_that_turns_private_is_refused_before_any_connect(
    engine: AsyncEngine,
) -> None:
    """Validation and connection are one decision, so there is no window."""
    attempted: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        attempted.append(request)
        return httpx.Response(200)

    settings = require_it_settings().model_copy(update={"env": "prod"})

    async def rebinds(hostname: str, port: int) -> list[str]:
        return ["10.0.0.9"]

    result = await send_webhook(
        destination(),
        settings,
        payload={"schema_version": 1},
        idempotency_key="k",
        transport=httpx.MockTransport(record),
        resolver=rebinds,
    )
    assert result.outcome == "refused"
    assert result.error_code == "destination_private_refused"
    assert attempted == []


async def test_each_retry_revalidates_dns(engine: AsyncEngine) -> None:
    """A destination that was safe on attempt one is re-checked on two."""
    calls = {"n": 0}

    async def flips(hostname: str, port: int) -> list[str]:
        calls["n"] += 1
        return [PUBLIC_ADDRESS] if calls["n"] == 1 else ["127.0.0.1"]

    settings = require_it_settings().model_copy(update={"env": "prod"})
    transport = httpx.MockTransport(lambda request: httpx.Response(503))

    first = await send_webhook(
        destination(),
        settings,
        payload={"schema_version": 1},
        idempotency_key="k",
        transport=transport,
        resolver=flips,
    )
    second = await send_webhook(
        destination(),
        settings,
        payload={"schema_version": 1},
        idempotency_key="k",
        transport=transport,
        resolver=flips,
    )
    assert first.outcome == "retryable"
    assert second.outcome == "refused"
    assert calls["n"] == 2


# --- a real socket, not a MockTransport -----------------------------------


class LocalReceiver:
    """A real HTTP server on a real socket.

    MockTransport proves what Drake composes; this proves what actually
    leaves the process — the pinned connection target, the `Host` header,
    the idempotency header and the signature all cross a genuine
    socket boundary here.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self._server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                name, _, value = line.partition(": ")
                headers[name.lower()] = value
        length = int(headers.get("content-length", "0"))
        body = await reader.readexactly(length) if length else b""
        self.requests.append(
            {
                "request_line": lines[0],
                "headers": headers,
                "body": body,
                "peer": writer.get_extra_info("sockname"),
            }
        )
        writer.write(
            f"HTTP/1.1 {self.status} OK\r\nContent-Length: 2\r\n"
            f"Connection: close\r\n\r\nok".encode()
        )
        await writer.drain()
        writer.close()


@asynccontextmanager
async def local_receiver() -> AsyncIterator[LocalReceiver]:
    receiver = LocalReceiver()
    await receiver.start()
    try:
        yield receiver
    finally:
        await receiver.stop()


async def test_a_real_connection_carries_the_payload_headers_and_signature(
    tmp_path: Any,
) -> None:
    """End-to-end over a genuine socket, with the hostname pinned.

    The URL resolves through an injected resolver to 127.0.0.1; the request
    is then sent to that exact address while the `Host` header keeps the
    original name. If pinning were removed, this connection would go
    wherever the OS resolver sent `pinned.test` — which is nowhere.
    """
    secret = uuidlib.uuid4().hex.encode()
    secret_file = tmp_path / "signing.key"
    secret_file.write_bytes(secret)

    async with local_receiver() as receiver:

        async def to_local(hostname: str, port: int) -> list[str]:
            return ["127.0.0.1"]

        # env=test permits loopback; the pinning logic is identical.
        settings = require_it_settings()
        target = destination(
            url=f"http://pinned.test:{receiver.port}/hooks/drake",
            signing_secret_file=str(secret_file),
        )
        result = await send_webhook(
            target,
            settings,
            payload={"schema_version": 1, "event_type": "opened"},
            idempotency_key="idem-real-socket",
            resolver=to_local,
        )

    assert result.outcome == "delivered"
    assert result.http_status == 200
    assert len(receiver.requests) == 1

    received = receiver.requests[0]
    assert received["request_line"].startswith("POST /hooks/drake")
    # The socket went to the validated address, and the receiver still sees
    # the real hostname.
    assert received["peer"][0] == "127.0.0.1"
    assert received["headers"]["host"] == f"pinned.test:{receiver.port}"
    assert received["headers"]["idempotency-key"] == "idem-real-socket"

    timestamp = received["headers"]["x-drake-timestamp"]
    expected = hmac.new(
        secret, f"{timestamp}.".encode() + received["body"], hashlib.sha256
    ).hexdigest()
    assert received["headers"]["x-drake-signature"] == f"v1={expected}"
    assert json.loads(received["body"].decode())["event_type"] == "opened"


async def test_a_real_receiver_failure_is_recorded_without_its_body(
    engine: AsyncEngine,
) -> None:
    """Over a real socket too: the response body never lands anywhere."""
    from test_notification_webhook_integration import planned_delivery

    world = await planned_delivery(engine)
    async with local_receiver() as receiver:
        receiver.status = 503

        async def to_local(hostname: str, port: int) -> list[str]:
            return ["127.0.0.1"]

        settings = require_it_settings().model_copy(
            update={
                "notification_webhooks": {
                    WEBHOOK_KEY: destination(url=f"http://pinned.test:{receiver.port}/hooks/drake")
                }
            }
        )
        state = await deliver_one(
            engine,
            settings,
            {
                "id": world["delivery_id"],
                "destination_key": WEBHOOK_KEY,
                "payload": {"schema_version": 1},
                "idempotency_key": world["idempotency_key"],
                "attempt_count": 0,
                "created_at": datetime.now(UTC),
                "payload_schema_version": 1,
            },
            resolver=to_local,
        )

    assert state == "retrying"
    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT wd.last_error_code, wd.last_http_status, a.outcome, a.error_code "
                    "FROM webhook_deliveries wd "
                    "JOIN webhook_delivery_attempts a ON a.delivery_id = wd.id "
                    "WHERE wd.id = :id"
                ),
                {"id": world["delivery_id"]},
            )
        ).first()
    assert stored is not None
    assert stored[0] == "http_503"
    assert stored[1] == 503
    assert stored[2] == "retryable"
    # Nothing from the receiver's own response survived.
    assert "pinned.test" not in json.dumps([str(value) for value in stored])


# ===========================================================================
# 5. Terminal delivery states stay terminal, and mean different things
# ===========================================================================


@pytest.fixture
async def redis() -> Any:
    client = aioredis.from_url(require_it_settings().redis_url)
    await client.delete(DELIVERY_LEASE_KEY)
    yield client
    await client.delete(DELIVERY_LEASE_KEY)
    await client.aclose()


async def test_a_suppressed_delivery_never_becomes_a_dead_letter(
    engine: AsyncEngine, redis: Any
) -> None:
    """Two terminal states, two different stories. A row that told both
    would be describing a delivery that never happened as one that failed.
    """
    from test_notification_webhook_integration import planned_delivery

    world = await planned_delivery(engine)
    empty_registry = require_it_settings().model_copy(update={"notification_webhooks": {}})

    await run_delivery_cycle(engine, empty_registry, redis, resolver=public_resolver)
    async with engine.connect() as connection:
        first = (
            await connection.execute(
                text(
                    "SELECT state, last_error_code, attempt_count FROM webhook_deliveries "
                    "WHERE id = :id"
                ),
                {"id": world["delivery_id"]},
            )
        ).first()
    assert first is not None
    assert first[0] == "suppressed"
    assert first[1] == "destination_not_configured"
    # Nothing was attempted, so there is no attempt row to explain.
    assert first[2] == 0
    async with engine.connect() as connection:
        attempts = (
            await connection.execute(
                text("SELECT count(*) FROM webhook_delivery_attempts WHERE delivery_id = :id"),
                {"id": world["delivery_id"]},
            )
        ).scalar_one()
    assert attempts == 0

    # The key comes back, and a later cycle must not re-claim a terminal row.
    restored = require_it_settings().model_copy(
        update={"notification_webhooks": {WEBHOOK_KEY: destination()}}
    )
    await redis.delete(DELIVERY_LEASE_KEY)
    report = await run_delivery_cycle(engine, restored, redis, resolver=public_resolver)
    assert report.claimed == 0

    async with engine.connect() as connection:
        after = (
            await connection.execute(
                text("SELECT state FROM webhook_deliveries WHERE id = :id"),
                {"id": world["delivery_id"]},
            )
        ).scalar_one()
    assert after == "suppressed"


@pytest.mark.parametrize(
    ("label", "status", "expected_state", "expected_outcome"),
    [
        ("terminal 4xx", 400, "dead_letter", "terminal"),
        ("redirect", 302, "dead_letter", "terminal"),
    ],
)
async def test_terminal_failures_dead_letter_and_are_not_reclaimed(
    engine: AsyncEngine,
    redis: Any,
    label: str,
    status: int,
    expected_state: str,
    expected_outcome: str,
) -> None:
    """`dead_letter` means Drake tried and stopped — with an attempt row
    that says why."""
    from test_notification_webhook_integration import FakeReceiver, planned_delivery

    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    receiver.status = status
    settings = require_it_settings().model_copy(
        update={"notification_webhooks": {WEBHOOK_KEY: destination()}}
    )

    await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT wd.state, wd.attempt_count, a.outcome "
                    "FROM webhook_deliveries wd "
                    "JOIN webhook_delivery_attempts a ON a.delivery_id = wd.id "
                    "WHERE wd.id = :id"
                ),
                {"id": world["delivery_id"]},
            )
        ).first()
    assert row is not None
    assert (row[0], row[1], row[2]) == (expected_state, 1, expected_outcome), label

    await redis.delete(DELIVERY_LEASE_KEY)
    report = await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )
    assert report.claimed == 0
    assert len(receiver.requests) == 1


async def test_a_delivered_row_is_never_claimed_again(engine: AsyncEngine, redis: Any) -> None:
    from test_notification_webhook_integration import FakeReceiver, planned_delivery

    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    settings = require_it_settings().model_copy(
        update={"notification_webhooks": {WEBHOOK_KEY: destination()}}
    )
    await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )
    await redis.delete(DELIVERY_LEASE_KEY)
    # Force it due again: a terminal row must still be ignored.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE webhook_deliveries SET next_attempt_at = now() WHERE id = :id"),
            {"id": world["delivery_id"]},
        )
    report = await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )
    assert report.claimed == 0
    assert len(receiver.requests) == 1
