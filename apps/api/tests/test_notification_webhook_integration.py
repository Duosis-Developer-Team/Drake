"""Webhook delivery: the SSRF boundary, the signature, retry and dead-letter.

Everything here goes through the real send path. Only the network is a
local fake: an `httpx.MockTransport` receiver that records what arrived and
can be told to fail in specific ways. No request in this suite reaches a
real host, and no real destination URL or credential exists anywhere in it.
"""

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from drake_api.notifications.webhook import (
    DestinationRefusedError,
    classify_status,
    next_backoff_seconds,
    parse_retry_after,
    send_webhook,
    validate_destination,
)
from drake_api.notifications.worker import (
    DELIVERY_LEASE_KEY,
    claim_due_deliveries,
    deliver_one,
    run_delivery_cycle,
)
from drake_api.settings import WebhookDestination
from harness_s1 import require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_notification_planner_integration import (
    WEBHOOK_KEY,
    attach,
    make_destination,
    make_policy,
    open_incident_world,
)
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

# A name that never resolves and is never dialled: the MockTransport
# intercepts, and the resolver below is injected.
RECEIVER_URL = "https://receiver.invalid/hooks/drake"
# A genuinely global address. Never dialled: resolution is injected and the
# transport is a MockTransport, so no packet leaves the test process.
# (Documentation ranges like TEST-NET-3 are `is_private` in Python, which
# would make the public/private distinction under test meaningless.)
PUBLIC_ADDRESS = "93.184.216.34"


async def public_resolver(hostname: str, port: int) -> list[str]:
    return [PUBLIC_ADDRESS]


