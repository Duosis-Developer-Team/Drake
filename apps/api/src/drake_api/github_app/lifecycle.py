"""Event/action lifecycle for GitHub App webhooks (ADR-0019 §6).

A delivery names two things: the EVENT (what kind of object changed) and
the ACTION (what happened to it). Collapsing them into a single comparison
makes unrelated situations equivalent — "one repository was removed from
the installation" and "the App was uninstalled" both contain the word
`removed`, and treating them the same deletes an installation because a
single repository left it.

This module owns that distinction. Every (event, action) pair maps to an
explicit plan, and a pair that is not in the allowlist produces no domain
mutation at all.
"""

from dataclasses import dataclass
from typing import Literal

# What the plan says to do with the installation row.
InstallationOutcome = Literal["active", "suspended", "deleted", "unchanged"]
# Which repositories the action is about.
RepositoryTarget = Literal["announced", "all_of_installation", "none"]
# What happens to those repositories.
RepositoryOutcome = Literal["present", "removed", "suspended", "restored", "none"]


@dataclass(frozen=True)
class LifecyclePlan:
    """The decided effect of one (event, action) pair."""

    supported: bool
    installation: InstallationOutcome = "unchanged"
    target: RepositoryTarget = "none"
    outcome: RepositoryOutcome = "none"
    # The action tells us something changed but not what it now is, so the
    # projection has to be re-derived from the provider before anything may
    # be called READY again.
    requires_reconciliation: bool = False
    reason: str = ""


_UNSUPPORTED = LifecyclePlan(supported=False, reason="action_unsupported")

# Only these pairs do domain work. Anything else is acknowledged, audited,
# and left alone — a future GitHub action must never be guessed at.
_PLANS: dict[tuple[str, str], LifecyclePlan] = {
    # --- installation: the App's relationship with the account ----------
    ("installation", "created"): LifecyclePlan(
        supported=True,
        installation="active",
        target="announced",
        outcome="present",
        reason="installation_created",
    ),
    # Suspension removes access without removing the relationship. Every
    # repository under the installation is affected, not just any the
    # payload happens to list.
    ("installation", "suspend"): LifecyclePlan(
        supported=True,
        installation="suspended",
        target="all_of_installation",
        outcome="suspended",
        reason="installation_suspended",
    ),
    # Access returns; compliance knowledge does not. Nothing goes back to
    # READY on the strength of an unsuspend event alone.
    ("installation", "unsuspend"): LifecyclePlan(
        supported=True,
        installation="active",
        target="all_of_installation",
        outcome="restored",
        requires_reconciliation=True,
        reason="installation_unsuspended",
    ),
    # An uninstall payload carries no repository list. Access is gone for
    # everything under it regardless, so the affected set comes from our
    # own rows rather than from the payload.
    ("installation", "deleted"): LifecyclePlan(
        supported=True,
        installation="deleted",
        target="all_of_installation",
        outcome="removed",
        reason="installation_deleted",
    ),
    ("installation", "new_permissions_accepted"): LifecyclePlan(
        supported=True,
        installation="active",
        target="none",
        outcome="none",
        requires_reconciliation=True,
        reason="installation_permissions_changed",
    ),
    # --- installation_repositories: which repositories are in scope -----
    ("installation_repositories", "added"): LifecyclePlan(
        supported=True,
        installation="unchanged",
        target="announced",
        outcome="present",
        reason="repositories_added",
    ),
    ("installation_repositories", "removed"): LifecyclePlan(
        supported=True,
        installation="unchanged",
        target="announced",
        outcome="removed",
        reason="repositories_removed",
    ),
    # --- repository: one repository's own attributes --------------------
    ("repository", "deleted"): LifecyclePlan(
        supported=True,
        installation="unchanged",
        target="announced",
        outcome="removed",
        reason="repository_deleted",
    ),
    ("repository", "transferred"): LifecyclePlan(
        supported=True,
        installation="unchanged",
        target="announced",
        outcome="present",
        requires_reconciliation=True,
        reason="repository_transferred",
    ),
}

# Attribute changes: same permanent id, new observed metadata, nothing
# structural. They share one plan.
_METADATA_ACTIONS = frozenset(
    {"created", "renamed", "edited", "archived", "unarchived", "privatized", "publicized"}
)
_METADATA_PLAN = LifecyclePlan(
    supported=True,
    installation="unchanged",
    target="announced",
    outcome="present",
    reason="repository_metadata_changed",
)


def plan_for(event: str, action: str) -> LifecyclePlan:
    """Decide what one delivery is allowed to change."""
    if event == "repository" and action in _METADATA_ACTIONS:
        return _METADATA_PLAN
    return _PLANS.get((event, action), _UNSUPPORTED)


def supported_actions(event: str) -> frozenset[str]:
    actions = {action for known_event, action in _PLANS if known_event == event}
    if event == "repository":
        actions |= set(_METADATA_ACTIONS)
    return frozenset(actions)
