"""Reconciliation-job ownership: leases, fencing, and attempt accounting.

CTO fix gate 4, finding 5. Every test here failed on `8e47f13`.

Claiming fifty jobs at once for five minutes and then running them one
after another is not bounded ownership: the last job in the batch may sit
unstarted until long after its lease was taken, and a second worker that
arrives once the lease expires will happily take a job the first worker is
still about to run.
"""

import asyncio
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import (
    INSTALLATION_ID,
    _seed_admin,
    deliver,
    github_harness,
    installation_payload,
)

pytestmark = pytest.mark.integration

OWNER = "Duosis-Developer-Team"


def _oversized(count: int = 100, start: int = 960_000) -> list[dict[str, Any]]:
    """A payload big enough to be truncated, which queues a job."""
    return [
        {
            "id": start + index,
            "node_id": "R_" + "k" * 126,
            "name": "r" * 200,
            "full_name": f"{OWNER}/" + "r" * (250 - len(OWNER)),
            "private": True,
        }
        for index in range(count)
    ]


async def _queue_jobs(harness: Any, count: int) -> None:
    """Queue `count` distinct installation jobs by truncating deliveries."""
    async with harness.api_client() as client:
        for index in range(count):
            payload = installation_payload(repositories=_oversized(start=960_000 + index * 200))
            payload["installation"]["id"] = INSTALLATION_ID + index
            response = await deliver(client, "installation", payload, str(uuidlib.uuid4()))
            assert response.status_code == 202, response.text


async def _jobs(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT installation_external_id, status, attempts, lease_owner, "
                    "lease_expires_at, lease_generation FROM github_reconciliation_jobs "
                    "ORDER BY created_at"
                )
            )
        ).all()
    return [
        {
            "installation": row[0],
            "status": row[1],
            "attempts": row[2],
            "lease_owner": row[3],
            "lease_expires_at": row[4],
            "lease_generation": row[5],
        }
        for row in rows
    ]


async def _audits(engine: AsyncEngine, action: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE action = :a"), {"a": action}
                )
            ).scalar_one()
        )


class _SlowReconciler:
    """Stands in for a reconciler whose provider round trip takes a while."""

    def __init__(self, delay: float, log: list[int]) -> None:
        self._delay = delay
        self.log = log
        self.active = 0
        self.max_active = 0

    async def reconcile_installation(
        self, installation_external_id: int, *, scope_id: uuidlib.UUID
    ) -> Any:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.log.append(installation_external_id)
        try:
            await asyncio.sleep(self._delay)
        finally:
            self.active -= 1
        return service.InstallationSync(
            installation_external_id=installation_external_id,
            state="active",
            present=0,
            removed=0,
        )