class FakeReceiver:
    """A local webhook endpoint that records requests and can misbehave."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self.status = 200
        self.retry_after: str | None = None
        self.raise_timeout = False
        self.location: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_timeout:
            raise httpx.ConnectTimeout("receiver is slow")
        self.bodies.append(json.loads(request.content.decode()))
        headers = {}
        if self.retry_after:
            headers["Retry-After"] = self.retry_after
        if self.location:
            headers["Location"] = self.location
        # A body the receiver would consider private. Nothing in Drake may
        # keep or surface it.
        return httpx.Response(
            self.status, headers=headers, json={"internal": "receiver-private-detail"}
        )

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def destination(**overrides: Any) -> WebhookDestination:
    base: dict[str, Any] = {"url": RECEIVER_URL, "display_name": "Ops primary"}
    base.update(overrides)
    return WebhookDestination(**base)


def settings_with(receiver_destination: WebhookDestination, **overrides: Any):
    base = require_it_settings().model_copy(
        update={"notification_webhooks": {WEBHOOK_KEY: receiver_destination}, **overrides}
    )
    return base


@pytest.fixture
async def redis() -> Any:
    client = aioredis.from_url(require_it_settings().redis_url)
    await client.delete(DELIVERY_LEASE_KEY)
    yield client
    await client.delete(DELIVERY_LEASE_KEY)
    await client.aclose()


async def planned_delivery(engine: AsyncEngine) -> dict[str, Any]:
    """A world with one pending webhook delivery, produced by the planner."""
    from drake_api.notifications.planner import plan_pending

    world = await open_incident_world(engine)
    policy = await make_policy(engine, world)
    await attach(
        engine,
        policy,
        await make_destination(engine, world, destination_type="webhook", key=WEBHOOK_KEY),
    )
    await plan_pending(engine, base_url="https://drake.example.test")
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT id, state, idempotency_key FROM webhook_deliveries "
                    "WHERE incident_id = :i"
                ),
                {"i": world["incident_id"]},
            )
        ).first()
    assert row is not None
    return {**world, "delivery_id": row[0], "idempotency_key": row[2]}


async def delivery_row(engine: AsyncEngine, delivery_id: uuid.UUID) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT state, attempt_count, last_error_code, last_http_status, "
                    "next_attempt_at, delivered_at FROM webhook_deliveries WHERE id = :id"
                ),
                {"id": delivery_id},
            )
        ).first()


async def attempts_for(engine: AsyncEngine, delivery_id: uuid.UUID) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    text(
                        "SELECT attempt_number, outcome, http_status, error_code, duration_ms "
                        "FROM webhook_delivery_attempts WHERE delivery_id = :id "
                        "ORDER BY attempt_number"
                    ),
                    {"id": delivery_id},
                )
            ).all()
        )


# --- the SSRF boundary ----------------------------------------------------


@pytest.mark.parametrize(
    ("label", "address"),
    [
        ("loopback", "127.0.0.1"),
        ("private", "10.0.0.5"),
        ("link-local metadata", "169.254.169.254"),
        ("multicast", "224.0.0.1"),
        ("unspecified", "0.0.0.0"),  # noqa: S104 - a refused target, not a bind address
    ],
)
async def test_dangerous_targets_are_refused_at_send_time(label: str, address: str) -> None:
    """Re-checked on every send, because DNS answers change.

    169.254.169.254 is the one that matters most: it is the cloud metadata
    endpoint, and reaching it is how an SSRF becomes a credential theft.
    """
    settings = require_it_settings().model_copy(update={"env": "prod"})

    async def resolver(hostname: str, port: int) -> list[str]:
        return [address]

    with pytest.raises(DestinationRefusedError):
        await validate_destination(destination(), settings, resolver)


async def test_a_name_answering_public_and_private_is_refused() -> None:
    """The shape of a rebinding attack, not of a healthy endpoint.

    Checked with `allow_private` ON, which is exactly when this rule earns
    its keep: with it off, the private address is refused outright and the
    mixed answer never gets that far.
    """
    settings = require_it_settings().model_copy(update={"env": "prod"})

    async def mixed(hostname: str, port: int) -> list[str]:
        return [PUBLIC_ADDRESS, "10.1.2.3"]

    with pytest.raises(DestinationRefusedError) as error:
        await validate_destination(destination(allow_private=True), settings, mixed)
    assert error.value.code == "destination_mixed_answers_refused"

    # And with the opt-in absent it is refused for the blunter reason.
    with pytest.raises(DestinationRefusedError) as strict:
        await validate_destination(destination(), settings, mixed)
    assert strict.value.code == "destination_private_refused"


@pytest.mark.parametrize(
    ("label", "url"),
    [
        ("plaintext", "http://receiver.invalid/hook"),
        ("credentials in url", "https://user:pass@receiver.invalid/hook"),
        ("non-http scheme", "file:///etc/passwd"),
        ("fragment", "https://receiver.invalid/hook#x"),
    ],
)
async def test_malformed_or_unsafe_urls_are_refused(label: str, url: str) -> None:
    settings = require_it_settings().model_copy(update={"env": "prod"})
    with pytest.raises(DestinationRefusedError):
        await validate_destination(destination(url=url), settings, public_resolver)


async def test_a_refused_target_is_terminal_and_never_dialled(
    engine: AsyncEngine,
) -> None:
    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    settings = settings_with(destination(url="https://receiver.invalid/hook")).model_copy(
        update={"env": "prod"}
    )

    async def private(hostname: str, port: int) -> list[str]:
        return ["10.0.0.9"]

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
        transport=receiver.transport,
        resolver=private,
    )

    assert state == "dead_letter"
    assert receiver.requests == []  # no connection was attempted
    attempts = await attempts_for(engine, world["delivery_id"])
    assert attempts[0][1] == "refused"
    assert attempts[0][3] == "destination_private_refused"


# --- a real delivery -------------------------------------------------------


async def test_a_delivery_sends_the_planned_payload_and_marks_delivered(
    engine: AsyncEngine, redis: Any
) -> None:
    """The whole path: planned row → signed request → delivered state."""
    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    settings = settings_with(destination())

    report = await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )

    assert report.lease_acquired is True
    assert report.delivered == 1
    assert len(receiver.requests) == 1

    request = receiver.requests[0]
    assert request.method == "POST"
    assert request.headers["Idempotency-Key"] == world["idempotency_key"]
    assert request.headers["Content-Type"] == "application/json"

    body = receiver.bodies[0]
    assert body["schema_version"] == 1
    assert body["event_type"] == "opened"
    assert body["idempotency_key"] == world["idempotency_key"]
    assert body["incident"]["severity"] == "critical"
    assert body["incident"]["url"].startswith("https://drake.example.test/incidents/")

    row = await delivery_row(engine, world["delivery_id"])
    assert row[0] == "delivered"
    assert row[1] == 1
    assert row[5] is not None
    attempts = await attempts_for(engine, world["delivery_id"])
    assert [(a[1], a[2]) for a in attempts] == [("delivered", 200)]


async def test_the_signature_is_verifiable_and_covers_the_timestamp(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    """A receiver can verify it, and a captured request cannot be replayed."""
    world = await planned_delivery(engine)
    secret_file = tmp_path / "signing.key"
    # Generated here, never committed: the repository contains no secret.
    secret = uuid.uuid4().hex.encode()
    secret_file.write_bytes(secret)

    receiver = FakeReceiver()
    settings = settings_with(destination(signing_secret_file=str(secret_file)))
    await deliver_one(
        engine,
        settings,
        {
            "id": world["delivery_id"],
            "destination_key": WEBHOOK_KEY,
            "payload": {"schema_version": 1, "event_type": "opened"},
            "idempotency_key": world["idempotency_key"],
            "attempt_count": 0,
            "created_at": datetime.now(UTC),
            "payload_schema_version": 1,
        },
        transport=receiver.transport,
        resolver=public_resolver,
    )

    request = receiver.requests[0]
    signature = request.headers["X-Drake-Signature"]
    timestamp = request.headers["X-Drake-Timestamp"]
    assert signature.startswith("v1=")

    expected = hmac.new(
        secret, f"{timestamp}.".encode() + request.content, hashlib.sha256
    ).hexdigest()
    assert signature == f"v1={expected}"
    # The timestamp is inside the signed material, so replaying an old body
    # with a new timestamp fails verification.
    forged = hmac.new(
        secret, f"{int(timestamp) + 60}.".encode() + request.content, hashlib.sha256
    ).hexdigest()
    assert forged != expected


async def test_the_secret_never_appears_in_a_request_or_a_record(
    engine: AsyncEngine, tmp_path: Any
) -> None:
    world = await planned_delivery(engine)
    secret_file = tmp_path / "signing.key"
    secret = uuid.uuid4().hex
    secret_file.write_text(secret)

    receiver = FakeReceiver()
    settings = settings_with(destination(signing_secret_file=str(secret_file)))
    await deliver_one(
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
        transport=receiver.transport,
        resolver=public_resolver,
    )

    request = receiver.requests[0]
    assert secret not in request.content.decode()
    assert secret not in json.dumps(dict(request.headers))

    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT wd.payload::text, wd.last_error_code, a.error_code "
                    "FROM webhook_deliveries wd "
                    "LEFT JOIN webhook_delivery_attempts a ON a.delivery_id = wd.id "
                    "WHERE wd.id = :id"
                ),
                {"id": world["delivery_id"]},
            )
        ).first()
    serialized = json.dumps([str(value) for value in stored])
    assert secret not in serialized
    # And no trace of the receiver's own response body either.
    assert "receiver-private-detail" not in serialized
    assert RECEIVER_URL not in serialized


# --- classification --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (200, "delivered"),
        (204, "delivered"),
        (301, "terminal"),
        (302, "terminal"),
        (400, "terminal"),
        (401, "terminal"),
        (404, "terminal"),
        (408, "retryable"),
        (429, "retryable"),
        (500, "retryable"),
        (503, "retryable"),
    ],
)
def test_status_classification(status: int, outcome: str) -> None:
    assert classify_status(status)[0] == outcome


def test_a_redirect_is_terminal_rather_than_followed() -> None:
    """Following one would send a validated endpoint's traffic elsewhere."""
    outcome, code = classify_status(302)
    assert outcome == "terminal"
    assert code == "destination_redirect_refused"


