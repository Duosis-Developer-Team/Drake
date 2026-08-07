"""Provider identity verification, gate re-derivation, scope binding, leases.

CTO fix gate 3, findings 6 to 13. Every test here failed on `6f4cb52`.

The theme: a provider response has to be checked against the identity we
asked about. Writing whatever came back onto the row we happened to query
by name means a reused path, a transfer, or a rename into a gated name all
land on the wrong object with the right-looking id.
"""

import asyncio
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import service
from drake_api.github_app.client import GitHubError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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


async def _repository(engine: AsyncEngine, external_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT onboarding_state, access_state, full_name, name, default_branch, "
                    "security_gate, reconciliation_state, last_reconciled_at, scope_id, "
                    "installation_id FROM github_repositories WHERE external_id = :id"
                ),
                {"id": external_id},
            )
        ).one()
    return {
        "onboarding_state": row[0],
        "access_state": row[1],
        "full_name": row[2],
        "name": row[3],
        "default_branch": row[4],
        "security_gate": row[5],
        "reconciliation_state": row[6],
        "last_reconciled_at": row[7],
        "scope_id": row[8],
        "installation_id": row[9],
    }


async def _audits(engine: AsyncEngine, action: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE action = :a"), {"a": action}
                )
            ).scalar_one()
        )


async def _row_id(engine: AsyncEngine, external_id: int) -> uuidlib.UUID:
    async with engine.connect() as connection:
        return uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM github_repositories WHERE external_id = :e"),
                        {"e": external_id},
                    )
                ).scalar_one()
            )
        )


async def _scope_of_installation(engine: AsyncEngine) -> uuidlib.UUID:
    async with engine.connect() as connection:
        return uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT scope_id FROM github_installations WHERE external_id = :e"),
                        {"e": INSTALLATION_ID},
                    )
                ).scalar_one()
            )
        )


async def _onboard(harness: Any) -> None:
    async with harness.api_client() as client:
        response = await deliver(
            client, "installation", installation_payload(), str(uuidlib.uuid4())
        )
        assert response.status_code == 202, response.text


async def _reconcile(harness: Any, engine: AsyncEngine, external_id: int) -> Any:
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    return await reconciler.reconcile_repository(
        await _row_id(engine, external_id),
        INSTALLATION_ID,
        (await _repository(engine, external_id))["full_name"],
        external_id,
    )


# --- §6 permanent repository identity -------------------------------------


async def test_a_missed_rename_lands_on_the_same_permanent_id(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    fake.repositories["logislot"]["name"] = "logislot-renamed"
    fake.repositories["logislot"]["full_name"] = f"{OWNER}/logislot-renamed"
    await _reconcile(harness, engine, LOGISLOT_ID)

    row = await _repository(engine, LOGISLOT_ID)
    assert row["full_name"] == f"{OWNER}/logislot-renamed"
    async with engine.connect() as connection:
        count = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM github_repositories WHERE external_id = :e"),
                    {"e": LOGISLOT_ID},
                )
            ).scalar_one()
        )
    assert count == 1


