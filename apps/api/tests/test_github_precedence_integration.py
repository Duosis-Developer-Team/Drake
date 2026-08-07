"""Installation state precedence, identity verification, and job leases.

CTO fix gate 3. Every test here failed on `6f4cb52`.

The theme of this round: a webhook is a *notification*, not a source of
truth. Treating one as though it re-established facts it never carried
let a repository event revive a deleted installation, blank an
installation's metadata, restore access under a suspended App, and promote
a repository back to READY after we had already learned we could not see
it properly.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import service
from drake_api.github_app.client import GitHubError
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


def _repo(external_id: int, name: str, owner: str = OWNER) -> dict[str, Any]:
    return {
        "id": external_id,
        "node_id": f"R_{name}",
        "name": name,
        "full_name": f"{owner}/{name}",
        "private": True,
    }


async def _installation(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT state, account_login, account_type, app_slug, "
                    "repository_selection, granted_permissions, subscribed_events, "
                    "last_reconciled_at, scope_id FROM github_installations "
                    "WHERE external_id = :id"
                ),
                {"id": INSTALLATION_ID},
            )
        ).one()
    return {
        "state": row[0],
        "account_login": row[1],
        "account_type": row[2],
        "app_slug": row[3],
        "repository_selection": row[4],
        "granted_permissions": row[5],
        "subscribed_events": row[6],
        "last_reconciled_at": row[7],
        "scope_id": row[8],
    }


async def _repository(engine: AsyncEngine, external_id: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT onboarding_state, access_state, full_name, name, "
                    "default_branch, security_gate, reconciliation_state, "
                    "last_reconciled_at, scope_id FROM github_repositories "
                    "WHERE external_id = :id"
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


async def _onboard(harness: Any) -> None:
    async with harness.api_client() as client:
        response = await deliver(
            client, "installation", installation_payload(), str(uuidlib.uuid4())
        )
        assert response.status_code == 202, response.text


async def _send(harness: Any, event: str, action: str, **extra: Any) -> Any:
    payload = installation_payload(action=action, repositories=None)
    payload.pop("repositories", None)
    payload.update(extra)
    async with harness.api_client() as client:
        return await deliver(client, event, payload, str(uuidlib.uuid4()))


# --- §3 "unchanged" really means unchanged --------------------------------


async def test_a_repository_event_does_not_revive_a_suspended_installation(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`unchanged` was written straight into the state column, and an
    invalid value was coerced to `active` — so a rename reactivated a
    suspended App."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _send(harness, "installation", "suspend")
    assert (await _installation(engine))["state"] == "suspended"

    await _send(harness, "repository", "renamed", repository=_repo(LOGISLOT_ID, "logislot-x"))
    assert (await _installation(engine))["state"] == "suspended"


async def test_a_repository_event_does_not_revive_a_deleted_installation(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _send(harness, "installation", "deleted")
    assert (await _installation(engine))["state"] == "deleted"

    # A stale delivery arriving after the uninstall must not resurrect it.
    await _send(harness, "repository", "edited", repository=_repo(LOGISLOT_ID, "logislot"))
    assert (await _installation(engine))["state"] == "deleted"


async def test_membership_events_never_change_installation_state(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _send(harness, "installation", "suspend")

    await _send(
        harness,
        "installation_repositories",
        "added",
        repositories_added=[_repo(LOGISLOT_ID, "logislot")],
    )
    assert (await _installation(engine))["state"] == "suspended"

    await _send(
        harness,
        "installation_repositories",
        "removed",
        repositories_removed=[_repo(LOGISLOT_ID, "logislot")],
    )
    assert (await _installation(engine))["state"] == "suspended"


async def test_an_unknown_action_never_changes_installation_state(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _send(harness, "installation", "suspend")
    await _send(harness, "repository", "some_future_action", repository=_repo(LOGISLOT_ID, "x"))
    assert (await _installation(engine))["state"] == "suspended"


# --- §4 a webhook must not blank installation metadata --------------------


async def test_a_repository_webhook_preserves_installation_metadata(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The webhook envelope carries none of these fields, so overwriting
    them with defaults destroys what a real reconciliation established."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE github_installations SET account_type = 'Organization', "
                "app_slug = 'drake', repository_selection = 'all', "
                'granted_permissions = \'{"metadata": "read"}\'::jsonb, '
                "subscribed_events = '[\"installation\"]'::jsonb, "
                "account_external_id = 4242 WHERE external_id = :id"
            ),
            {"id": INSTALLATION_ID},
        )
    before = await _installation(engine)

    await _send(harness, "repository", "renamed", repository=_repo(LOGISLOT_ID, "logislot-y"))

    after = await _installation(engine)
    assert after["account_type"] == before["account_type"] == "Organization"
    assert after["app_slug"] == before["app_slug"] == "drake"
    assert after["repository_selection"] == before["repository_selection"] == "all"
    assert after["granted_permissions"] == before["granted_permissions"]
    assert after["subscribed_events"] == before["subscribed_events"]


async def test_membership_events_preserve_installation_metadata(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE github_installations SET app_slug = 'drake', "
                'granted_permissions = \'{"metadata": "read"}\'::jsonb '
                "WHERE external_id = :id"
            ),
            {"id": INSTALLATION_ID},
        )
    await _send(
        harness,
        "installation_repositories",
        "added",
        repositories_added=[_repo(LOGISLOT_ID, "logislot")],
    )
    after = await _installation(engine)
    assert after["app_slug"] == "drake"
    assert after["granted_permissions"] == {"metadata": "read"}


async def test_a_webhook_never_stamps_installation_reconciliation(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Receiving a notification is not the same as having re-read the App."""
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    assert (await _installation(engine))["last_reconciled_at"] is None

    await _send(harness, "repository", "renamed", repository=_repo(LOGISLOT_ID, "logislot-z"))
    assert (await _installation(engine))["last_reconciled_at"] is None