def test_retry_after_is_honoured_only_when_small_and_numeric() -> None:
    assert parse_retry_after("30") == 30
    assert parse_retry_after("100000") == 300  # clamped, not obeyed
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert parse_retry_after("-5") is None
    assert parse_retry_after(None) is None


def test_backoff_grows_and_stays_bounded() -> None:
    delays = [next_backoff_seconds(attempt) for attempt in range(1, 7)]
    assert delays == sorted(delays)
    assert delays[0] >= 30
    assert max(delays) <= 3600 * 1.25


async def test_a_redirect_response_is_not_followed(engine: AsyncEngine) -> None:
    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    receiver.status = 302
    receiver.location = "https://elsewhere.invalid/hook"
    settings = settings_with(destination())

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
        transport=receiver.transport,
        resolver=public_resolver,
    )
    assert state == "dead_letter"
    # One request only — the redirect was not chased.
    assert len(receiver.requests) == 1


# --- retry and dead-letter -------------------------------------------------


async def test_a_transient_failure_is_retried_then_dead_lettered(
    engine: AsyncEngine, redis: Any
) -> None:
    """Bounded: six attempts, then the delivery stops costing anything."""
    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    receiver.status = 503
    settings = settings_with(destination())

    for _ in range(settings.webhook_max_attempts):
        # Due immediately, so the whole budget can be exercised without
        # waiting out real backoff. The lease is cleared too: it is tested
        # on its own, and a leftover one here would silently skip a cycle.
        await redis.delete(DELIVERY_LEASE_KEY)
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE webhook_deliveries SET next_attempt_at = now() WHERE id = :id"),
                {"id": world["delivery_id"]},
            )
        await run_delivery_cycle(
            engine, settings, redis, transport=receiver.transport, resolver=public_resolver
        )

    row = await delivery_row(engine, world["delivery_id"])
    assert row[0] == "dead_letter"
    assert row[1] == settings.webhook_max_attempts
    assert row[3] == 503
    attempts = await attempts_for(engine, world["delivery_id"])
    assert len(attempts) == settings.webhook_max_attempts
    assert {a[1] for a in attempts} == {"retryable"}
    assert len(receiver.requests) == settings.webhook_max_attempts


