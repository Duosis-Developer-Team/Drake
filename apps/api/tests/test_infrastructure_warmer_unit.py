"""The Clusters and Resource usage screens answer completely on their first
open: a partial snapshot is not held as fresh, and the warmer keeps the
snapshots fresh with one replica at a time."""

import json
import time
import uuid
from typing import Any

import pytest
from drake_api.infrastructure import service as service_module
from drake_api.infrastructure import warmer as warmer_module
from drake_api.infrastructure.service import FRESH_SECONDS, InfrastructureService
from drake_api.infrastructure.warmer import InfrastructureWarmer
from fakeredis import aioredis as fake_aioredis


async def _stored_at(redis: Any, cluster_id: uuid.UUID) -> float:
    raw = await redis.get(InfrastructureService._key(cluster_id))
    return float(json.loads(raw)["stored_at"])


async def test_a_partial_snapshot_is_stored_already_stale() -> None:
    redis = fake_aioredis.FakeRedis()
    service = InfrastructureService(None, None, redis)  # type: ignore[arg-type]
    cluster_id = uuid.uuid4()

    await service._write(cluster_id, {"state": "partial"})

    # The next read finds it stale and rebuilds it at once.
    assert time.time() - await _stored_at(redis, cluster_id) >= FRESH_SECONDS - 1


async def test_a_complete_snapshot_is_stored_fresh() -> None:
    redis = fake_aioredis.FakeRedis()
    service = InfrastructureService(None, None, redis)  # type: ignore[arg-type]
    cluster_id = uuid.uuid4()

    await service._write(cluster_id, {"state": "ok"})

    assert time.time() - await _stored_at(redis, cluster_id) < 5


def test_a_snapshot_outlives_its_fresh_window_by_hours() -> None:
    # A reader is answered from the last snapshot while a new one is built.
    assert service_module.KEEP_SECONDS >= 60 * 60


class _CountingService:
    def __init__(self) -> None:
        self.rounds = 0

    async def warm(self, principal: Any) -> int:
        self.rounds += 1
        return 1


async def test_one_replica_warms_a_round_and_the_others_skip_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def principal(engine: Any) -> str:
        return "system-evaluator"

    monkeypatch.setattr(warmer_module, "ensure_system_evaluator", principal)
    redis = fake_aioredis.FakeRedis()
    service = _CountingService()
    first = InfrastructureWarmer(None, service, redis, 45)  # type: ignore[arg-type]
    second = InfrastructureWarmer(None, service, redis, 45)  # type: ignore[arg-type]

    assert await first.run_once() == 1
    assert await second.run_once() == 0
    assert service.rounds == 1


async def test_the_warmer_is_off_unless_an_interval_is_set() -> None:
    from drake_api.settings import Settings

    assert Settings.model_fields["infrastructure_warm_seconds"].default == 0
