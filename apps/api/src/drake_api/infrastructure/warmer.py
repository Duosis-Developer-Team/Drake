"""Keeps the infrastructure snapshots warm, so the Clusters and Resource
usage screens answer at once on their first open, not after a cold build.

One replica warms at a time (a Redis lease); the others skip the round.
Each round rebuilds only the snapshots about to stop being fresh, with the
same broker, budgets and leases a screen uses. Off unless an interval is
set (`DRAKE_INFRASTRUCTURE_WARM_SECONDS`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.incidents.system_actor import ensure_system_evaluator
from drake_api.infrastructure.service import InfrastructureService

logger = logging.getLogger("drake_api.infrastructure.warmer")

LEADER_KEY = "infrastructure:warmer:v1"


class InfrastructureWarmer:
    def __init__(
        self,
        engine: AsyncEngine,
        service: InfrastructureService,
        redis: aioredis.Redis,
        interval_seconds: int,
        extras: Sequence[Callable[[Any], Awaitable[int]]] = (),
    ) -> None:
        self._engine = engine
        self._service = service
        self._redis = redis
        self._interval = interval_seconds
        # Other reads warmed in the same round, as the same principal (the
        # error signals every overview, log and trace screen opens with).
        self._extras = tuple(extras)
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> int:
        """One round. Returns how many snapshots were rebuilt (0 when
        another replica holds the round)."""
        try:
            leader = bool(
                await self._redis.set(LEADER_KEY, "1", nx=True, ex=max(5, self._interval - 1))
            )
        except Exception:
            return 0
        if not leader:
            return 0
        principal = await ensure_system_evaluator(self._engine)
        rebuilt = await self._service.warm(principal)
        for extra in self._extras:
            try:
                rebuilt += await extra(principal)
            except Exception:
                logger.warning("infrastructure warmer: an extra read failed")
        return rebuilt

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("infrastructure warmer: a round failed")
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="infrastructure-warmer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
