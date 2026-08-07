"""Truncated-envelope recovery and real reconciliation.

CTO fix gate 2, findings 7 to 10. A flag that says "data was dropped" is
not a recovery: the dropped repository identities have to come back, and
until they do nothing may present itself as complete.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import service
from drake_api.github_app.client import GitHubContractError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import (
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

OWNER = "Duosis-Developer-Team"


def _hostile(count: int, start: int = 940_000) -> list[dict[str, Any]]:
    """Maximum-length fields so the byte budget bites."""
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


async def _jobs(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT installation_external_id, reason, status, attempts "
                    "FROM github_reconciliation_jobs ORDER BY created_at"
                )
            )
        ).all()
    return [{"installation": r[0], "reason": r[1], "status": r[2], "attempts": r[3]} for r in rows]


async def _repo_ids(engine: AsyncEngine) -> set[int]:
    async with engine.connect() as connection:
        rows = (await connection.execute(text("SELECT external_id FROM github_repositories"))).all()
    return {int(row[0]) for row in rows}


async def _access(engine: AsyncEngine, external_id: int) -> str | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT access_state FROM github_repositories WHERE external_id = :id"),
                {"id": external_id},
            )
        ).first()
    return None if row is None else str(row[0])


async def _repository_row(engine: AsyncEngine, external_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT full_name, name, default_branch, archived, onboarding_state, "
                    "last_reconciled_at FROM github_repositories WHERE external_id = :id"
                ),
                {"id": external_id},
            )
        ).one()
    return {
        "full_name": row[0],
        "name": row[1],
        "default_branch": row[2],
        "archived": row[3],
        "onboarding_state": row[4],
        "last_reconciled_at": row[5],
    }


# --- §7 truncation is recovered, not just flagged ------------------------


async def test_a_truncated_installation_event_queues_durable_reconciliation(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    delivery = str(uuidlib.uuid4())

    async with harness.api_client() as client:
        response = await deliver(
            client,
            "installation",
            installation_payload(repositories=_hostile(100)),
            delivery,
        )
    assert response.status_code == 202, response.text

    jobs = await _jobs(engine)
    assert len(jobs) == 1, "a truncated event must leave durable intent behind"
    assert jobs[0]["installation"] == INSTALLATION_ID
    assert jobs[0]["status"] == "pending"


async def test_repeated_truncated_deliveries_coalesce_onto_one_job(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        for _ in range(3):
            await deliver(
                client,
                "installation",
                installation_payload(repositories=_hostile(100)),
                str(uuidlib.uuid4()),
            )
    assert len(await _jobs(engine)) == 1, "outstanding work must not queue up in duplicate"


async def test_a_truncated_removal_does_not_destroy_membership(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The dangerous case: a partial list driving a destructive change.

    The repositories that fell outside the byte budget look exactly like
    the ones that stayed, so acting on the visible subset would remove the
    wrong things.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        assert await _access(engine, HERMES_ID) == "accessible"

        payload = installation_payload(action="removed", repositories=None)
        payload.pop("repositories", None)
        payload["repositories_removed"] = _hostile(100)
        response = await deliver(client, "installation_repositories", payload, str(uuidlib.uuid4()))
    assert response.status_code == 202, response.text

    assert await _access(engine, HERMES_ID) == "accessible", (
        "a truncated removal must not be applied as if it were complete"
    )
    jobs = await _jobs(engine)
    assert jobs and jobs[0]["status"] == "pending"


async def test_the_worker_recovers_full_membership_after_a_truncated_event(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The identities dropped by the budget come back from the provider."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(repositories=_hostile(100)),
            str(uuidlib.uuid4()),
        )
    stored = await _repo_ids(engine)
    assert len(stored) < 100, "the envelope really was truncated"

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    completed = await service.drain_reconciliation_jobs(engine, reconciler)
    assert completed == 1

    # The fake lists the full installation membership.
    recovered = await _repo_ids(engine)
    assert HERMES_ID in recovered and LOGISLOT_ID in recovered
    assert [job["status"] for job in await _jobs(engine)] == ["processed"]
    assert all(call.startswith("GET") or call.endswith("/access_tokens") for call in fake.calls)


async def test_a_failed_reconciliation_does_not_mark_anything_ready(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(repositories=_hostile(100)),
            str(uuidlib.uuid4()),
        )

    fake.mode = "unavailable"
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    completed = await service.drain_reconciliation_jobs(engine, reconciler)
    assert completed == 0
    jobs = await _jobs(engine)
    assert jobs[0]["status"] == "pending" and jobs[0]["attempts"] == 1

    async with engine.connect() as connection:
        ready = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM github_repositories WHERE onboarding_state = 'ready'"
                    )
                )
            ).scalar_one()
        )
    assert ready == 0


async def test_a_truncated_event_including_datalake_makes_no_call_for_it(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The gate holds through the reconciliation path too."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    repositories = [
        {
            "id": DATALAKE_ID,
            "node_id": "R_datalake",
            "name": "Datalake-Platform-GUI",
            "full_name": f"{OWNER}/Datalake-Platform-GUI",
            "private": True,
        },
        *_hostile(100),
    ]
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(repositories=repositories),
            str(uuidlib.uuid4()),
        )

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await service.drain_reconciliation_jobs(engine, reconciler)

    datalake_calls = [call for call in fake.calls if "Datalake" in call]
    assert datalake_calls == [], f"the gate was bypassed by reconciliation: {datalake_calls}"

    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT onboarding_state FROM github_repositories WHERE external_id = :id"),
                {"id": DATALAKE_ID},
            )
        ).scalar_one()
    assert state == "blocked"


# --- §8 reconciliation actually reconciles -------------------------------


async def test_a_missed_rename_is_corrected_by_reconciliation(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Webhooks get missed. Reconciliation is how the projection recovers."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")

        # The provider renamed it; Drake never saw the webhook.
        fake.repositories["logislot"]["name"] = "logislot-v2"
        fake.repositories["logislot"]["full_name"] = f"{OWNER}/logislot-v2"

        listing = (await client.get("/v1/integrations/github/repositories")).json()
        row = next(item for item in listing["repositories"] if item["external_id"] == LOGISLOT_ID)
        assert row["full_name"] == f"{OWNER}/logislot"

        response = await client.post(
            f"/v1/integrations/github/repositories/{row['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
    assert response.status_code == 202, response.text

    reconciled = await _repository_row(engine, LOGISLOT_ID)
    assert reconciled["full_name"] == f"{OWNER}/logislot-v2"
    assert reconciled["name"] == "logislot-v2"
    assert reconciled["default_branch"] == "main"


async def test_reconciliation_is_idempotent(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))

    first = await reconciler.reconcile_installation(INSTALLATION_ID)
    second = await reconciler.reconcile_installation(INSTALLATION_ID)
    assert (first.present, first.removed) == (second.present, second.removed)
    assert second.removed == 0


async def test_installation_reconciliation_removes_what_the_provider_no_longer_lists(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
    assert await _access(engine, LOGISLOT_ID) == "accessible"

    # The repository leaves the installation; no webhook arrives.
    del fake.repositories["logislot"]
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    sync = await reconciler.reconcile_installation(INSTALLATION_ID)

    assert sync.removed == 1
    assert await _access(engine, LOGISLOT_ID) == "removed", "soft state, not deletion"
    assert LOGISLOT_ID in await _repo_ids(engine), "history is preserved"


async def test_an_incomplete_listing_commits_no_partial_membership(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A truncated page set is a reason to fail, not to conclude removal."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
    before = await _access(engine, LOGISLOT_ID)

    fake.installation_repositories_pages = 999  # always a full page
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    with pytest.raises(GitHubContractError):
        await reconciler.reconcile_installation(INSTALLATION_ID)

    assert await _access(engine, LOGISLOT_ID) == before, (
        "a failed listing must not have removed anything"
    )


async def test_reconciliation_only_reads(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
    fake.calls.clear()

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID)

    for call in fake.calls:
        method, path = call.split(" ", 1)
        assert method == "GET" or path.endswith("/access_tokens"), f"write call: {call}"


# --- §9/§10 permission shortfall and readiness ---------------------------


async def test_a_missing_metadata_grant_blocks_instead_of_evaluating(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        row = next(item for item in listing["repositories"] if item["external_id"] == HERMES_ID)

        fake.granted_permissions = {"administration": "read", "actions": "read"}
        response = await client.post(
            f"/v1/integrations/github/repositories/{row['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
    assert response.status_code == 409, response.text

    state = (await _repository_row(engine, HERMES_ID))["onboarding_state"]
    assert state == "blocked", f"a missing required grant is BLOCKED, not {state}"


async def test_a_missing_administration_grant_makes_protection_unknown_not_pass(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        row = next(item for item in listing["repositories"] if item["external_id"] == HERMES_ID)

        # The token comes back narrower than requested, but the fake would
        # still happily answer the protection call with a 200.
        fake.granted_permissions = {"metadata": "read", "actions": "read"}
        response = await client.post(
            f"/v1/integrations/github/repositories/{row['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        snapshot = (
            await client.get(f"/v1/integrations/github/repositories/{row['id']}/policy")
        ).json()

    assert body["overall"] != "pass", "a permission we never got cannot produce a pass"
    assert body["evidence_complete"] is False
    protection = next(
        item for item in snapshot["results"] if item["rule_id"] == "branch.protection.present"
    )
    assert protection["verdict"] == "unknown"
    assert (await _repository_row(engine, HERMES_ID))["onboarding_state"] == "degraded"


async def test_partial_evidence_is_degraded_not_ready(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        row = next(item for item in listing["repositories"] if item["external_id"] == HERMES_ID)

        fake.protection_status = 500  # unreadable, not "absent"
        response = await client.post(
            f"/v1/integrations/github/repositories/{row['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
    assert response.status_code == 202, response.text
    assert response.json()["evidence_complete"] is False

    stored = await _repository_row(engine, HERMES_ID)
    assert stored["onboarding_state"] == "degraded"
    assert stored["last_reconciled_at"] is None, (
        "an incomplete read must not stamp a successful reconciliation"
    )


async def test_complete_evidence_is_ready_even_when_governance_fails(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Readiness describes the projection; the verdict describes the repo.

    A 404 from the protection endpoint is a real, complete answer: this
    branch is not protected. Complete evidence, failing governance.
    """
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        row = next(item for item in listing["repositories"] if item["external_id"] == HERMES_ID)
        fake.protection_status = 404  # a real "not protected", not an error
        response = await client.post(
            f"/v1/integrations/github/repositories/{row['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["overall"] == "fail", body
    assert body["evidence_complete"] is True

    stored = await _repository_row(engine, HERMES_ID)
    assert stored["onboarding_state"] == "ready"
    assert stored["last_reconciled_at"] is not None


async def test_a_transient_failure_keeps_the_previous_snapshot(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        row = next(item for item in listing["repositories"] if item["external_id"] == LOGISLOT_ID)
        headers = {"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())}
        good = await client.post(
            f"/v1/integrations/github/repositories/{row['id']}/reconcile", headers=headers
        )
        assert good.status_code == 202
        before = (
            await client.get(f"/v1/integrations/github/repositories/{row['id']}/policy")
        ).json()

        fake.mode = "unavailable"
        failed = await client.post(
            f"/v1/integrations/github/repositories/{row['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
        assert failed.status_code == 503
        after = (
            await client.get(f"/v1/integrations/github/repositories/{row['id']}/policy")
        ).json()

    assert after["evaluated_at"] == before["evaluated_at"]
    assert after["evidence_digest"] == before["evidence_digest"]