async def test_a_complete_installation_reconciliation_does_stamp(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = (await _installation(engine))["scope_id"]

    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)
    assert (await _installation(engine))["last_reconciled_at"] is not None


async def test_a_failed_installation_reconciliation_does_not_stamp(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    scope_id = (await _installation(engine))["scope_id"]

    fake.mode = "unavailable"
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    with pytest.raises(GitHubError):
        await reconciler.reconcile_installation(INSTALLATION_ID, scope_id=scope_id)
    assert (await _installation(engine))["last_reconciled_at"] is None


# --- §5 parent installation state bounds repository access ----------------


async def test_a_metadata_event_does_not_restore_access_under_a_suspended_app(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _send(harness, "installation", "suspend")
    assert (await _repository(engine, LOGISLOT_ID))["access_state"] == "suspended"

    await _send(harness, "repository", "renamed", repository=_repo(LOGISLOT_ID, "logislot-r"))
    after = await _repository(engine, LOGISLOT_ID)
    assert after["access_state"] == "suspended"
    assert after["onboarding_state"] == "disabled"


async def test_a_metadata_event_does_not_restore_access_under_a_deleted_app(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _send(harness, "installation", "deleted")

    await _send(harness, "repository", "edited", repository=_repo(LOGISLOT_ID, "logislot"))
    after = await _repository(engine, LOGISLOT_ID)
    assert after["access_state"] == "removed"
    assert after["onboarding_state"] == "disabled"


async def test_a_stale_rename_does_not_undo_a_repository_removal(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _send(
        harness,
        "installation_repositories",
        "removed",
        repositories_removed=[_repo(LOGISLOT_ID, "logislot")],
    )
    assert (await _repository(engine, LOGISLOT_ID))["access_state"] == "removed"

    await _send(harness, "repository", "renamed", repository=_repo(LOGISLOT_ID, "logislot-late"))
    assert (await _repository(engine, LOGISLOT_ID))["access_state"] == "removed"


async def test_unsuspend_restores_access_without_readiness(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    await _send(harness, "installation", "suspend")
    await _send(harness, "installation", "unsuspend")

    after = await _repository(engine, LOGISLOT_ID)
    assert after["access_state"] == "accessible"
    assert after["onboarding_state"] == "discovered"


async def test_no_lifecycle_observation_unblocks_a_gated_repository(
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
                    _repo(DATALAKE_ID, "Datalake-Platform-GUI"),
                    _repo(HERMES_ID, "Hermes"),
                ]
            ),
            str(uuidlib.uuid4()),
        )
    assert (await _repository(engine, DATALAKE_ID))["onboarding_state"] == "blocked"

    for event, action, extra in (
        ("installation", "suspend", {}),
        ("installation", "unsuspend", {}),
        ("repository", "renamed", {"repository": _repo(DATALAKE_ID, "Datalake-Platform-GUI")}),
        (
            "installation_repositories",
            "added",
            {"repositories_added": [_repo(DATALAKE_ID, "Datalake-Platform-GUI")]},
        ),
    ):
        await _send(harness, event, action, **extra)
        assert (await _repository(engine, DATALAKE_ID))["onboarding_state"] == "blocked", (
            f"{event}.{action} moved a gated repository out of BLOCKED"
        )


# --- §14 unsupported actions are honest -----------------------------------


async def test_an_unsupported_action_is_reported_as_unsupported(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """One exact contract, asserted exactly.

    The previous test used `!= "processed" or True`, which is a tautology
    and passed regardless of what the endpoint did — while the endpoint
    reported `processed` and wrote a success audit for work it refused.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)
    before_state = await _repository(engine, HERMES_ID)
    success_before = await _audits(engine, "github.webhook.installation")
    unsupported_before = await _audits(engine, "github.webhook.action_unsupported")

    response = await _send(harness, "installation", "some_future_action")

    assert response.status_code == 202
    assert response.json()["status"] == "unsupported"
    assert await _audits(engine, "github.webhook.installation") == success_before
    assert await _audits(engine, "github.webhook.action_unsupported") == unsupported_before + 1
    assert await _repository(engine, HERMES_ID) == before_state


async def test_redelivering_an_unsupported_action_is_deterministic(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    await _onboard(harness)

    payload = installation_payload(action="some_future_action")
    delivery = str(uuidlib.uuid4())
    async with harness.api_client() as client:
        first = await deliver(client, "installation", payload, delivery)
        second = await deliver(client, "installation", payload, delivery)

    assert first.status_code == 202
    assert first.json()["status"] == "unsupported"
    assert second.status_code == 202
    assert second.json()["status"] == "duplicate"
