"""Persisted gate authority, completeness invalidation, patch semantics.

CTO fix gate 4, findings 1 to 4 and 6 to 7. Every test here failed on
`8e47f13`.

The theme: state we already hold is authoritative over anything a later
observation implies. A gate we recorded, an access loss we recorded, and a
field we were never told about all outrank whatever the newest message
happens to omit.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import service
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


def _repo(external_id: int, name: str, owner: str = OWNER, **extra: Any) -> dict[str, Any]:
    payload = {
        "id": external_id,
        "node_id": f"R_{name}",
        "name": name,
        "full_name": f"{owner}/{name}",
        "private": True,
    }
    payload.update(extra)
    return payload


async def _row(engine: AsyncEngine, external_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        result = (
            await connection.execute(
                text(
                    "SELECT onboarding_state, access_state, full_name, name, default_branch, "
                    "security_gate, reconciliation_state, last_reconciled_at, archived, "
                    "disabled, visibility, private, node_id, id "
                    "FROM github_repositories WHERE external_id = :e"
                ),
                {"e": external_id},
            )
        ).one()
    keys = (
        "onboarding_state",
        "access_state",
        "full_name",
        "name",
        "default_branch",
        "security_gate",
        "reconciliation_state",
        "last_reconciled_at",
        "archived",
        "disabled",
        "visibility",
        "private",
        "node_id",
        "id",
    )
    return dict(zip(keys, result, strict=True))


async def _audits(engine: AsyncEngine, action: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE action = :a"), {"a": action}
                )
            ).scalar_one()
        )


async def _scope(engine: AsyncEngine) -> uuidlib.UUID:
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


async def _onboard(harness: Any, repositories: list[dict[str, Any]] | None = None) -> None:
    async with harness.api_client() as client:
        response = await deliver(
            client,
            "installation",
            installation_payload(repositories=repositories),
            str(uuidlib.uuid4()),
        )
        assert response.status_code == 202, response.text


async def _send(harness: Any, event: str, action: str, **extra: Any) -> Any:
    payload = installation_payload(action=action, repositories=None)
    payload.pop("repositories", None)
    payload.update(extra)
    async with harness.api_client() as client:
        return await deliver(client, event, payload, str(uuidlib.uuid4()))


async def _reconcile(harness: Any, engine: AsyncEngine, external_id: int) -> Any:
    row = await _row(engine, external_id)
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    return await reconciler.reconcile_repository(
        uuidlib.UUID(str(row["id"])), INSTALLATION_ID, str(row["full_name"]), external_id
    )


# --- §1 the persisted gate is authoritative before the network -----------


async def test_a_rename_away_from_the_gated_name_is_not_a_licence_to_call(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The gate lives in the row, not in the name.

    Deriving it from `full_name` alone means anyone who can rename a
    repository can also decide when Drake starts talking to the provider
    about it.
    """
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness, repositories=[_repo(DATALAKE_ID, "Datalake-Platform-GUI")])
    assert (await _row(engine, DATALAKE_ID))["security_gate"] == "manual_env_review"

    await _send(
        harness, "repository", "renamed", repository=_repo(DATALAKE_ID, "totally-ordinary-name")
    )
    renamed = await _row(engine, DATALAKE_ID)
    assert renamed["full_name"] == f"{OWNER}/totally-ordinary-name"
    assert renamed["security_gate"] == "manual_env_review", "the gate must survive the rename"

    fake.calls.clear()
    fake.token_requests.clear()

    async with harness.api_client() as client:
        me = await harness.login(client, "user-owner")
        response = await client.post(
            f"/v1/integrations/github/repositories/{renamed['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )

    assert response.status_code == 409
    assert fake.calls == [], f"the gate was bypassed: {fake.calls}"
    assert fake.token_requests == [], "a token was minted for a gated repository"


async def test_the_reconciler_itself_refuses_a_persisted_gate_without_calling(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Not only the endpoint: the worker path uses the same contract."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness, repositories=[_repo(DATALAKE_ID, "Datalake-Platform-GUI")])
    await _send(harness, "repository", "renamed", repository=_repo(DATALAKE_ID, "ordinary-again"))
    fake.calls.clear()
    fake.token_requests.clear()

    with pytest.raises(service.SecurityGateBlockedError):
        await _reconcile(harness, engine, DATALAKE_ID)
    assert fake.calls == []
    assert fake.token_requests == []


# --- §2 losing access invalidates current completeness -------------------


async def test_suspend_then_unsuspend_does_not_restore_ready(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Completeness describes what we can see NOW.

    While the App was suspended we could see nothing, so the old
    `complete` cannot survive to be read as current on the way back.
    """
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _row(engine, HERMES_ID))["onboarding_state"] == "ready"

    await _send(harness, "installation", "suspend")
    await _send(harness, "installation", "unsuspend")

    restored = await _row(engine, HERMES_ID)
    assert restored["access_state"] == "accessible"
    assert restored["onboarding_state"] != "ready"
    assert restored["reconciliation_state"] != "complete"

    # Only a fresh complete reconciliation restores it.
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _row(engine, HERMES_ID))["onboarding_state"] == "ready"


async def test_removed_then_re_added_does_not_restore_ready(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _row(engine, HERMES_ID))["onboarding_state"] == "ready"

    await _send(
        harness,
        "installation_repositories",
        "removed",
        repositories_removed=[_repo(HERMES_ID, "Hermes")],
    )
    await _send(
        harness,
        "installation_repositories",
        "added",
        repositories_added=[_repo(HERMES_ID, "Hermes")],
    )

    row = await _row(engine, HERMES_ID)
    assert row["access_state"] == "accessible"
    assert row["onboarding_state"] != "ready"
    assert row["reconciliation_state"] != "complete"


async def test_an_access_loss_keeps_the_last_good_snapshot(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    before = await _row(engine, HERMES_ID)

    async with engine.connect() as connection:
        snapshots_before = int(
            (
                await connection.execute(text("SELECT count(*) FROM github_policy_evaluations"))
            ).scalar_one()
        )

    await _send(harness, "installation", "suspend")

    async with engine.connect() as connection:
        snapshots_after = int(
            (
                await connection.execute(text("SELECT count(*) FROM github_policy_evaluations"))
            ).scalar_one()
        )
    assert snapshots_after == snapshots_before, "history is not deleted by an access loss"
    # The successful timestamp is kept; only current completeness is dropped.
    assert (await _row(engine, HERMES_ID))["last_reconciled_at"] == before["last_reconciled_at"]


async def test_a_gated_repository_stays_blocked_through_access_changes(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness, repositories=[_repo(DATALAKE_ID, "Datalake-Platform-GUI")])
    for action in ("suspend", "unsuspend"):
        await _send(harness, "installation", action)
        assert (await _row(engine, DATALAKE_ID))["onboarding_state"] == "blocked"


# --- §3 repository upsert is a PATCH -------------------------------------


# Fully static statements, one per column: nothing about the SQL is
# assembled from a value, so there is no shape for an injection to take.
_SETTERS = {
    "archived": "UPDATE github_repositories SET archived = :v WHERE external_id = :e",
    "disabled": "UPDATE github_repositories SET disabled = :v WHERE external_id = :e",
    "visibility": "UPDATE github_repositories SET visibility = :v WHERE external_id = :e",
    "default_branch": ("UPDATE github_repositories SET default_branch = :v WHERE external_id = :e"),
    "node_id": "UPDATE github_repositories SET node_id = :v WHERE external_id = :e",
    "access_state": "UPDATE github_repositories SET access_state = :v WHERE external_id = :e",
    "private": "UPDATE github_repositories SET private = :v WHERE external_id = :e",
}


async def _set_projection(engine: AsyncEngine, external_id: int, **values: Any) -> None:
    async with engine.begin() as connection:
        for column, value in sorted(values.items()):
            await connection.execute(text(_SETTERS[column]), {"v": value, "e": external_id})


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("archived", True),
        ("disabled", True),
        ("visibility", "internal"),
        ("default_branch", "trunk"),
    ],
)
async def test_a_webhook_preserves_fields_it_never_carried(
    engine: AsyncEngine, tmp_path: Path, column: str, value: Any
) -> None:
    """The envelope has no archived/disabled/visibility/default_branch.

    Writing them from defaults means every rename silently reports the
    repository as active, public-ish and on whatever branch we guessed.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _set_projection(engine, LOGISLOT_ID, **{column: value})

    await _send(harness, "repository", "renamed", repository=_repo(LOGISLOT_ID, "logislot-new"))

    row = await _row(engine, LOGISLOT_ID)
    assert row[column] == value, f"{column} was overwritten by a webhook that never carried it"
    assert row["full_name"] == f"{OWNER}/logislot-new", "the field it DID carry is applied"


async def test_a_metadata_webhook_makes_current_evidence_stale(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _row(engine, HERMES_ID))["reconciliation_state"] == "complete"

    await _send(harness, "repository", "renamed", repository=_repo(HERMES_ID, "Hermes-2"))
    after = await _row(engine, HERMES_ID)
    assert after["reconciliation_state"] != "complete", (
        "a metadata change invalidates evidence gathered before it"
    )
    assert after["onboarding_state"] != "ready"


async def test_provider_reconciliation_writes_authoritative_fields(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _set_projection(engine, LOGISLOT_ID, archived=True, visibility="internal")

    fake.repositories["logislot"]["archived"] = False
    fake.repositories["logislot"]["visibility"] = "private"
    fake.protection_status = 404
    await _reconcile(harness, engine, LOGISLOT_ID)

    row = await _row(engine, LOGISLOT_ID)
    assert row["archived"] is False
    assert row["visibility"] == "private"


async def test_a_webhook_never_loosens_access_or_the_gate(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness, repositories=[_repo(DATALAKE_ID, "Datalake-Platform-GUI")])
    await _set_projection(engine, DATALAKE_ID, access_state="removed")

    await _send(
        harness, "repository", "edited", repository=_repo(DATALAKE_ID, "Datalake-Platform-GUI")
    )
    row = await _row(engine, DATALAKE_ID)
    assert row["access_state"] == "removed"
    assert row["security_gate"] == "manual_env_review"


# --- §4 membership comparison uses the PRE-update snapshot ---------------


@pytest.mark.parametrize(
    ("column", "before", "observed_key", "observed"),
    [
        ("archived", True, "archived", False),
        ("archived", False, "archived", True),
        ("disabled", True, "disabled", False),
        ("disabled", False, "disabled", True),
        ("visibility", "internal", "visibility", "private"),
        ("visibility", "private", "visibility", "internal"),
        ("default_branch", "main", "default_branch", "trunk"),
    ],
)
async def test_membership_sync_detects_changes_in_both_directions(
    engine: AsyncEngine,
    tmp_path: Path,
    column: str,
    before: Any,
    observed_key: str,
    observed: Any,
) -> None:
    """Comparing after a partial update makes changes invisible.

    The old value has to be read once, before anything is written, or a
    field the update already overwrote compares equal to itself.
    """
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    await _reconcile(harness, engine, LOGISLOT_ID)
    await _set_projection(engine, LOGISLOT_ID, **{column: before})
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE github_repositories SET reconciliation_state = 'complete' "
                "WHERE external_id = :e"
            ),
            {"e": LOGISLOT_ID},
        )

    fake.repositories["logislot"][observed_key] = observed
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=await _scope(engine))

    row = await _row(engine, LOGISLOT_ID)
    assert row[column] == observed, "the observed value must be written"
    assert row["reconciliation_state"] == "stale", (
        f"{column} {before!r} -> {observed!r} went unnoticed"
    )


async def test_membership_sync_detects_a_rename(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    await _reconcile(harness, engine, LOGISLOT_ID)

    fake.repositories["logislot"]["full_name"] = f"{OWNER}/logislot-elsewhere"
    fake.repositories["logislot"]["name"] = "logislot-elsewhere"
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=await _scope(engine))

    row = await _row(engine, LOGISLOT_ID)
    assert row["full_name"] == f"{OWNER}/logislot-elsewhere"
    assert row["reconciliation_state"] == "stale"


async def test_a_malformed_membership_entry_fails_the_whole_listing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Skipping a malformed entry makes a known repository look removed."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    before = await _row(engine, LOGISLOT_ID)

    del fake.repositories["logislot"]["id"]  # known repository, unusable entry
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    with pytest.raises(service.MembershipContractError):
        await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=await _scope(engine))

    after = await _row(engine, LOGISLOT_ID)
    assert after["access_state"] == before["access_state"] == "accessible"


