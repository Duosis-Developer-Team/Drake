"""Periodic, bounded evaluation of every active binding.

Incidents are produced here and nowhere else. Making a GET produce one
would mean the estate's incident history depended on who opened which page
— so the read endpoints stay reads, and this loop is the single writer.

What keeps it from being a liability:

- **One cycle at a time, across replicas.** A Redis lease with a unique
  token; a replica that crashes mid-cycle loses its lease when the TTL
  expires rather than blocking evaluation forever.
- **Bounded everywhere.** A batch limit, a concurrency limit, a per-cycle
  service budget, and an interval floor enforced in settings.
- **One service's failure is one service's failure.** A binding that
  raises is logged and skipped; the rest of the cycle continues.
- **Shutdown reaches the provider.** Cancellation propagates through the
  orchestrator into the broker, so no query outlives the process.

Nothing here logs a query expression, a datasource URL, or a credential.
"""

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.alert_rules.evaluator import record_and_evaluate
from drake_api.alert_rules.telemetry import TelemetrySampler
from drake_api.incidents.model import evaluation_from_health
from drake_api.incidents.processor import ProcessResult, process_evaluation
from drake_api.incidents.system_actor import ensure_system_evaluator
from drake_api.rbac.service import Principal
from drake_api.service_health.orchestrator import MAX_CONCURRENT_QUERIES, HealthOrchestrator
from drake_api.telemetry.budgets import PRINCIPAL_CONCURRENCY

logger = logging.getLogger("drake_api.incidents.runner")

LEASE_KEY = "incidents:evaluation:cycle"

# How many bindings one cycle evaluates at once. Every evaluation in a
# cycle runs as one principal, the system evaluator, and the broker lets one
# principal hold PRINCIPAL_CONCURRENCY queries. One evaluation already runs
# up to MAX_CONCURRENT_QUERIES of its own. Evaluating more bindings than
# that budget holds does not make a cycle faster: the queries still go
# through the same few leases. It only makes the broker refuse the
# overflow, and after the lease retries a refused query is a missing
# signal. In production (4 bindings x 4 queries against a budget of 4) that
# turned a random 3-5 healthy services "unknown / partial_result" in every
# cycle. Within the budget, a query waits for its own evaluation's
# semaphore, never for a refusal.
MAX_EVALUATION_CONCURRENCY = max(1, PRINCIPAL_CONCURRENCY // MAX_CONCURRENT_QUERIES)


def bounded_concurrency(requested: int) -> int:
    """A requested concurrency, held inside the principal's query budget."""
    return max(1, min(requested, MAX_EVALUATION_CONCURRENCY))


# Release only our own token. A blind DEL would let a slow replica delete
# the lease a faster one legitimately holds.
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# Extend only our own token. A cycle renews its lease before each binding,
# so the lease bounds how long a CRASHED replica can block evaluation, not
# how long a healthy cycle may take. A cycle held inside its query budget
# (MAX_EVALUATION_CONCURRENCY) runs at the provider's pace: 21 bindings took
# 104 s in production against a 120 s lease. Without renewal, a little more
# growth would let the lease expire mid-cycle and a second replica start a
# parallel cycle, doubling the provider load the budget exists to bound.
_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


@dataclass
class CycleReport:
    """What one cycle did. Counts only — no per-service detail leaves here."""

    lease_acquired: bool = False
    # The lease could not be renewed (another replica holds it, or Redis is
    # unreachable). The rest of the cycle was skipped rather than run
    # without knowing it was alone.
    lease_lost: bool = False
    evaluated: int = 0
    failed: int = 0
    incidents_opened: int = 0
    incidents_resolved: int = 0
    transitions: int = 0
    duplicates: int = 0


async def _due_bindings(engine: AsyncEngine, limit: int, after: uuid.UUID | None) -> list[Any]:
    """Active, resolved bindings — a bounded page, ordered by id.

    Only the columns an evaluation needs. A background loop reading whole
    rows it does not use is how a field ends up somewhere it should not be.
    """
    # The cursor is a separate clause rather than a nullable parameter:
    # Postgres cannot infer a type for `:after IS NULL` when it is NULL.
    cursor_clause = "AND b.id > :after" if after is not None else ""
    params: dict[str, Any] = {"limit": limit}
    if after is not None:
        params["after"] = after
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    f"""
                    SELECT b.id, b.environment_service_id, b.project_id,
                           b.environment_id, b.service_id
                    FROM service_workload_bindings b
                    WHERE b.lifecycle = 'active'
                      AND b.resolved_resource_uid IS NOT NULL
                      {cursor_clause}
                    ORDER BY b.id
                    LIMIT :limit
                    """  # noqa: S608 - the only variable fragment is fixed text
                ),
                params,
            )
        ).all()
    return list(rows)


async def evaluate_binding(
    engine: AsyncEngine,
    orchestrator: HealthOrchestrator,
    principal: Principal,
    binding_row: Any,
    telemetry: dict[str, float | None] | None = None,
) -> ProcessResult | None:
    """One binding: fresh evaluation, then the lifecycle decision.

    `refresh=True` bypasses the health verdict cache so a cycle is a real
    observation. The broker's own per-template cache still applies, which
    is intended: that one bounds provider load, and a verdict computed from
    a recent template response is still a new verdict.
    """
    binding_id = binding_row[0]
    context = await orchestrator.load_context(binding_id)
    if context is None:
        return None

    payload = await orchestrator.current_health(principal, context, refresh=True)
    catalog = {
        "environment_service_id": binding_row[1],
        "project_id": binding_row[2],
        "environment_id": binding_row[3],
        "service_id": binding_row[4],
    }
    # Alert rules (ADR-0031) are judged on this same verdict. A failure there
    # is logged and never stops the binding's own incident lifecycle below.
    try:
        await record_and_evaluate(
            engine,
            binding_id=binding_id,
            catalog=catalog,
            service_key=str(context.get("service_key") or ""),
            payload=payload,
            telemetry=telemetry,
            now=datetime.now(UTC),
        )
    except Exception:
        logger.warning("incident runner: alert rules could not be evaluated for a binding")
    evaluation = evaluation_from_health(
        payload,
        context,
        {
            "environment_service_id": binding_row[1],
            "project_id": binding_row[2],
            "environment_id": binding_row[3],
            "service_id": binding_row[4],
        },
    )
    return await process_evaluation(engine, evaluation)