async def test_a_reused_path_returning_another_id_mutates_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The path we ask about is not the identity we mean.

    If `owner/logislot` is deleted and someone else creates a repository at
    the same path, the provider answers with a DIFFERENT permanent id. That
    response must never be written onto our row.
    """
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    before = await _repository(engine, LOGISLOT_ID)
    audits_before = await _audits(engine, "github.repository.identity_mismatch")

    # Same path, different repository.
    fake.repositories["logislot"]["id"] = 999_777
    fake.repositories["logislot"]["node_id"] = "R_someoneelse"

    with pytest.raises(service.RepositoryIdentityError):
        await _reconcile(harness, engine, LOGISLOT_ID)

    after = await _repository(engine, LOGISLOT_ID)
    assert after["full_name"] == before["full_name"]
    assert after["default_branch"] == before["default_branch"]
    assert after["onboarding_state"] == "blocked"
    assert after["security_gate"] is None or after["security_gate"] == "identity_conflict"
    assert await _audits(engine, "github.repository.identity_mismatch") == audits_before + 1


@pytest.mark.parametrize("bogus", [None, "900002", 12.5, True])
async def test_a_missing_or_non_integer_id_fails_closed(
    engine: AsyncEngine, tmp_path: Path, bogus: Any
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    before = await _repository(engine, LOGISLOT_ID)

    if bogus is None:
        del fake.repositories["logislot"]["id"]
    else:
        fake.repositories["logislot"]["id"] = bogus

    with pytest.raises(service.RepositoryIdentityError):
        await _reconcile(harness, engine, LOGISLOT_ID)
    assert (await _repository(engine, LOGISLOT_ID))["full_name"] == before["full_name"]


async def test_a_transfer_out_seen_by_the_provider_is_an_access_loss(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    fake.repositories["logislot"]["full_name"] = "another-org/logislot"
    with pytest.raises(service.RepositoryIdentityError):
        await _reconcile(harness, engine, LOGISLOT_ID)

    row = await _repository(engine, LOGISLOT_ID)
    assert row["access_state"] == "removed"
    assert row["onboarding_state"] == "disabled"


async def test_an_identity_mismatch_does_not_preserve_a_previous_ready(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404  # complete evidence, failing governance
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _repository(engine, HERMES_ID))["onboarding_state"] == "ready"

    fake.repositories["Hermes"]["id"] = 888_111
    with pytest.raises(service.RepositoryIdentityError):
        await _reconcile(harness, engine, HERMES_ID)
    assert (await _repository(engine, HERMES_ID))["onboarding_state"] != "ready"


# --- §7 the gate is re-derived from what the provider actually returned ---


async def test_an_already_gated_repository_makes_no_provider_call(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(
                repositories=[
                    {
                        "id": DATALAKE_ID,
                        "node_id": "R_dl",
                        "name": "Datalake-Platform-GUI",
                        "full_name": f"{OWNER}/Datalake-Platform-GUI",
                        "private": True,
                    }
                ]
            ),
            str(uuidlib.uuid4()),
        )
    fake.calls.clear()
    with pytest.raises(service.SecurityGateBlockedError):
        await _reconcile(harness, engine, DATALAKE_ID)
    assert fake.calls == [], "the gate must refuse before the network"


async def test_a_rename_into_the_gated_name_blocks_before_policy_reads(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The gate is derived from the name, so it has to be re-derived when
    the provider tells us the name changed."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    # logislot was renamed to the gated name while we were not looking.
    fake.repositories["logislot"]["name"] = "Datalake-Platform-GUI"
    fake.repositories["logislot"]["full_name"] = f"{OWNER}/Datalake-Platform-GUI"
    fake.calls.clear()

    with pytest.raises(service.SecurityGateBlockedError):
        await _reconcile(harness, engine, LOGISLOT_ID)

    row = await _repository(engine, LOGISLOT_ID)
    assert row["onboarding_state"] == "blocked"
    assert row["security_gate"] == "manual_env_review"
    # The metadata read that revealed the name is allowed; nothing after it.
    policy_calls = [
        call
        for call in fake.calls
        if "/branches/" in call
        or "/rules/branches/" in call
        or "/actions/workflows" in call
        or "/environments" in call
    ]
    assert policy_calls == [], (
        f"policy subresources were read after the gate closed: {policy_calls}"
    )


async def test_renaming_away_from_the_gated_name_does_not_open_the_gate(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(
                repositories=[
                    {
                        "id": DATALAKE_ID,
                        "node_id": "R_dl",
                        "name": "Datalake-Platform-GUI",
                        "full_name": f"{OWNER}/Datalake-Platform-GUI",
                        "private": True,
                    }
                ]
            ),
            str(uuidlib.uuid4()),
        )

    payload = installation_payload(action="renamed", repositories=None)
    payload.pop("repositories", None)
    payload["repository"] = {
        "id": DATALAKE_ID,
        "node_id": "R_dl",
        "name": "datalake-renamed",
        "full_name": f"{OWNER}/datalake-renamed",
        "private": True,
    }
    async with harness.api_client() as client:
        await deliver(client, "repository", payload, str(uuidlib.uuid4()))

    row = await _repository(engine, DATALAKE_ID)
    assert row["onboarding_state"] == "blocked", "a rename must not close a manual security gate"


async def test_installation_reconciliation_makes_no_call_for_a_gated_repository(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.repositories["Datalake-Platform-GUI"] = {
        "id": DATALAKE_ID,
        "node_id": "R_dl",
        "name": "Datalake-Platform-GUI",
        "full_name": f"{OWNER}/Datalake-Platform-GUI",
        "private": True,
        "visibility": "private",
        "archived": False,
        "disabled": False,
        "default_branch": "main",
    }
    scope_id = await _scope_of_installation(engine)
    fake.calls.clear()

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    per_repo = [call for call in fake.calls if "Datalake-Platform-GUI" in call]
    assert per_repo == [], f"reconciliation touched a gated repository: {per_repo}"
    assert (await _repository(engine, DATALAKE_ID))["onboarding_state"] == "blocked"


# --- §8 partial evidence cannot be promoted by a webhook -----------------


async def test_a_webhook_cannot_promote_partial_evidence_back_to_ready(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Once we know we could not see the whole picture, only a complete
    reconciliation may say otherwise. A rename notification cannot."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _repository(engine, HERMES_ID))["onboarding_state"] == "ready"

    # Now the provider becomes partly unreadable.
    fake.protection_status = 500
    partial = await _reconcile(harness, engine, HERMES_ID)
    assert partial.complete is False
    degraded = await _repository(engine, HERMES_ID)
    assert degraded["onboarding_state"] == "degraded"
    assert degraded["reconciliation_state"] != "complete"

    payload = installation_payload(action="renamed", repositories=None)
    payload.pop("repositories", None)
    payload["repository"] = {
        "id": HERMES_ID,
        "node_id": "R_hermes",
        "name": "Hermes",
        "full_name": f"{OWNER}/Hermes",
        "private": True,
    }
    async with harness.api_client() as client:
        await deliver(client, "repository", payload, str(uuidlib.uuid4()))
    assert (await _repository(engine, HERMES_ID))["onboarding_state"] == "degraded"

    added = installation_payload(action="added", repositories=None)
    added.pop("repositories", None)
    added["repositories_added"] = [
        {
            "id": HERMES_ID,
            "node_id": "R_hermes",
            "name": "Hermes",
            "full_name": f"{OWNER}/Hermes",
            "private": True,
        }
    ]
    async with harness.api_client() as client:
        await deliver(client, "installation_repositories", added, str(uuidlib.uuid4()))
    assert (await _repository(engine, HERMES_ID))["onboarding_state"] == "degraded"

    # Only a complete reconciliation restores READY.
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _repository(engine, HERMES_ID))["onboarding_state"] == "ready"


async def test_a_partial_failure_keeps_the_last_good_snapshot(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    first = await _reconcile(harness, engine, HERMES_ID)
    stamped = (await _repository(engine, HERMES_ID))["last_reconciled_at"]

    fake.protection_status = 500
    partial = await _reconcile(harness, engine, HERMES_ID)
    assert partial.complete is False

    async with engine.connect() as connection:
        snapshots = int(
            (
                await connection.execute(text("SELECT count(*) FROM github_policy_evaluations"))
            ).scalar_one()
        )
    assert snapshots >= 1, "the last-good snapshot must survive a partial failure"
    assert first.evaluation.overall in ("pass", "warn", "fail")
    # The successful timestamp is preserved; the CURRENT state is degraded.
    row = await _repository(engine, HERMES_ID)
    assert row["last_reconciled_at"] == stamped
    assert row["onboarding_state"] == "degraded"


# --- §9 reconciliation is scope-bound ------------------------------------


async def test_reconciliation_writes_into_the_installation_scope_not_root(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    async with engine.begin() as connection:
        other = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref) "
                    "VALUES ('project', 'scoped-install') RETURNING id"
                )
            )
        ).scalar_one()
        await connection.execute(
            text("UPDATE github_installations SET scope_id = :s WHERE external_id = :e"),
            {"s": other, "e": INSTALLATION_ID},
        )
        await connection.execute(text("UPDATE github_repositories SET scope_id = :s"), {"s": other})
    other_scope = uuidlib.UUID(str(other))

    # A repository the provider knows about but Drake has never seen.
    fake.repositories["newcomer"] = {
        "id": 951_001,
        "node_id": "R_newcomer",
        "name": "newcomer",
        "full_name": f"{OWNER}/newcomer",
        "private": True,
        "visibility": "private",
        "archived": False,
        "disabled": False,
        "default_branch": "main",
    }

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=other_scope)

    created = await _repository(engine, 951_001)
    assert uuidlib.UUID(str(created["scope_id"])) == other_scope, (
        "a new repository must land in the installation's scope, not root"
    )


async def test_a_job_scope_that_contradicts_the_installation_fails_before_the_network(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    async with engine.begin() as connection:
        wrong = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref) "
                    "VALUES ('project', 'wrong-scope') RETURNING id"
                )
            )
        ).scalar_one()
    fake.calls.clear()
    audits_before = await _audits(engine, "github.installation.scope_mismatch")

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    with pytest.raises(service.InstallationScopeMismatchError):
        await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=uuidlib.UUID(str(wrong)))
    assert fake.calls == [], "a scope mismatch must be caught before any provider call"
    assert await _audits(engine, "github.installation.scope_mismatch") == audits_before + 1


async def test_the_database_refuses_a_repository_in_a_foreign_scope(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The invariant belongs in the schema, not only in the writer."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    async with engine.begin() as connection:
        foreign = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref) "
                    "VALUES ('project', 'foreign-scope') RETURNING id"
                )
            )
        ).scalar_one()

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE github_repositories SET scope_id = :s WHERE external_id = :e"),
                {"s": foreign, "e": LOGISLOT_ID},
            )


# --- §10 provider identity and missed uninstall ---------------------------


async def test_a_missed_uninstall_is_closed_by_reconciliation(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """No webhook arrives; the provider simply stops knowing the App."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)
    assert (await _repository(engine, LOGISLOT_ID))["access_state"] == "accessible"

    fake.installation_present = False  # documented 404 for the installation
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    sync = await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    assert sync.state == "deleted"
    async with engine.connect() as connection:
        state = str(
            (
                await connection.execute(
                    text("SELECT state FROM github_installations WHERE external_id = :e"),
                    {"e": INSTALLATION_ID},
                )
            ).scalar_one()
        )
    assert state == "deleted"
    for external_id in (HERMES_ID, LOGISLOT_ID):
        row = await _repository(engine, external_id)
        assert row["access_state"] == "removed"
        assert row["onboarding_state"] == "disabled"


