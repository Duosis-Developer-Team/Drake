"""Periodic deployment ingest.

Reads inventory the cluster agent already reported and records revisions.
It makes no Kubernetes call of its own — the agent remains the only thing
that talks to a cluster, and this only interprets what it sent.

Off by default and leased, like every other background actor here: one
cycle at a time across replicas, bounded batch, and a failure in one
workload costs only that workload.
"""

import asyncio
import contextlib
import logging
import uuid
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.deployments.health import compare_health, store_comparison
from drake_api.deployments.ingest import IngestReport, ingest_deployments
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.deployments.worker")

INGEST_LEASE_KEY = "deployments:ingest:cycle"

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


async def run_ingest_cycle(
    engine: AsyncEngine, settings: Settings, redis: aioredis.Redis
) -> IngestReport:
    """One bounded ingest pass, guarded by a distributed lease."""
    report = IngestReport()
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(INGEST_LEASE_KEY, token, nx=True, ex=300)
    except Exception:
        logger.warning("deployment ingest: lease unavailable, skipping cycle")
        return report
    if not acquired:
        return report
    try:
        return await ingest_deployments(
            engine, limit=settings.deployment_ingest_batch_size
        )
    finally:
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, INGEST_LEASE_KEY, token)


async def compare_pending(
    engine: AsyncEngine, broker: Any, principal: Any, limit: int = 20
) -> int:
    """Compute health comparisons for completed rollouts that lack one.

    Only for finished rollouts: comparing "after" while the rollout is
    still in flight would measure a half-deployed service and call the
    result a regression.
    """
    from sqlalchemy import text

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT d.id, d.binding_id, d.environment_service_id,
                           d.rollout_started_at
                    FROM deployment_revisions d
                    LEFT JOIN deployment_health_comparisons hc
                           ON hc.deployment_revision_id = d.id
                    WHERE hc.deployment_revision_id IS NULL
                      AND d.rollout_completed_at IS NOT NULL
                      -- The "after" window must have closed, or the
                      -- comparison would be reading a window that is still
                      -- filling.
                      AND d.rollout_completed_at < now() - interval '35 minutes'
                    ORDER BY d.rollout_completed_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
        ).all()

    computed = 0
    for row in rows:
        try:
            comparison = await compare_health(
                engine,
                broker,
                principal,
                binding_id=row[1],
                environment_service_id=row[2],
                rollout_at=row[3],
            )
            await store_comparison(engine, row[0], comparison)
            computed += 1
        except Exception:
            logger.warning("deployment health comparison failed for one revision")
    return computed


class DeploymentIngestWorker:
    """Lifespan-owned loop. Absent unless the flag is on."""

    def __init__(
        self, engine: AsyncEngine, settings: Settings, redis: aioredis.Redis
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._redis = redis
        self._interval = max(30.0, settings.deployment_ingest_interval_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="deployment-ingest")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await run_ingest_cycle(self._engine, self._settings, self._redis)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("deployment ingest cycle failed")
            await asyncio.sleep(self._interval)