async def test_a_slow_job_is_not_stolen_while_its_owner_is_still_working(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A short lease plus a slow job is the exact race that matters.

    If ownership is a one-shot stamp taken at claim time, the lease lapses
    while the first worker is still inside the provider call and a second
    worker runs the same job against GitHub.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _queue_jobs(harness, 1)

    log: list[int] = []
    slow = _SlowReconciler(delay=1.2, log=log)
    fast = _SlowReconciler(delay=0.0, log=log)

    await asyncio.gather(
        service.drain_reconciliation_jobs(engine, slow, lease_seconds=1),
        _delayed(0.4, service.drain_reconciliation_jobs(engine, fast, lease_seconds=1)),
        return_exceptions=True,
    )
    assert log.count(INSTALLATION_ID) == 1, (
        f"the job was executed {log.count(INSTALLATION_ID)} times against the provider"
    )


async def _delayed(seconds: float, coro: Any) -> Any:
    await asyncio.sleep(seconds)
    return await coro


async def test_a_later_job_in_a_batch_is_not_left_unprotected(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Claiming a batch up front means the last job's lease is burning
    while the first job is still running."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _queue_jobs(harness, 3)

    log: list[int] = []
    slow = _SlowReconciler(delay=0.7, log=log)
    fast = _SlowReconciler(delay=0.0, log=log)

    await asyncio.gather(
        service.drain_reconciliation_jobs(engine, slow, lease_seconds=1),
        _delayed(0.9, service.drain_reconciliation_jobs(engine, fast, lease_seconds=1)),
        return_exceptions=True,
    )
    for installation in {INSTALLATION_ID, INSTALLATION_ID + 1, INSTALLATION_ID + 2}:
        assert log.count(installation) <= 1, (
            f"installation {installation} was reconciled {log.count(installation)} times"
        )


async def test_a_crash_after_claiming_still_spends_an_attempt(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Otherwise a job that kills its worker is retried without limit."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _queue_jobs(harness, 1)

    class _Crashing:
        async def reconcile_installation(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("worker died mid-job")

    await service.drain_reconciliation_jobs(engine, _Crashing(), lease_seconds=1)
    assert (await _jobs(engine))[0]["attempts"] == 1


async def test_repeated_crashes_reach_a_terminal_failed_state(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _queue_jobs(harness, 1)
    audits_before = await _audits(engine, "github.reconciliation.exhausted")

    class _Crashing:
        async def reconcile_installation(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("permanent")

    for _ in range(service.MAX_DELIVERY_ATTEMPTS + 3):
        await service.drain_reconciliation_jobs(engine, _Crashing(), lease_seconds=0)

    job = (await _jobs(engine))[0]
    assert job["status"] == "failed"
    assert job["attempts"] == service.MAX_DELIVERY_ATTEMPTS
    assert await _audits(engine, "github.reconciliation.exhausted") == audits_before + 1


async def test_a_successful_run_spends_exactly_one_attempt(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _queue_jobs(harness, 1)

    log: list[int] = []
    completed = await service.drain_reconciliation_jobs(
        engine, _SlowReconciler(0.0, log), lease_seconds=60
    )
    assert completed == 1
    job = (await _jobs(engine))[0]
    assert job["status"] == "processed"
    assert job["attempts"] == 1, "success must not double-count the attempt"


async def test_a_worker_that_lost_its_lease_commits_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The fencing check: finishing is only allowed for the current owner."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _queue_jobs(harness, 1)

    stolen = asyncio.Event()

    class _StolenMidway:
        async def reconcile_installation(self, *args: Any, **kwargs: Any) -> Any:
            # Someone else takes the job while we are inside the provider.
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE github_reconciliation_jobs "
                        "SET lease_owner = 'somebody-else', "
                        "    lease_generation = lease_generation + 1, "
                        "    lease_expires_at = now() + interval '5 minutes' "
                        "WHERE status = 'pending'"
                    )
                )
            stolen.set()
            return service.InstallationSync(
                installation_external_id=INSTALLATION_ID,
                state="active",
                present=0,
                removed=0,
            )

    completed = await service.drain_reconciliation_jobs(engine, _StolenMidway(), lease_seconds=60)
    assert stolen.is_set()
    assert completed == 0, "a worker that lost its lease must not report completion"
    assert (await _jobs(engine))[0]["status"] == "pending"


async def test_two_racing_workers_produce_one_logical_execution(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _queue_jobs(harness, 1)

    log: list[int] = []
    left = _SlowReconciler(0.2, log)
    right = _SlowReconciler(0.2, log)
    results = await asyncio.gather(
        service.drain_reconciliation_jobs(engine, left, lease_seconds=60),
        service.drain_reconciliation_jobs(engine, right, lease_seconds=60),
        return_exceptions=True,
    )
    assert log.count(INSTALLATION_ID) == 1
    assert sum(r for r in results if isinstance(r, int)) == 1


async def test_a_terminal_job_is_never_reclaimed(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _queue_jobs(harness, 1)
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE github_reconciliation_jobs SET status = 'failed'"))
    log: list[int] = []
    assert (
        await service.drain_reconciliation_jobs(engine, _SlowReconciler(0.0, log), lease_seconds=60)
        == 0
    )
    assert log == []


async def test_the_worker_shuts_down_without_orphans(engine: AsyncEngine) -> None:
    worker = service.DeliveryRecoveryWorker(engine, poll_seconds=0.05)
    await worker.start()
    assert worker.running is True
    await worker.stop()
    assert worker.running is False
    await worker.stop()
    assert worker.running is False