async def test_a_terminal_status_is_not_retried(engine: AsyncEngine, redis: Any) -> None:
    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    receiver.status = 400
    settings = settings_with(destination())

    await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )
    row = await delivery_row(engine, world["delivery_id"])
    assert row[0] == "dead_letter"
    assert row[1] == 1

    # A second cycle must not pick it up again.
    await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )
    assert len(receiver.requests) == 1


async def test_a_timeout_is_retryable_and_records_a_safe_code(
    engine: AsyncEngine, redis: Any
) -> None:
    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    receiver.raise_timeout = True
    settings = settings_with(destination())

    report = await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )
    assert report.retrying == 1
    row = await delivery_row(engine, world["delivery_id"])
    assert row[0] == "retrying"
    assert row[2] in ("timeout", "connect_failed")
    assert row[4] > datetime.now(UTC)  # scheduled into the future


async def test_a_removed_destination_is_suppressed_rather_than_retried(
    engine: AsyncEngine, redis: Any
) -> None:
    """There is nowhere to send it; saying so beats retrying into a void."""
    world = await planned_delivery(engine)
    receiver = FakeReceiver()
    settings = require_it_settings().model_copy(update={"notification_webhooks": {}})

    await run_delivery_cycle(
        engine, settings, redis, transport=receiver.transport, resolver=public_resolver
    )
    row = await delivery_row(engine, world["delivery_id"])
    assert row[0] == "suppressed"
    assert row[2] == "destination_not_configured"
    assert receiver.requests == []


# --- claiming and concurrency ---------------------------------------------


async def test_two_workers_never_claim_the_same_delivery(engine: AsyncEngine) -> None:
    """`FOR UPDATE SKIP LOCKED`: the second worker takes different rows."""
    for _ in range(4):
        await planned_delivery(engine)

    first, second = await asyncio.gather(
        claim_due_deliveries(engine, "worker-a", 4, 60),
        claim_due_deliveries(engine, "worker-b", 4, 60),
    )
    claimed = [row["id"] for row in first] + [row["id"] for row in second]
    assert len(claimed) == len(set(claimed)) == 4


async def test_an_expired_claim_is_recoverable(engine: AsyncEngine) -> None:
    """A worker that died holding a row must not strand it forever."""
    world = await planned_delivery(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE webhook_deliveries
                SET state = 'retrying', locked_by = 'dead-worker',
                    locked_until = :past, next_attempt_at = now()
                WHERE id = :id
                """
            ),
            {"id": world["delivery_id"], "past": datetime.now(UTC) - timedelta(minutes=5)},
        )

    claimed = await claim_due_deliveries(engine, "worker-new", 5, 60)
    assert [row["id"] for row in claimed] == [world["delivery_id"]]


async def test_a_held_claim_is_not_stolen(engine: AsyncEngine) -> None:
    world = await planned_delivery(engine)
    held = await claim_due_deliveries(engine, "worker-a", 5, 300)
    assert [row["id"] for row in held] == [world["delivery_id"]]

    # Still leased: a second worker sees nothing due.
    assert await claim_due_deliveries(engine, "worker-b", 5, 300) == []


async def test_the_lease_admits_one_delivery_cycle_at_a_time(
    engine: AsyncEngine, redis: Any
) -> None:
    await planned_delivery(engine)
    receiver = FakeReceiver()
    settings = settings_with(destination())

    reports = await asyncio.gather(
        run_delivery_cycle(
            engine, settings, redis, transport=receiver.transport, resolver=public_resolver
        ),
        run_delivery_cycle(
            engine, settings, redis, transport=receiver.transport, resolver=public_resolver
        ),
    )
    assert sum(1 for report in reports if report.lease_acquired) == 1
    assert len(receiver.requests) == 1


async def test_the_disabled_worker_makes_no_network_call() -> None:
    """The flags are the whole switch."""
    from drake_api.main import create_app

    settings = require_it_settings()
    app = create_app(
        settings.model_copy(
            update={"notification_planner_enabled": False, "webhook_worker_enabled": False}
        )
    )
    assert app.state.notification_worker is None

    enabled = create_app(settings.model_copy(update={"notification_planner_enabled": True}))
    assert enabled.state.notification_worker is not None
    assert enabled.state.notification_worker.running is False


async def test_send_webhook_never_raises_for_a_delivery_failure() -> None:
    """Failures are classified, not thrown: a raise would lose the attempt."""
    receiver = FakeReceiver()
    receiver.raise_timeout = True
    result = await send_webhook(
        destination(),
        settings_with(destination()),
        payload={"schema_version": 1},
        idempotency_key="k",
        transport=receiver.transport,
        resolver=public_resolver,
    )
    assert result.outcome == "retryable"
    assert result.error_code in ("timeout", "connect_failed")
