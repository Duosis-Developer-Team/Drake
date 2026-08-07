"""Repository onboarding state machine (ADR-0020 §2/§3).

One module owns the state column. Every transition is explicit, audited
with a bounded reason code, and reversible in the sense that data is
never deleted — losing access moves a repository to DISABLED and keeps
its history.
"""

from dataclasses import dataclass
from typing import Literal

OnboardingState = Literal["discovered", "validating", "ready", "blocked", "degraded", "disabled"]

DISCOVERED: OnboardingState = "discovered"
VALIDATING: OnboardingState = "validating"
READY: OnboardingState = "ready"
BLOCKED: OnboardingState = "blocked"
DEGRADED: OnboardingState = "degraded"
DISABLED: OnboardingState = "disabled"

STATES: tuple[OnboardingState, ...] = (
    DISCOVERED,
    VALIDATING,
    READY,
    BLOCKED,
    DEGRADED,
    DISABLED,
)

# Allowed transitions. DISABLED is reachable from everywhere (access can
# vanish at any moment) and recoverable only through rediscovery.
_ALLOWED: dict[OnboardingState, frozenset[OnboardingState]] = {
    # DEGRADED is reachable directly: a reconciliation that fails at the
    # provider leaves a discovered repository degraded, and routing that
    # through VALIDATING would only be bookkeeping.
    DISCOVERED: frozenset({VALIDATING, DEGRADED, BLOCKED, DISABLED, DISCOVERED}),
    VALIDATING: frozenset({READY, DEGRADED, BLOCKED, DISABLED, VALIDATING}),
    READY: frozenset({VALIDATING, DEGRADED, BLOCKED, DISABLED, READY}),
    DEGRADED: frozenset({VALIDATING, READY, BLOCKED, DISABLED, DEGRADED}),
    BLOCKED: frozenset({VALIDATING, DISABLED, BLOCKED, DISCOVERED}),
    DISABLED: frozenset({DISCOVERED, DISABLED}),
}


class InvalidTransitionError(ValueError):
    """A transition the state machine does not allow."""


@dataclass(frozen=True)
class Transition:
    previous: OnboardingState
    next: OnboardingState
    reason: str
    changed: bool


def can_transition(current: OnboardingState, target: OnboardingState) -> bool:
    return target in _ALLOWED.get(current, frozenset())


def transition(current: OnboardingState, target: OnboardingState, reason: str) -> Transition:
    """The single entry point for a state change.

    A no-op transition (same state) is legal and reported as unchanged so
    callers can stay idempotent without special-casing.
    """
    if target not in STATES:
        raise InvalidTransitionError(f"unknown onboarding state: {target}")
    if not can_transition(current, target):
        raise InvalidTransitionError(f"{current} cannot transition to {target}")
    return Transition(previous=current, next=target, reason=reason, changed=current != target)


def resolve_effective(
    *,
    security_gate: str | None,
    installation_state: str,
    access_state: str,
    reconciliation_state: str,
    had_error: bool = False,
) -> tuple[OnboardingState, str]:
    """The single precedence chain for a repository's state.

    Ordered most-authoritative first, because each level describes a
    stronger reason to stop than the one below it:

        security gate > installation deleted > installation suspended >
        repository access removed > reconciliation incomplete > accessible

    A weaker observation can never override a stronger one. That is what
    stops a repository-metadata webhook from restoring access under a
    suspended App, or a rename from re-opening a manual security gate.
    """
    if security_gate:
        return BLOCKED, f"security_gate_{security_gate}"
    if installation_state == "deleted":
        return DISABLED, "installation_deleted"
    if installation_state == "suspended":
        return DISABLED, "installation_suspended"
    if access_state in ("removed", "suspended"):
        return DISABLED, f"access_{access_state}"
    if had_error or reconciliation_state == "failed":
        return DEGRADED, "reconciliation_error"
    if reconciliation_state in ("partial", "stale"):
        return DEGRADED, f"evidence_{reconciliation_state}"
    if reconciliation_state == "complete":
        return READY, "reconciled"
    return DISCOVERED, "awaiting_reconciliation"


def resolve_target(
    *,
    security_gate: str | None,
    access_state: str,
    reconciled: bool,
    had_error: bool,
) -> tuple[OnboardingState, str]:
    """Derive the state a repository SHOULD be in, with its reason code.

    Precedence is deliberate and fail-closed:
    1. an open manual security gate outranks everything;
    2. lost access is DISABLED (soft state, history preserved);
    3. an error after a successful reconcile is DEGRADED, not BLOCKED —
       transient failure must not look like a policy decision;
    4. otherwise READY once reconciled, DISCOVERED before that.
    """
    if security_gate:
        return BLOCKED, f"security_gate_{security_gate}"
    if access_state in ("removed", "suspended"):
        return DISABLED, f"access_{access_state}"
    if had_error:
        return DEGRADED, "reconciliation_error"
    if reconciled:
        return READY, "reconciled"
    return DISCOVERED, "awaiting_reconciliation"
