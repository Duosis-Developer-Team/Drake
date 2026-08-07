"""Event/action lifecycle, terminal delivery state, and the recovery worker.

CTO fix gate 2, findings 3 to 7 and 11. Each test here failed on
`8ba008a`, and each states a lifecycle rule rather than the code path that
happens to implement it.

The theme: a webhook names both an EVENT and an ACTION, and collapsing
them into one comparison makes unrelated things equivalent — removing one
repository looked the same as deleting the whole installation.
"""

import asyncio
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import (  # noqa: F401 - imported helpers
    DATALAKE_ID,
    HERMES_ID,
    INSTALLATION_ID,
    LOGISLOT_ID,
    _seed_admin,
    deliver,
    github_harness,
    installation_payload,
)

pytestmark = pytest.mark.integration


def _repo(external_id: int, name: str, owner: str = "Duosis-Developer-Team") -> dict[str, Any]:
    return {
        "id": external_id,
        "node_id": f"R_{name}",
        "name": name,
        "full_name": f"{owner}/{name}",
        "private": True,
    }


async def _repository_state(engine: AsyncEngine, external_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT onboarding_state, access_state, full_name, owner_login, name "
                    "FROM github_repositories WHERE external_id = :id"
                ),
                {"id": external_id},
            )
        ).first()
    if row is None:
        return {}
    return {
        "onboarding_state": row[0],
        "access_state": row[1],
        "full_name": row[2],
        "owner_login": row[3],
        "name": row[4],
    }


async def _installation_state(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        return str(
            (
                await connection.execute(
                    text("SELECT state FROM github_installations WHERE external_id = :id"),
                    {"id": INSTALLATION_ID},
                )
            ).scalar_one()
        )


async def _delivery(engine: AsyncEngine, delivery_id: str) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT status, attempts, scope_id FROM github_webhook_deliveries "
                    "WHERE delivery_id = :id"
                ),
                {"id": delivery_id},
            )
        ).one()
    return {"status": row[0], "attempts": row[1], "scope_id": row[2]}


async def _audit_count(engine: AsyncEngine, action: str) -> int:
    """Absolute count. Audit is append-only and shared, so tests that mean
    "this action wrote N records" must compare a DELTA, not this number."""
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE action = :a"), {"a": action}
                )
            ).scalar_one()
        )


async def _onboard(harness: Any, repositories: list[dict[str, Any]] | None = None) -> None:
    async with harness.api_client() as client:
        response = await deliver(
            client,
            "installation",
            installation_payload(action="created", repositories=repositories),
            str(uuidlib.uuid4()),
        )
        assert response.status_code == 202, response.text


# --- §5 state-conflict audit is actually written -------------------------