@pytest.mark.parametrize("mode", ["rate_limited", "unavailable"])
async def test_a_transient_failure_is_not_read_as_an_uninstall(
    engine: AsyncEngine, tmp_path: Path, mode: str
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)

    fake.mode = mode
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    with pytest.raises(GitHubError):
        await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    async with engine.connect() as connection:
        state = str(
            (
                await connection.execute(
                    text("SELECT state FROM github_installations WHERE external_id = :e"),
                    {"e": INSTALLATION_ID},
                )
            ).scalar_one()
        )
    assert state == "active", f"{mode} must not be mistaken for an uninstall"
    assert (await _repository(engine, LOGISLOT_ID))["access_state"] == "accessible"
    # But nothing is refreshed into READY either.
    assert (await _repository(engine, LOGISLOT_ID))["onboarding_state"] != "ready"


async def test_a_foreign_installation_identity_mutates_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)
    audits_before = await _audits(engine, "github.installation.identity_mismatch")

    fake.installation_account_login = "another-org"
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    with pytest.raises(service.InstallationIdentityError):
        await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    assert (await _repository(engine, LOGISLOT_ID))["access_state"] == "accessible"
    assert await _audits(engine, "github.installation.identity_mismatch") == audits_before + 1


async def test_an_installation_id_mismatch_mutates_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)

    fake.installation_id_override = 777_777
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    with pytest.raises(service.InstallationIdentityError):
        await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)
    assert (await _repository(engine, LOGISLOT_ID))["access_state"] == "accessible"


