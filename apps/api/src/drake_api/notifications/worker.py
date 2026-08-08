"""The delivery worker: claim, send, classify, reschedule or give up.

Delivery is **at-least-once**. A crash between sending and recording the
result means the delivery is retried, and the receiver sees the same
request twice — which is exactly why every request carries a stable
`Idempotency-Key`. Pretending otherwise would require a distributed
transaction with the receiver, which does not exist.

Claiming uses `FOR UPDATE SKIP LOCKED` plus a time-bounded lease. The lock
keeps two workers out of the same row within a transaction; the lease is
what recovers a row whose worker died holding it.

Three terminal states, and they mean different things:

- `delivered` — the receiver accepted it.
- `dead_letter` — Drake tried and stopped. Either the receiver refused in
  a way retrying cannot fix (a terminal 4xx, a redirect, an SSRF refusal)
  or the bounded retry budget ran out.
- `suppressed` — Drake never tried, and never will: the destination key is
  no longer in the operator's registry, so there is nowhere to send it.

All three are final. A terminal delivery is never claimed again, and never
transitions to another terminal state — a row that went `suppressed` and
later showed up as `dead_letter` would describe two different stories about
the same delivery.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.notifications.planner import PlanReport, plan_pending
from drake_api.notifications.webhook import (
    AttemptResult,
    Resolver,
    next_backoff_seconds,
    send_webhook,
)
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.notifications.worker")

PLANNER_LEASE_KEY = "notifications:planner:cycle"
DELIVERY_LEASE_KEY = "notifications:delivery:cycle"

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass
class DeliveryReport:
    lease_acquired: bool = False
    claimed: int = 0
    delivered: int = 0
    retrying: int = 0
    dead_lettered: int = 0
    suppressed: int = 0


async def claim_due_deliveries(
    engine: AsyncEngine, worker_id: str, limit: int, claim_seconds: int
) -> list[dict[str, Any]]:
    """Take ownership of due work, and only work nobody else holds.

    `SKIP LOCKED` means a second worker running at the same moment picks up
    different rows instead of blocking on ours. The `locked_until` check is
    what lets a row whose worker crashed become claimable again.
    """
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT id FROM webhook_deliveries
                    WHERE state IN ('pending', 'retrying')
                      AND next_attempt_at <= :now
                      AND (locked_until IS NULL OR locked_until < :now)
                    ORDER BY next_attempt_at
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"now": now, "limit": limit},
            )
        ).all()
        ids = [row[0] for row in rows]
        if not ids:
            return []
        claimed = (
            await connection.execute(
                text(
                    """
                    UPDATE webhook_deliveries
                    SET state = 'processing',
                        locked_by = :worker,
                        locked_until = :until,
                        updated_at = now()
                    WHERE id = ANY(:ids)
                    RETURNING id, destination_key, payload, idempotency_key,
                              attempt_count, created_at, payload_schema_version
                    """
                ),
                {
                    "worker": worker_id,
                    "until": now + timedelta(seconds=claim_seconds),
                    "ids": ids,
                },
            )
        ).all()
    return [
        {
            "id": row[0],
            "destination_key": row[1],
            "payload": row[2],
            "idempotency_key": row[3],
            "attempt_count": row[4],
            "created_at": row[5],
            "payload_schema_version": row[6],
        }
        for row in claimed
    ]


async def _record_attempt(
    engine: AsyncEngine,
    delivery: dict[str, Any],
    result: AttemptResult,
    settings: Settings,
    started_at: datetime,
) -> str:
    """Write the attempt and the resulting delivery state, atomically."""
    attempt_number = delivery["attempt_count"] + 1
    now = datetime.now(UTC)
    elapsed = (now - delivery["created_at"]).total_seconds()

    if result.outcome == "delivered":
        state, retry_at = "delivered", None
    elif result.outcome in ("terminal", "refused"):
        state, retry_at = "dead_letter", None
    elif attempt_number >= settings.webhook_max_attempts:
        # The retry budget is spent. Continuing would turn the outbox into
        # a permanent backlog nobody looks at.
        state, retry_at = "dead_letter", None
    elif elapsed >= settings.webhook_max_elapsed_seconds:
        state, retry_at = "dead_letter", None
    else:
        state = "retrying"
        retry_at = now + timedelta(
            seconds=next_backoff_seconds(attempt_number, result.retry_after_seconds)
        )

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO webhook_delivery_attempts
                    (delivery_id, attempt_number, started_at, completed_at, outcome,
                     http_status, error_code, duration_ms, retry_at)
                VALUES (:delivery, :number, :started, :completed, :outcome,
                        :status, :error, :duration, :retry_at)
                ON CONFLICT (delivery_id, attempt_number) DO NOTHING
                """
            ),
            {
                "delivery": delivery["id"],
                "number": attempt_number,
                "started": started_at,
                "completed": now,
                "outcome": result.outcome,
                "status": result.http_status,
                "error": result.error_code,
                "duration": result.duration_ms,
                "retry_at": retry_at,
            },
        )
        await connection.execute(
            text(
                """
                UPDATE webhook_deliveries
                SET state = :state,
                    attempt_count = :attempts,
                    next_attempt_at = COALESCE(:retry_at, next_attempt_at),
                    delivered_at = CASE WHEN :state = 'delivered' THEN :now ELSE delivered_at END,
                    last_error_code = :error,
                    last_http_status = :status,
                    locked_by = NULL,
                    locked_until = NULL,
                    updated_at = now()
                WHERE id = :id
                  -- A terminal delivery stays terminal. Without this a
                  -- late attempt could move `suppressed` to `dead_letter`
                  -- and rewrite what happened.
                  AND state NOT IN ('delivered', 'dead_letter', 'suppressed')
                """
            ),
            {
                "id": delivery["id"],
                "state": state,
                "attempts": attempt_number,
                "retry_at": retry_at,
                "now": now,
                "error": result.error_code,
                "status": result.http_status,
            },
        )
    return state


