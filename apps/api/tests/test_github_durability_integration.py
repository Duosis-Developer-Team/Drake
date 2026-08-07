"""Webhook delivery durability, replay evidence, and scope isolation.

These are the CTO fix-gate regressions. Each one failed on the head that
was reviewed, and each states the invariant it protects rather than the
implementation that happens to satisfy it.

The central property: a delivery that GitHub considers acknowledged must
be a delivery Drake will finish. Acknowledging before the work is durable
turns any crash in between into silent data loss, because GitHub's retry
carries the same digest and would look like a harmless replay.
"""

import asyncio
import json as jsonlib
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import (  # noqa: F401 - fixtures are used by name
    DATALAKE_ID,
    HERMES_ID,
    INSTALLATION_ID,
    LOGISLOT_ID,
    _seed_admin,
    deliver,
    github_harness,
    installation_payload,
    webhook_headers,
)

pytestmark = pytest.mark.integration


async def _delivery_row(engine: AsyncEngine, delivery_id: str) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT status, payload_digest, processed_at, attempts, event_type "
                    "FROM github_webhook_deliveries WHERE delivery_id = :id"
                ),
                {"id": delivery_id},
            )
        ).one()
    return {
        "status": row[0],
        "digest": row[1],
        "processed_at": row[2],
        "attempts": row[3],
        "event_type": row[4],
    }


async def _repository_count(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(text("SELECT count(*) FROM github_repositories"))
            ).scalar_one()
        )


# --- §3 durability ------------------------------------------------------


async def test_crash_after_claim_before_domain_work_is_not_lost(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact hole: claim commits, domain work dies, retry says 'duplicate'.

    If the claim is acknowledged before the work is durable, GitHub's retry
    carries the same digest and is indistinguishable from a harmless replay.
    The event then disappears with a 202 on both attempts.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())
    payload = installation_payload()

    boom = {"count": 0}
    original = service.upsert_repository

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        boom["count"] += 1
        raise RuntimeError("injected failure before the domain mutation lands")

    monkeypatch.setattr(service, "upsert_repository", exploding)
    async with harness.api_client() as client:
        first = await deliver(client, "installation", payload, delivery)
    # The endpoint may fail loudly, but it must NEVER report success while
    # the domain work has not happened.
    assert first.status_code >= 500 or first.json().get("status") != "processed"
    assert boom["count"] > 0
    assert await _repository_count(engine) == 0

    row = await _delivery_row(engine, delivery)
    assert row["status"] == "pending", "an unfinished delivery must stay claimable"
    assert row["processed_at"] is None

    # GitHub retries the SAME delivery. It must be processed, not acked.
    monkeypatch.setattr(service, "upsert_repository", original)
    async with harness.api_client() as client:
        retried = await deliver(client, "installation", payload, delivery)
    assert retried.status_code == 202, retried.text
    assert retried.json()["status"] != "duplicate", (
        "a pending delivery must never be acknowledged as a finished duplicate"
    )
    assert await _repository_count(engine) > 0

    row = await _delivery_row(engine, delivery)
    assert row["status"] == "processed"
    assert row["processed_at"] is not None


async def test_domain_rollback_leaves_no_partial_state(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Domain work and the processed flag must commit or fail together."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    real_apply = service.apply_announced_state
    calls = {"n": 0}

    async def fail_on_second(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected mid-batch failure")
        return await real_apply(*args, **kwargs)

    monkeypatch.setattr(service, "apply_announced_state", fail_on_second)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)

    # Partial repository writes must not survive a failed delivery.
    assert await _repository_count(engine) == 0
    row = await _delivery_row(engine, delivery)
    assert row["status"] == "pending"
    assert row["processed_at"] is None

    monkeypatch.setattr(service, "apply_announced_state", real_apply)
    async with harness.api_client() as client:
        retried = await deliver(client, "installation", installation_payload(), delivery)
    assert retried.status_code == 202
    assert await _repository_count(engine) == 2
    assert (await _delivery_row(engine, delivery))["status"] == "processed"


async def test_transient_failure_then_retry_processes_exactly_once(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    real_apply = service.apply_announced_state
    state = {"fail": True}

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("transient")
        return await real_apply(*args, **kwargs)

    monkeypatch.setattr(service, "apply_announced_state", flaky)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)
        second = await deliver(client, "installation", installation_payload(), delivery)
        assert second.status_code == 202
        # A third delivery is now a genuine, finished duplicate.
        third = await deliver(client, "installation", installation_payload(), delivery)
        assert third.status_code == 202
        assert third.json()["status"] == "duplicate"

    assert await _repository_count(engine) == 2, "exactly once, not twice"
    async with engine.connect() as connection:
        deliveries = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM github_webhook_deliveries WHERE delivery_id = :id"),
                    {"id": delivery},
                )
            ).scalar_one()
        )
    assert deliveries == 1


async def test_concurrent_duplicates_create_exactly_one_durable_work_item(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())
    payload = installation_payload()

    async with harness.api_client() as client:
        results = await asyncio.gather(
            *[deliver(client, "installation", payload, delivery) for _ in range(6)],
            return_exceptions=True,
        )

    statuses = [r.status_code for r in results if not isinstance(r, BaseException)]
    assert all(code == 202 for code in statuses), statuses
    async with engine.connect() as connection:
        rows = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM github_webhook_deliveries WHERE delivery_id = :id"),
                    {"id": delivery},
                )
            ).scalar_one()
        )
    assert rows == 1
    # Idempotent domain work: the repositories exist exactly once each.
    assert await _repository_count(engine) == 2