async def run_cycle(
    engine: AsyncEngine,
    orchestrator: HealthOrchestrator,
    redis: aioredis.Redis,
    *,
    batch_size: int = 25,
    concurrency: int = MAX_EVALUATION_CONCURRENCY,
    lease_seconds: int = 120,
    principal: Principal | None = None,
    sampler: TelemetrySampler | None = None,
) -> CycleReport:
    """One bounded sweep, guarded by a distributed lease.

    Returns immediately with `lease_acquired=False` when another replica
    holds the lease — two replicas both evaluating would double the
    provider load and race on every state row for no benefit.
    """
    report = CycleReport()
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(LEASE_KEY, token, nx=True, ex=lease_seconds)
    except Exception:
        # No lease layer means no way to know we are alone. Skipping the
        # cycle is the safe answer; the next tick tries again.
        logger.warning("incident runner: lease unavailable, skipping cycle")
        return report
    if not acquired:
        return report
    report.lease_acquired = True

    actor = principal or await ensure_system_evaluator(engine)
    semaphore = asyncio.Semaphore(bounded_concurrency(concurrency))

    async def still_ours() -> bool:
        if report.lease_lost:
            return False
        try:
            renewed = await redis.eval(_RENEW, 1, LEASE_KEY, token, str(lease_seconds))
        except Exception:
            renewed = 0
        if not renewed:
            if not report.lease_lost:
                logger.warning("incident runner: lease lost mid-cycle, stopping the cycle")
            report.lease_lost = True
            return False
        return True

    async def evaluate(row: Any, telemetry: dict[str, float | None] | None) -> ProcessResult | None:
        async with semaphore:
            if not await still_ours():
                return None
            try:
                return await evaluate_binding(engine, orchestrator, actor, row, telemetry)
            except asyncio.CancelledError:
                raise
            except Exception:
                # One binding's failure is one binding's failure. The
                # message carries no query, no URL and no credential —
                # only that this binding could not be evaluated.
                logger.warning("incident runner: evaluation failed for a binding")
                report.failed += 1
                return None

    try:
        cursor: uuid.UUID | None = None
        remaining = batch_size
        while remaining > 0 and not report.lease_lost:
            page = await _due_bindings(engine, min(remaining, batch_size), cursor)
            if not page:
                break
            # Log and trace signals for the whole page in two statements
            # (ADR-0034). Without them a rule on them simply has no data.
            telemetry: dict[str, dict[str, float | None]] = {}
            if sampler is not None:
                try:
                    telemetry = await sampler.sample(
                        engine, [row[0] for row in page], datetime.now(UTC)
                    )
                except Exception:
                    logger.warning("incident runner: telemetry signals could not be read")
            results = await asyncio.gather(
                *(evaluate(row, telemetry.get(str(row[0]))) for row in page)
            )
            for result in results:
                if result is None:
                    continue
                report.evaluated += 1
                if result.duplicate:
                    report.duplicates += 1
                if result.transition_recorded:
                    report.transitions += 1
                if result.incident_opened is not None:
                    report.incidents_opened += 1
                if result.incident_resolved is not None:
                    report.incidents_resolved += 1
            cursor = page[-1][0]
            remaining -= len(page)
    finally:
        # Release our own token on every exit path, including cancellation,
        # so a shutdown does not leave the next cycle waiting out the TTL.
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, LEASE_KEY, token)
    return report


class EvaluationRunner:
    """Lifespan-owned loop. Started only when the feature flag is on.

    Modelled on the GitHub delivery recovery worker: a task the application
    owns, cancelled deterministically on shutdown, so nothing outlives the
    process that created it.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        orchestrator: HealthOrchestrator,
        redis: aioredis.Redis,
        *,
        interval_seconds: float = 60.0,
        batch_size: int = 25,
        concurrency: int = MAX_EVALUATION_CONCURRENCY,
        lease_seconds: int = 120,
        sampler: TelemetrySampler | None = None,
    ) -> None:
        self._engine = engine
        self._sampler = sampler
        self._orchestrator = orchestrator
        self._redis = redis
        # Floors, not suggestions: a misconfigured interval cannot turn
        # this into a tight loop against someone's Prometheus.
        self._interval = max(15.0, interval_seconds)
        self._batch = max(1, min(batch_size, 200))
        self._concurrency = bounded_concurrency(concurrency)
        self._lease = max(30, lease_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="incident-evaluation")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        # Awaited, not just cancelled: the cycle's finally releases the
        # lease and the broker's own cleanup runs before we return.
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await run_cycle(
                    self._engine,
                    self._orchestrator,
                    self._redis,
                    batch_size=self._batch,
                    concurrency=self._concurrency,
                    lease_seconds=self._lease,
                    sampler=self._sampler,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed cycle must not kill the loop; the next tick
                # retries and the lease has already been released.
                logger.warning("incident runner: cycle failed")
            await asyncio.sleep(self._interval)