async def _suppress(engine: AsyncEngine, delivery_id: uuid.UUID, code: str) -> None:
    """A destination the operator has removed from the registry.

    Terminal, and deliberately NOT `dead_letter`: nothing was attempted and
    nothing failed. `dead_letter` says "Drake tried and gave up", which
    would send someone looking for a receiver problem that does not exist.
    No attempt row is written either, because there was no attempt.
    """
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE webhook_deliveries
                SET state = 'suppressed', last_error_code = :code,
                    locked_by = NULL, locked_until = NULL, updated_at = now()
                WHERE id = :id
                  AND state NOT IN ('delivered', 'dead_letter', 'suppressed')
                """
            ),
            {"id": delivery_id, "code": code},
        )


async def deliver_one(
    engine: AsyncEngine,
    settings: Settings,
    delivery: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> str:
    """Send one claimed delivery and record what happened."""
    destination = settings.notification_webhooks.get(delivery["destination_key"])
    if destination is None:
        await _suppress(engine, delivery["id"], "destination_not_configured")
        return "suppressed"

    started_at = datetime.now(UTC)
    payload = delivery["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    result = await send_webhook(
        destination,
        settings,
        payload=payload,
        idempotency_key=delivery["idempotency_key"],
        transport=transport,
        resolver=resolver,
    )
    return await _record_attempt(engine, delivery, result, settings, started_at)


async def run_delivery_cycle(
    engine: AsyncEngine,
    settings: Settings,
    redis: aioredis.Redis,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> DeliveryReport:
    """One bounded sweep of due deliveries, guarded by a distributed lease."""
    report = DeliveryReport()
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(
            DELIVERY_LEASE_KEY, token, nx=True, ex=max(30, settings.webhook_claim_seconds)
        )
    except Exception:
        logger.warning("notification delivery: lease unavailable, skipping cycle")
        return report
    if not acquired:
        return report
    report.lease_acquired = True

    worker_id = f"worker-{token[:12]}"
    try:
        deliveries = await claim_due_deliveries(
            engine, worker_id, settings.webhook_worker_batch_size, settings.webhook_claim_seconds
        )
        report.claimed = len(deliveries)
        if not deliveries:
            return report

        global_limit = asyncio.Semaphore(max(1, settings.webhook_worker_concurrency))
        # One slow receiver must not consume the whole worker, so each
        # destination gets its own smaller ceiling as well.
        per_destination: dict[str, asyncio.Semaphore] = {}

        async def run(delivery: dict[str, Any]) -> str:
            key = delivery["destination_key"]
            gate = per_destination.setdefault(
                key, asyncio.Semaphore(max(1, settings.webhook_destination_concurrency))
            )
            async with global_limit, gate:
                try:
                    return await deliver_one(
                        engine, settings, delivery, transport=transport, resolver=resolver
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # One destination's failure is one delivery's failure.
                    # The claim lease expires and the row is retried.
                    logger.warning("notification delivery failed for one delivery")
                    return "error"

        outcomes = await asyncio.gather(*(run(delivery) for delivery in deliveries))
        for outcome in outcomes:
            if outcome == "delivered":
                report.delivered += 1
            elif outcome == "retrying":
                report.retrying += 1
            elif outcome == "dead_letter":
                report.dead_lettered += 1
            elif outcome == "suppressed":
                report.suppressed += 1
    finally:
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, DELIVERY_LEASE_KEY, token)
    return report


async def run_planner_cycle(
    engine: AsyncEngine, settings: Settings, redis: aioredis.Redis
) -> PlanReport:
    """One bounded planning sweep, guarded by its own lease."""
    report = PlanReport()
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(PLANNER_LEASE_KEY, token, nx=True, ex=120)
    except Exception:
        logger.warning("notification planner: lease unavailable, skipping cycle")
        return report
    if not acquired:
        return report
    try:
        return await plan_pending(
            engine,
            limit=settings.notification_planner_batch_size,
            base_url=settings.public_app_base_url,
        )
    finally:
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, PLANNER_LEASE_KEY, token)


class NotificationWorker:
    """Lifespan-owned loop for planning and delivery.

    Each half has its own flag: an operator can plan notifications into the
    in-app inbox without ever letting Drake call an external endpoint.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        redis: aioredis.Redis,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._redis = redis
        self._transport = transport
        self._planner_task: asyncio.Task[None] | None = None
        self._delivery_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return any(
            task is not None and not task.done()
            for task in (self._planner_task, self._delivery_task)
        )

    async def start(self) -> None:
        if self._settings.notification_planner_enabled and self._planner_task is None:
            self._planner_task = asyncio.create_task(
                self._loop(
                    lambda: run_planner_cycle(self._engine, self._settings, self._redis),
                    max(15.0, self._settings.notification_planner_interval_seconds),
                ),
                name="notification-planner",
            )
        if self._settings.webhook_worker_enabled and self._delivery_task is None:
            self._delivery_task = asyncio.create_task(
                self._loop(
                    lambda: run_delivery_cycle(
                        self._engine, self._settings, self._redis, transport=self._transport
                    ),
                    max(10.0, self._settings.webhook_worker_interval_seconds),
                ),
                name="notification-delivery",
            )

    async def stop(self) -> None:
        tasks = [self._planner_task, self._delivery_task]
        self._planner_task = self._delivery_task = None
        for task in tasks:
            if task is None:
                continue
            task.cancel()
            # Awaited, so each cycle's finally releases its lease before the
            # process exits.
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _loop(self, cycle: Any, interval: float) -> None:
        while True:
            try:
                await cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("notification cycle failed")
            await asyncio.sleep(interval)