async def test_processed_delivery_redelivered_is_an_idempotent_ack(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    async with harness.api_client() as client:
        first = await deliver(client, "installation", installation_payload(), delivery)
        assert first.json()["status"] == "processed"
        before = await _delivery_row(engine, delivery)

        again = await deliver(client, "installation", installation_payload(), delivery)
        assert again.status_code == 202
        assert again.json()["status"] == "duplicate"

    after = await _delivery_row(engine, delivery)
    assert after["status"] == "processed"
    assert after["processed_at"] == before["processed_at"], "a replay must not restamp the original"


async def test_a_pending_delivery_is_drained_by_the_worker_without_a_redelivery(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry is one recovery path; it must not be the ONLY one.

    GitHub does not redeliver forever, so a delivery stranded by a crash
    has to be recoverable from Drake's own side.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    real_apply = service.apply_announced_state

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected")

    monkeypatch.setattr(service, "apply_announced_state", exploding)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)
    assert (await _delivery_row(engine, delivery))["status"] == "pending"

    monkeypatch.setattr(service, "apply_announced_state", real_apply)
    drained = await service.drain_pending_deliveries(engine)
    assert drained >= 1
    assert (await _delivery_row(engine, delivery))["status"] == "processed"
    assert await _repository_count(engine) == 2


async def test_retries_are_bounded_and_never_spin_forever(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("permanent")

    monkeypatch.setattr(service, "apply_announced_state", exploding)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)

    for _ in range(service.MAX_DELIVERY_ATTEMPTS + 3):
        await service.drain_pending_deliveries(engine)

    row = await _delivery_row(engine, delivery)
    assert row["status"] == "failed", "a poison delivery must stop, not spin"
    assert row["attempts"] <= service.MAX_DELIVERY_ATTEMPTS
    # And a dead-lettered delivery is not silently forgotten.
    async with engine.connect() as connection:
        audited = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE action = 'github.webhook.exhausted'"
                    )
                )
            ).scalar_one()
        )
    assert audited >= 1


# --- §4 conflicting replay evidence -------------------------------------


async def test_conflict_never_rewrites_the_original_delivery_evidence(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A forged replay is an attack on the record; it must not edit it."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    async with harness.api_client() as client:
        first = await deliver(client, "installation", installation_payload(), delivery)
        assert first.status_code == 202
        original = await _delivery_row(engine, delivery)
        assert original["status"] == "processed"
        repositories_before = await _repository_count(engine)

        forged = jsonlib.dumps(installation_payload(action="deleted")).encode()
        conflict = await client.post(
            "/v1/integrations/github/webhook",
            content=forged,
            headers=webhook_headers("installation", delivery, forged),
        )
        assert conflict.status_code == 409

    after = await _delivery_row(engine, delivery)
    assert after["status"] == "processed", "the original delivery stays processed"
    assert after["digest"] == original["digest"], "the recorded digest is evidence, not scratch"
    assert after["processed_at"] == original["processed_at"]
    assert await _repository_count(engine) == repositories_before, "a conflict does no domain work"

    async with engine.connect() as connection:
        conflicts = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE action = 'github.webhook.replay_conflict'"
                    )
                )
            ).scalar_one()
        )
    assert conflicts >= 1


async def test_conflict_against_a_pending_delivery_also_preserves_evidence(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    real_apply = service.apply_announced_state

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected")

    monkeypatch.setattr(service, "apply_announced_state", exploding)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)
        pending = await _delivery_row(engine, delivery)
        assert pending["status"] == "pending"

        forged = jsonlib.dumps(installation_payload(action="deleted")).encode()
        conflict = await client.post(
            "/v1/integrations/github/webhook",
            content=forged,
            headers=webhook_headers("installation", delivery, forged),
        )
        assert conflict.status_code == 409

    after = await _delivery_row(engine, delivery)
    assert after["digest"] == pending["digest"]
    assert after["status"] == "pending", "a conflict must not close out honest pending work"

    # The genuine delivery can still complete afterwards.
    monkeypatch.setattr(service, "apply_announced_state", real_apply)
    await service.drain_pending_deliveries(engine)
    assert (await _delivery_row(engine, delivery))["status"] == "processed"


async def test_concurrent_original_and_conflict_is_deterministic(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())
    honest = jsonlib.dumps(installation_payload()).encode()
    forged = jsonlib.dumps(installation_payload(action="deleted")).encode()

    async with harness.api_client() as client:
        results = await asyncio.gather(
            *[
                client.post(
                    "/v1/integrations/github/webhook",
                    content=body,
                    headers=webhook_headers("installation", delivery, body),
                )
                for body in (honest, forged, honest, forged)
            ],
            return_exceptions=True,
        )

    codes = sorted(r.status_code for r in results if not isinstance(r, BaseException))
    # Exactly one digest wins the row; every request with the other digest
    # is a 409. Which digest wins depends on arrival order, but the split
    # is always "one digest accepted, the other refused".
    assert set(codes) <= {202, 409}
    assert codes.count(202) == 2 and codes.count(409) == 2, codes

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT count(*), max(payload_digest) FROM github_webhook_deliveries "
                    "WHERE delivery_id = :id"
                ),
                {"id": delivery},
            )
        ).one()
    assert rows[0] == 1