# --- §11 least privilege for membership sync ------------------------------


async def test_membership_sync_requests_only_metadata(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)
    fake.token_requests.clear()

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    assert fake.token_requests, "a token must have been minted"
    for request in fake.token_requests:
        assert request.get("permissions") == {"metadata": "read"}, (
            f"membership sync asked for more than it needs: {request}"
        )


async def test_a_metadata_shortfall_blocks_membership_sync(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)
    before = await _repository(engine, LOGISLOT_ID)

    # The provider would answer 200, but it never granted metadata:read.
    fake.granted_permissions = {"actions": "read"}
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    with pytest.raises(service.PermissionShortfallError):
        await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    assert await _repository(engine, LOGISLOT_ID) == before


# --- §12 membership sync is not policy readiness --------------------------


async def test_membership_sync_does_not_make_a_new_repository_ready(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)

    fake.repositories["fresh"] = {
        "id": 952_002,
        "node_id": "R_fresh",
        "name": "fresh",
        "full_name": f"{OWNER}/fresh",
        "private": True,
        "visibility": "private",
        "archived": False,
        "disabled": False,
        "default_branch": "main",
    }
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    created = await _repository(engine, 952_002)
    assert created["onboarding_state"] == "discovered"
    assert created["reconciliation_state"] != "complete"