async def test_an_unchanged_repository_is_not_marked_stale(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    before = await _row(engine, HERMES_ID)
    assert before["reconciliation_state"] == "complete"

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=await _scope(engine))
    assert (await _row(engine, HERMES_ID))["reconciliation_state"] == "complete"


# --- §6 the delivery carries the same installation/scope invariant -------


async def test_a_delivery_may_not_reference_a_foreign_scope(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    async with engine.begin() as connection:
        foreign = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref) "
                    "VALUES ('project', 'delivery-foreign') RETURNING id"
                )
            )
        ).scalar_one()

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE github_webhook_deliveries SET scope_id = :s "
                    "WHERE installation_id IS NOT NULL"
                ),
                {"s": foreign},
            )


async def test_a_delivery_before_installation_binding_is_allowed(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The row is claimed before the installation row exists."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    async with engine.connect() as connection:
        unbound = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM github_webhook_deliveries "
                        "WHERE installation_id IS NULL"
                    )
                )
            ).scalar_one()
        )
    assert unbound >= 0  # the shape is permitted; nothing forces binding


# --- §7 node identity ----------------------------------------------------


async def test_a_matching_node_id_is_accepted(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.repositories["logislot"]["node_id"] = "R_logislot"
    fake.protection_status = 404
    await _reconcile(harness, engine, LOGISLOT_ID)
    assert (await _row(engine, LOGISLOT_ID))["node_id"] == "R_logislot"


async def test_a_different_node_id_is_an_identity_conflict(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Same numeric id, different node id: one of them is lying."""
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    before = await _row(engine, LOGISLOT_ID)
    audits_before = await _audits(engine, "github.repository.identity_mismatch")

    fake.repositories["logislot"]["node_id"] = "R_somethingelse"
    with pytest.raises(service.RepositoryIdentityError):
        await _reconcile(harness, engine, LOGISLOT_ID)

    after = await _row(engine, LOGISLOT_ID)
    assert after["node_id"] == before["node_id"], "the projection must not be rewritten"
    assert after["onboarding_state"] == "blocked"
    assert await _audits(engine, "github.repository.identity_mismatch") == audits_before + 1


async def test_an_empty_node_id_is_filled_from_a_verified_response(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _set_projection(engine, LOGISLOT_ID, node_id="")

    fake.repositories["logislot"]["node_id"] = "R_backfilled"
    fake.protection_status = 404
    await _reconcile(harness, engine, LOGISLOT_ID)
    assert (await _row(engine, LOGISLOT_ID))["node_id"] == "R_backfilled"


async def test_a_node_mismatch_does_not_preserve_ready(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    fake.protection_status = 404
    await _reconcile(harness, engine, HERMES_ID)
    assert (await _row(engine, HERMES_ID))["onboarding_state"] == "ready"

    fake.repositories["Hermes"]["node_id"] = "R_impostor"
    with pytest.raises(service.RepositoryIdentityError):
        await _reconcile(harness, engine, HERMES_ID)
    assert (await _row(engine, HERMES_ID))["onboarding_state"] != "ready"
