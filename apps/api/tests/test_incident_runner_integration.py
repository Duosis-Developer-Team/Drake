"""The evaluation runner: one cycle at a time, bounded, and interruptible.

Real Redis for the lease and a real database for the bindings; the health
orchestrator is a double, because what is under test here is the loop's
behaviour — not the verdicts, which have their own suites.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import redis.asyncio as aioredis
from drake_api.incidents.runner import LEASE_KEY, EvaluationRunner, run_cycle
from drake_api.incidents.system_actor import SYSTEM_ISSUER, ensure_system_evaluator
from drake_api.main import create_app
from drake_api.rbac.service import Principal
from harness_s1 import require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_incident_processor_integration import make_world
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]


@pytest.fixture
async def redis() -> Any:
    settings = require_it_settings()
    client = aioredis.from_url(settings.redis_url)
    await client.delete(LEASE_KEY)
    yield client
    await client.delete(LEASE_KEY)
    await client.aclose()


class FakeOrchestrator:
    """Answers `critical` for every binding, and can be made slow or broken."""

    def __init__(self) -> None:
        self.calls = 0
        self.in_flight = 0
        self.peak_in_flight = 0
        self.fail_for: set[uuid.UUID] = set()
        self.delay = 0.0
        self.started = asyncio.Event()
        self.hold = False

    async def load_context(self, binding_id: uuid.UUID) -> dict[str, Any]:
        return {
            "id": str(binding_id),
            "lifecycle": "active",
            "resolved": True,
            "revision": 1,
            "preset_key": "kubernetes.baseline.v1",
            "health_policy_key": "default.v1",
            "project_key": "pilot",
            "environment_key": "dev",
            "service_key": "api",
        }

    async def current_health(
        self, principal: Principal, context: dict[str, Any], *, refresh: bool = False
    ) -> dict[str, Any]:
        self.calls += 1
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        self.started.set()
        try:
            if self.hold:
                await asyncio.sleep(3600)
            if self.delay:
                await asyncio.sleep(self.delay)
            if uuid.UUID(context["id"]) in self.fail_for:
                raise RuntimeError("this binding could not be evaluated")
            return {
                "status": "critical",
                "reasons": ["no_ready_replicas"],
                "computed_at": datetime.now(UTC).isoformat(),
                "partial": False,
                "served_from_last_good": False,
            }
        finally:
            self.in_flight -= 1


async def test_the_lease_admits_one_cycle_at_a_time(
    engine: AsyncEngine, redis: Any
) -> None:
    """Two replicas ticking together must not both evaluate.

    Doubling provider load for no extra information is the mild version;
    the real cost is two workers racing on every state row.
    """
    await make_world(engine)
    orchestrator = FakeOrchestrator()
    orchestrator.delay = 0.2

    first, second = await asyncio.gather(
        run_cycle(engine, orchestrator, redis),  # type: ignore[arg-type]
        run_cycle(engine, orchestrator, redis),  # type: ignore[arg-type]
    )
    acquired = [report for report in (first, second) if report.lease_acquired]
    assert len(acquired) == 1
    assert acquired[0].evaluated == 1
    # The replica that lost did no work at all — not even a query.
    assert orchestrator.calls == 1


async def test_the_lease_is_released_so_the_next_cycle_can_run(
    engine: AsyncEngine, redis: Any
) -> None:
    await make_world(engine)
    orchestrator = FakeOrchestrator()

    first = await run_cycle(engine, orchestrator, redis)  # type: ignore[arg-type]
    second = await run_cycle(engine, orchestrator, redis)  # type: ignore[arg-type]

    assert first.lease_acquired and second.lease_acquired
    assert await redis.get(LEASE_KEY) is None


async def test_one_failing_binding_does_not_stop_the_cycle(
    engine: AsyncEngine, redis: Any
) -> None:
    worlds = [await make_world(engine) for _ in range(3)]
    orchestrator = FakeOrchestrator()
    orchestrator.fail_for = {worlds[1]["binding_id"]}

    report = await run_cycle(engine, orchestrator, redis)  # type: ignore[arg-type]

    assert report.failed == 1
    assert report.evaluated == 2
    # And the other two were really processed, not merely counted.
    async with engine.connect() as connection:
        states = (
            await connection.execute(
                text("SELECT count(*) FROM service_health_state WHERE binding_id = ANY(:ids)"),
                {"ids": [w["binding_id"] for w in worlds]},
            )
        ).scalar_one()
    assert states == 2


async def test_evaluation_concurrency_stays_within_its_bound(
    engine: AsyncEngine, redis: Any
) -> None:
    for _ in range(6):
        await make_world(engine)
    orchestrator = FakeOrchestrator()
    orchestrator.delay = 0.05

    report = await run_cycle(engine, orchestrator, redis, concurrency=2)  # type: ignore[arg-type]

    assert report.evaluated == 6
    assert orchestrator.peak_in_flight <= 2


async def test_the_batch_size_bounds_one_cycle(engine: AsyncEngine, redis: Any) -> None:
    for _ in range(5):
        await make_world(engine)
    orchestrator = FakeOrchestrator()

    report = await run_cycle(engine, orchestrator, redis, batch_size=2)  # type: ignore[arg-type]

    assert report.evaluated == 2
    assert orchestrator.calls == 2


async def test_only_active_resolved_bindings_are_evaluated(
    engine: AsyncEngine, redis: Any
) -> None:
    """A disabled or unresolved binding has nothing to say, so nothing is asked."""
    active = await make_world(engine)
    disabled = await make_world(engine)
    unresolved = await make_world(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE service_workload_bindings SET lifecycle='disabled' WHERE id=:b"),
            {"b": disabled["binding_id"]},
        )
        await connection.execute(
            text("UPDATE service_workload_bindings SET resolved_resource_uid=NULL WHERE id=:b"),
            {"b": unresolved["binding_id"]},
        )

    orchestrator = FakeOrchestrator()
    report = await run_cycle(engine, orchestrator, redis)  # type: ignore[arg-type]

    assert report.evaluated == 1
    async with engine.connect() as connection:
        rows = (
            await connection.execute(text("SELECT binding_id FROM service_health_state"))
        ).all()
    assert [row[0] for row in rows] == [active["binding_id"]]


async def test_shutdown_cancels_a_running_cycle_and_frees_the_lease(
    engine: AsyncEngine, redis: Any
) -> None:
    """Nothing outlives the process, and the next start is not blocked."""
    await make_world(engine)
    orchestrator = FakeOrchestrator()
    orchestrator.hold = True

    runner = EvaluationRunner(engine, orchestrator, redis, interval_seconds=15)  # type: ignore[arg-type]
    await runner.start()
    await asyncio.wait_for(orchestrator.started.wait(), timeout=5)
    await runner.stop()

    assert runner.running is False
    assert await redis.get(LEASE_KEY) is None


async def test_a_disabled_runner_is_not_created_and_queries_nothing() -> None:
    """The flag is the whole switch: off means no task and no lease."""
    settings = require_it_settings()
    app = create_app(settings.model_copy(update={"incident_runner_enabled": False}))
    assert app.state.incident_runner is None

    enabled = create_app(
        settings.model_copy(
            update={"incident_runner_enabled": True, "incident_runner_interval_seconds": 60.0}
        )
    )
    assert enabled.state.incident_runner is not None
    assert enabled.state.incident_runner.running is False


async def test_the_system_evaluator_holds_one_permission_and_cannot_log_in(
    engine: AsyncEngine,
) -> None:
    """A real identity with a real grant, rather than an authorization bypass."""
    principal = await ensure_system_evaluator(engine)
    # Idempotent: a second start does not create a second grant.
    await ensure_system_evaluator(engine)

    assert principal.issuer == SYSTEM_ISSUER
    async with engine.connect() as connection:
        permissions = [
            row[0]
            for row in (
                await connection.execute(
                    text(
                        """
                        SELECT DISTINCT rp.permission_key
                        FROM grants g
                        JOIN role_permissions rp ON rp.role_id = g.role_id
                        WHERE g.identity_id = :id AND g.revoked_at IS NULL
                        """
                    ),
                    {"id": principal.identity_id},
                )
            ).all()
        ]
        grants = (
            await connection.execute(
                text("SELECT count(*) FROM grants WHERE identity_id = :id"),
                {"id": principal.identity_id},
            )
        ).scalar_one()
        credentials = (
            await connection.execute(
                text("SELECT count(*) FROM local_credentials WHERE identity_id = :id"),
                {"id": principal.identity_id},
            )
        ).scalar_one()

    assert permissions == ["telemetry.query"]
    assert grants == 1
    # No local credential, and the issuer is a URN no provider can mint a
    # token for — so there is no path to a session as this identity.
    assert credentials == 0
    assert not SYSTEM_ISSUER.startswith("http")