async def test_a_policy_relevant_change_takes_a_ready_repository_out_of_ready(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A default-branch change invalidates every branch-scoped verdict."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _repository(engine, HERMES_ID))["onboarding_state"] == "ready"

    fake.repositories["Hermes"]["default_branch"] = "trunk"
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    row = await _repository(engine, HERMES_ID)
    assert row["default_branch"] == "trunk"
    assert row["onboarding_state"] != "ready", (
        "the evidence was gathered against a branch that is no longer the default"
    )


async def test_an_unchanged_repository_keeps_its_last_good_state(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = await _scope_of_installation(engine)
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    before = await _repository(engine, HERMES_ID)
    assert before["onboarding_state"] == "ready"

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)

    after = await _repository(engine, HERMES_ID)
    assert after["onboarding_state"] == "ready"
    assert after["last_reconciled_at"] == before["last_reconciled_at"]


# --- §13 reconciliation job claims are exclusive --------------------------


async def test_two_workers_racing_one_job_run_it_once(engine: AsyncEngine, tmp_path: Path) -> None:
    """The lock is released when the claim transaction commits, so the
    exclusivity has to be a durable lease, not the lock."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(
                repositories=[
                    {
                        "id": 940_000 + index,
                        "node_id": "R_" + "k" * 126,
                        "name": "r" * 200,
                        "full_name": f"{OWNER}/" + "r" * (250 - len(OWNER)),
                        "private": True,
                    }
                    for index in range(100)
                ]
            ),
            str(uuidlib.uuid4()),
        )

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    runs = {"n": 0}
    real = reconciler.reconcile_installation

    async def counting(*args: Any, **kwargs: Any) -> Any:
        runs["n"] += 1
        await asyncio.sleep(0.05)
        return await real(*args, **kwargs)

    reconciler.reconcile_installation = counting  # type: ignore[method-assign]
    await asyncio.gather(
        service.drain_reconciliation_jobs(engine, reconciler),
        service.drain_reconciliation_jobs(engine, reconciler),
        return_exceptions=True,
    )
    assert runs["n"] == 1, f"the job ran {runs['n']} times; the claim is not exclusive"


async def test_a_lease_left_by_a_dead_worker_is_reclaimed(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(
                repositories=[
                    {
                        "id": 940_000 + index,
                        "node_id": "R_" + "k" * 126,
                        "name": "r" * 200,
                        "full_name": f"{OWNER}/" + "r" * (250 - len(OWNER)),
                        "private": True,
                    }
                    for index in range(100)
                ]
            ),
            str(uuidlib.uuid4()),
        )

    # Simulate a worker that claimed the job and then died.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE github_reconciliation_jobs "
                "SET lease_expires_at = now() - interval '1 minute', attempts = 1 "
                "WHERE status = 'pending'"
            )
        )

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    completed = await service.drain_reconciliation_jobs(engine, reconciler)
    assert completed == 1, "an expired lease must be reclaimable"


async def test_a_live_lease_is_not_stolen(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(
                repositories=[
                    {
                        "id": 940_000 + index,
                        "node_id": "R_" + "k" * 126,
                        "name": "r" * 200,
                        "full_name": f"{OWNER}/" + "r" * (250 - len(OWNER)),
                        "private": True,
                    }
                    for index in range(100)
                ]
            ),
            str(uuidlib.uuid4()),
        )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE github_reconciliation_jobs "
                "SET lease_expires_at = now() + interval '5 minutes' WHERE status = 'pending'"
            )
        )

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    assert await service.drain_reconciliation_jobs(engine, reconciler) == 0


async def test_a_terminal_job_is_never_reclaimed(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(
                repositories=[
                    {
                        "id": 940_000 + index,
                        "node_id": "R_" + "k" * 126,
                        "name": "r" * 200,
                        "full_name": f"{OWNER}/" + "r" * (250 - len(OWNER)),
                        "private": True,
                    }
                    for index in range(100)
                ]
            ),
            str(uuidlib.uuid4()),
        )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE github_reconciliation_jobs SET status = 'failed' WHERE status = 'pending'")
        )

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    assert await service.drain_reconciliation_jobs(engine, reconciler) == 0
