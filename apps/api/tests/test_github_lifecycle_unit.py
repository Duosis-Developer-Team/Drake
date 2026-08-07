"""The (event, action) plan matrix, as a table.

CTO fix gate 2, finding 6. These assertions are the specification: if a
plan changes, the change has to be made here first, deliberately.
"""

import pytest
from drake_api.github_app import lifecycle


@pytest.mark.parametrize(
    ("event", "action", "installation", "target", "outcome"),
    [
        # An installation event is about the App's relationship.
        ("installation", "created", "active", "announced", "present"),
        ("installation", "suspend", "suspended", "all_of_installation", "suspended"),
        ("installation", "unsuspend", "active", "all_of_installation", "restored"),
        ("installation", "deleted", "deleted", "all_of_installation", "removed"),
        ("installation", "new_permissions_accepted", "active", "none", "none"),
        # A membership event is about which repositories are in scope, and
        # says nothing about the installation itself.
        ("installation_repositories", "added", "unchanged", "announced", "present"),
        ("installation_repositories", "removed", "unchanged", "announced", "removed"),
        # A repository event is about one repository.
        ("repository", "deleted", "unchanged", "announced", "removed"),
        ("repository", "renamed", "unchanged", "announced", "present"),
        ("repository", "transferred", "unchanged", "announced", "present"),
        ("repository", "archived", "unchanged", "announced", "present"),
        ("repository", "privatized", "unchanged", "announced", "present"),
    ],
)
def test_the_plan_matrix(
    event: str, action: str, installation: str, target: str, outcome: str
) -> None:
    plan = lifecycle.plan_for(event, action)
    assert plan.supported is True
    assert plan.installation == installation
    assert plan.target == target
    assert plan.outcome == outcome


def test_removing_a_repository_never_touches_the_installation() -> None:
    """The collapse this module exists to prevent.

    Both of these carry the word "removed"/"deleted"; only one of them is
    about the installation.
    """
    repo_removal = lifecycle.plan_for("installation_repositories", "removed")
    uninstall = lifecycle.plan_for("installation", "deleted")
    assert repo_removal.installation == "unchanged"
    assert repo_removal.target == "announced"
    assert uninstall.installation == "deleted"
    assert uninstall.target == "all_of_installation"


def test_an_uninstall_covers_repositories_the_payload_never_lists() -> None:
    plan = lifecycle.plan_for("installation", "deleted")
    assert plan.target == "all_of_installation", (
        "an uninstall payload carries no repository list, but access is gone for all of them"
    )


def test_regaining_access_still_requires_reconciliation() -> None:
    plan = lifecycle.plan_for("installation", "unsuspend")
    assert plan.outcome == "restored"
    assert plan.requires_reconciliation is True


@pytest.mark.parametrize(
    ("event", "action"),
    [
        ("installation", "some_future_action"),
        ("installation", "removed"),
        ("installation_repositories", "deleted"),
        ("installation_repositories", "suspend"),
        ("repository", "starred"),
        ("push", "created"),
        ("", ""),
    ],
)
def test_unknown_pairs_are_unsupported_and_change_nothing(event: str, action: str) -> None:
    plan = lifecycle.plan_for(event, action)
    assert plan.supported is False
    assert plan.installation == "unchanged"
    assert plan.target == "none"
    assert plan.outcome == "none"


def test_the_allowlist_is_explicit_per_event() -> None:
    assert "created" in lifecycle.supported_actions("installation")
    assert "added" in lifecycle.supported_actions("installation_repositories")
    # A membership action is not an installation action, and vice versa.
    assert "added" not in lifecycle.supported_actions("installation")
    assert "suspend" not in lifecycle.supported_actions("installation_repositories")
    assert lifecycle.supported_actions("push") == frozenset()


# --- fix gate 3: the precedence chain, as a table -------------------------


@pytest.mark.parametrize(
    ("gate", "installation", "access", "reconciliation", "expected"),
    [
        # A security gate outranks everything below it.
        ("manual_env_review", "active", "accessible", "complete", "blocked"),
        ("manual_env_review", "deleted", "removed", "never", "blocked"),
        # Then the installation's own state.
        (None, "deleted", "accessible", "complete", "disabled"),
        (None, "suspended", "accessible", "complete", "disabled"),
        # Then the repository's access.
        (None, "active", "removed", "complete", "disabled"),
        (None, "active", "suspended", "complete", "disabled"),
        # Then how complete the current evidence is.
        (None, "active", "accessible", "failed", "degraded"),
        (None, "active", "accessible", "partial", "degraded"),
        (None, "active", "accessible", "stale", "degraded"),
        (None, "active", "accessible", "never", "discovered"),
        # Only a complete, current projection is READY.
        (None, "active", "accessible", "complete", "ready"),
    ],
)
def test_the_precedence_chain(
    gate: str | None, installation: str, access: str, reconciliation: str, expected: str
) -> None:
    from drake_api.github_app import onboarding

    state, _reason = onboarding.resolve_effective(
        security_gate=gate,
        installation_state=installation,
        access_state=access,
        reconciliation_state=reconciliation,
    )
    assert state == expected


def test_a_weaker_observation_never_overrides_a_stronger_reason() -> None:
    """The property the chain exists to guarantee."""
    from drake_api.github_app import onboarding

    # Complete evidence does not make a suspended App's repository ready.
    suspended, _ = onboarding.resolve_effective(
        security_gate=None,
        installation_state="suspended",
        access_state="accessible",
        reconciliation_state="complete",
    )
    assert suspended == "disabled"
    # And regained access does not make partial evidence complete.
    partial, _ = onboarding.resolve_effective(
        security_gate=None,
        installation_state="active",
        access_state="accessible",
        reconciliation_state="partial",
    )
    assert partial == "degraded"


def test_a_failed_provider_read_can_degrade_a_discovered_repository() -> None:
    """Without this the failure path raised instead of recording anything.

    A reconciliation that never got an answer leaves the repository
    degraded; making that legal only from VALIDATING meant the honest
    outcome depended on a bookkeeping step having run first.
    """
    from drake_api.github_app import onboarding

    assert onboarding.can_transition(onboarding.DISCOVERED, onboarding.DEGRADED)
    change = onboarding.transition(
        onboarding.DISCOVERED, onboarding.DEGRADED, "reconciliation_error"
    )
    assert change.next == onboarding.DEGRADED
    assert change.changed is True