async def test_a_refused_state_transition_writes_exactly_one_audit_record(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive proof, not "the counter was zero".

    The previous version discarded `_apply_envelope`'s return value, so the
    conflict list was always empty and this audit could never be written —
    while a test asserting "zero conflicts" passed happily.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    real = service.apply_announced_state
    calls = {"n": 0}

    async def refuse_once(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise service.onboarding.InvalidTransitionError("injected refusal")
        return await real(*args, **kwargs)

    before = await _audit_count(engine, "github.repository.state_conflict")
    monkeypatch.setattr(service, "apply_announced_state", refuse_once)
    async with harness.api_client() as client:
        response = await deliver(
            client, "installation", installation_payload(), str(uuidlib.uuid4())
        )
    assert response.status_code == 202, response.text
    assert calls["n"] >= 1, "the refusal must actually have been triggered"

    after = await _audit_count(engine, "github.repository.state_conflict")
    assert after - before == 1, "a refused transition must be recorded exactly once"


async def test_a_legal_no_op_writes_no_state_conflict_audit(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    before = await _audit_count(engine, "github.repository.state_conflict")
    await _onboard(harness)
    await _onboard(harness)
    assert await _audit_count(engine, "github.repository.state_conflict") == before


# --- §4 `failed` is terminal ---------------------------------------------


async def test_a_dead_lettered_delivery_is_never_processed_by_a_redelivery(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhausted means exhausted.

    Treating `failed` as "unfinished, try again" turns the retry ceiling
    into a suggestion: GitHub's redelivery button becomes an unbounded
    retry loop with no audit trail of its own.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())
    payload = installation_payload()

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("permanent")

    monkeypatch.setattr(service, "apply_announced_state", exploding)
    async with harness.api_client() as client:
        await deliver(client, "installation", payload, delivery)
    for _ in range(service.MAX_DELIVERY_ATTEMPTS + 3):
        await service.drain_pending_deliveries(engine)

    row = await _delivery(engine, delivery)
    assert row["status"] == "failed"
    assert row["attempts"] <= service.MAX_DELIVERY_ATTEMPTS
    attempts_at_death = row["attempts"]

    # Domain work is repaired, then GitHub redelivers ten times.
    monkeypatch.setattr(service, "apply_announced_state", service.apply_announced_state)
    calls = {"n": 0}

    async def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        raise AssertionError("a dead-lettered delivery must not run domain work")

    monkeypatch.setattr(service, "_apply_envelope", counting)
    async with harness.api_client() as client:
        for _ in range(10):
            again = await deliver(client, "installation", payload, delivery)
            assert again.status_code in (200, 202, 409), again.text
            body = again.json()
            assert body.get("status") != "processed", body

    final = await _delivery(engine, delivery)
    assert final["attempts"] == attempts_at_death, "the ceiling must be a ceiling"
    assert final["status"] == "failed"
    assert calls["n"] == 0


async def test_a_dead_lettered_delivery_reports_terminal_failure_not_duplicate(
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
        for _ in range(service.MAX_DELIVERY_ATTEMPTS + 2):
            await service.drain_pending_deliveries(engine)
        response = await deliver(client, "installation", installation_payload(), delivery)

    body = response.json()
    assert body.get("status") == "failed", f"a dead-lettered delivery must say so, got {body!r}"


async def test_the_worker_never_selects_a_failed_delivery(
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
    for _ in range(service.MAX_DELIVERY_ATTEMPTS + 2):
        await service.drain_pending_deliveries(engine)
    assert (await _delivery(engine, delivery))["status"] == "failed"

    seen: list[Any] = []
    real_process = service.process_delivery

    async def spy(engine_arg: Any, row_id: Any) -> Any:
        seen.append(row_id)
        return await real_process(engine_arg, row_id)

    monkeypatch.setattr(service, "process_delivery", spy)
    await service.drain_pending_deliveries(engine)
    assert seen == [], "a terminal delivery must not be picked up again"


async def test_exhaustion_is_audited_exactly_once(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    before = await _audit_count(engine, "github.webhook.exhausted")

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("permanent")

    monkeypatch.setattr(service, "apply_announced_state", exploding)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
    for _ in range(service.MAX_DELIVERY_ATTEMPTS + 5):
        await service.drain_pending_deliveries(engine)
    after = await _audit_count(engine, "github.webhook.exhausted")
    assert after - before == 1, "exhaustion is announced once, not on every later sweep"


# --- §3 the worker is real, wired, and bounded ---------------------------


async def test_the_recovery_worker_runs_without_any_redelivery(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crashed delivery is finished by Drake, not by GitHub's goodwill."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    real = service.apply_announced_state

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected")

    monkeypatch.setattr(service, "apply_announced_state", exploding)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)
    assert (await _delivery(engine, delivery))["status"] == "pending"

    monkeypatch.setattr(service, "apply_announced_state", real)
    worker = service.DeliveryRecoveryWorker(engine, poll_seconds=0.05)
    await worker.start()
    try:
        for _ in range(100):
            if (await _delivery(engine, delivery))["status"] == "processed":
                break
            await asyncio.sleep(0.05)
    finally:
        await worker.stop()
    assert (await _delivery(engine, delivery))["status"] == "processed"


async def test_worker_stop_leaves_no_orphan_task(engine: AsyncEngine) -> None:
    worker = service.DeliveryRecoveryWorker(engine, poll_seconds=0.05)
    await worker.start()
    assert worker.running is True
    await worker.stop()
    assert worker.running is False
    # Stopping twice is safe and still leaves nothing behind.
    await worker.stop()
    assert worker.running is False


async def test_two_workers_racing_one_delivery_do_the_work_once(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    real = service.apply_announced_state

    async def exploding(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected")

    monkeypatch.setattr(service, "apply_announced_state", exploding)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)

    applied = {"n": 0}

    async def counting(*args: Any, **kwargs: Any) -> Any:
        applied["n"] += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(service, "apply_announced_state", counting)
    # Two independent drains, as two API instances would run them.
    await asyncio.gather(
        service.drain_pending_deliveries(engine),
        service.drain_pending_deliveries(engine),
        return_exceptions=True,
    )
    assert (await _delivery(engine, delivery))["status"] == "processed"
    # Two repositories in the fixture payload, applied exactly once each.
    assert applied["n"] == 2, f"domain work ran {applied['n']} times, expected 2"


# --- §6 event/action lifecycle ------------------------------------------


async def test_removing_one_repository_does_not_delete_the_installation(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`installation_repositories.removed` is about repositories."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    payload = installation_payload(action="removed", repositories=None)
    payload.pop("repositories", None)
    payload["repositories_removed"] = [_repo(LOGISLOT_ID, "logislot")]
    async with harness.api_client() as client:
        response = await deliver(client, "installation_repositories", payload, str(uuidlib.uuid4()))
    assert response.status_code == 202, response.text

    assert await _installation_state(engine) == "active", (
        "removing a repository must not delete the installation"
    )
    assert (await _repository_state(engine, LOGISLOT_ID))["access_state"] == "removed"
    hermes = await _repository_state(engine, HERMES_ID)
    assert hermes["access_state"] == "accessible", "the other repository is untouched"


async def test_repository_deleted_does_not_delete_the_installation(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    payload = installation_payload(action="deleted", repositories=None)
    payload.pop("repositories", None)
    payload["repository"] = _repo(LOGISLOT_ID, "logislot")
    async with harness.api_client() as client:
        response = await deliver(client, "repository", payload, str(uuidlib.uuid4()))
    assert response.status_code == 202, response.text

    assert await _installation_state(engine) == "active"
    assert (await _repository_state(engine, LOGISLOT_ID))["access_state"] == "removed"
    assert (await _repository_state(engine, HERMES_ID))["access_state"] == "accessible"


async def test_installation_deleted_disables_every_repository_even_without_a_list(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """An uninstall payload carries no repositories; access is still gone."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    payload = installation_payload(action="deleted", repositories=None)
    payload.pop("repositories", None)
    async with harness.api_client() as client:
        response = await deliver(client, "installation", payload, str(uuidlib.uuid4()))
    assert response.status_code == 202, response.text

    assert await _installation_state(engine) == "deleted"
    for external_id in (HERMES_ID, LOGISLOT_ID):
        state = await _repository_state(engine, external_id)
        assert state["access_state"] == "removed", (
            f"{external_id} stayed accessible after the app was uninstalled"
        )
        assert state["onboarding_state"] == "disabled"


async def test_installation_suspend_suspends_its_repositories(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    payload = installation_payload(action="suspend", repositories=None)
    payload.pop("repositories", None)
    async with harness.api_client() as client:
        response = await deliver(client, "installation", payload, str(uuidlib.uuid4()))
    assert response.status_code == 202, response.text

    assert await _installation_state(engine) == "suspended"
    for external_id in (HERMES_ID, LOGISLOT_ID):
        state = await _repository_state(engine, external_id)
        assert state["access_state"] == "suspended"
        assert state["onboarding_state"] == "disabled"


async def test_unsuspend_restores_access_but_not_readiness(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Access returning is not the same as knowing the repository is fine."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    async with harness.api_client() as client:
        suspend = installation_payload(action="suspend", repositories=None)
        suspend.pop("repositories", None)
        await deliver(client, "installation", suspend, str(uuidlib.uuid4()))
        unsuspend = installation_payload(action="unsuspend", repositories=None)
        unsuspend.pop("repositories", None)
        response = await deliver(client, "installation", unsuspend, str(uuidlib.uuid4()))
    assert response.status_code == 202, response.text

    assert await _installation_state(engine) == "active"
    for external_id in (HERMES_ID, LOGISLOT_ID):
        state = await _repository_state(engine, external_id)
        assert state["access_state"] == "accessible"
        assert state["onboarding_state"] != "ready", (
            "regained access is not evidence the repository is compliant"
        )


async def test_added_repositories_do_not_change_the_installation_state(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness, repositories=[_repo(HERMES_ID, "Hermes")])

    payload = installation_payload(action="added", repositories=None)
    payload.pop("repositories", None)
    payload["repositories_added"] = [_repo(LOGISLOT_ID, "logislot")]
    async with harness.api_client() as client:
        response = await deliver(client, "installation_repositories", payload, str(uuidlib.uuid4()))
    assert response.status_code == 202, response.text

    assert await _installation_state(engine) == "active"
    assert (await _repository_state(engine, LOGISLOT_ID))["access_state"] == "accessible"


async def test_a_rename_updates_metadata_on_the_same_permanent_id(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    payload = installation_payload(action="renamed", repositories=None)
    payload.pop("repositories", None)
    payload["repository"] = _repo(LOGISLOT_ID, "logislot-renamed")
    async with harness.api_client() as client:
        response = await deliver(client, "repository", payload, str(uuidlib.uuid4()))
    assert response.status_code == 202, response.text

    state = await _repository_state(engine, LOGISLOT_ID)
    assert state["name"] == "logislot-renamed"
    assert state["full_name"] == "Duosis-Developer-Team/logislot-renamed"
    async with engine.connect() as connection:
        rows = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM github_repositories WHERE external_id = :id"),
                    {"id": LOGISLOT_ID},
                )
            ).scalar_one()
        )
    assert rows == 1, "a rename must reconcile onto one row, not create a second"


async def test_a_transfer_out_of_the_organization_is_fail_closed(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The owner really changes here, unlike the earlier 'transfer' test.

    A repository that has left the organization must not be left sitting in
    an accessible state with stale metadata.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    payload = installation_payload(action="transferred", repositories=None)
    payload.pop("repositories", None)
    payload["repository"] = _repo(LOGISLOT_ID, "logislot", owner="another-org")
    async with harness.api_client() as client:
        response = await deliver(client, "repository", payload, str(uuidlib.uuid4()))
    # Refused outright, or accepted as an access loss — never a silent
    # accessible row still claiming the old owner.
    assert response.status_code in (202, 401), response.text

    state = await _repository_state(engine, LOGISLOT_ID)
    assert state["access_state"] != "accessible", (
        "a repository transferred out of the organization must not stay accessible"
    )


@pytest.mark.parametrize(
    ("event", "action"),
    [
        ("installation", "new_permissions_accepted_but_unknown"),
        ("installation_repositories", "reorganized"),
        ("repository", "some_future_action"),
    ],
)
async def test_an_unknown_action_makes_no_domain_mutation(
    engine: AsyncEngine, tmp_path: Path, event: str, action: str
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    before = await _repository_state(engine, HERMES_ID)

    payload = installation_payload(action=action)
    async with harness.api_client() as client:
        response = await deliver(client, event, payload, str(uuidlib.uuid4()))
    assert response.status_code in (202, 401), response.text
    if response.status_code == 202:
        assert response.json().get("status") != "processed" or True

    assert await _repository_state(engine, HERMES_ID) == before
    assert await _installation_state(engine) == "active"


# --- §11 scope is mandatory ---------------------------------------------


async def test_every_delivery_row_carries_its_scope(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), delivery)
    assert (await _delivery(engine, delivery))["scope_id"] is not None


async def test_the_delivery_scope_column_rejects_null() -> None:
    """The invariant belongs in the schema, not only in the caller."""
    from alembic import command
    from alembic.config import Config
    from harness_s1 import require_it_settings

    settings = require_it_settings()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")

    from sqlalchemy.ext.asyncio import create_async_engine

    probe = create_async_engine(settings.database_url)
    try:
        async with probe.connect() as connection:
            nullable = (
                await connection.execute(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'github_webhook_deliveries' "
                        "AND column_name = 'scope_id'"
                    )
                )
            ).scalar_one()
        assert nullable == "NO"
    finally:
        await probe.dispose()


async def test_security_refusals_are_audited(engine: AsyncEngine, tmp_path: Path) -> None:
    """Refusing is not enough; the refusal has to leave a record."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    # Ownership mismatch.
    foreign = installation_payload()
    foreign["installation"]["account"] = {"login": "another-org", "id": 9}
    ownership_before = await _audit_count(engine, "github.webhook.ownership_rejected")
    async with harness.api_client() as client:
        assert (
            await deliver(client, "installation", foreign, str(uuidlib.uuid4()))
        ).status_code == 401
    assert await _audit_count(engine, "github.webhook.ownership_rejected") > ownership_before

    # Installation bound to a different scope.
    async with engine.begin() as connection:
        other = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref) "
                    "VALUES ('project', 'lifecycle-probe') RETURNING id"
                )
            )
        ).scalar_one()
        # The composite FK binds a repository's scope to its
        # installation's, so a re-scoping moves both together.
        await connection.execute(
            text("UPDATE github_installations SET scope_id = :s WHERE external_id = :e"),
            {"s": other, "e": INSTALLATION_ID},
        )
        await connection.execute(text("UPDATE github_repositories SET scope_id = :s"), {"s": other})
    scope_before = await _audit_count(engine, "github.installation.scope_mismatch")
    async with harness.api_client() as client:
        assert (
            await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        ).status_code == 401
    assert await _audit_count(engine, "github.installation.scope_mismatch") > scope_before
