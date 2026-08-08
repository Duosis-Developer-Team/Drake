"""What "protected" means, decided in one place.

The rule this whole module exists to enforce:

    backup job success  ≠  artifact exists
    artifact exists     ≠  artifact is valid
    valid artifact      ≠  offsite protection
    offsite backup      ≠  verified recoverability

Each of those is a separate question with separate evidence, so the answer
is two axes rather than one. `backup_state` is about whether a usable copy
exists; `recoverability_state` is about whether anyone has proved it can be
restored. A green job with no artifact is not protected, and a perfect
artifact nobody has ever restored is not verified.

Pure and deterministic: evidence in, verdict out. No I/O, no clock beyond
the `now` it is handed, and every computation in UTC — a viewer's timezone
may change how a timestamp is displayed, never what it means.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class BackupState(StrEnum):
    PROTECTED = "protected"
    AT_RISK = "at_risk"
    OVERDUE = "overdue"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RecoverabilityState(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    FAILED = "failed"
    UNKNOWN = "unknown"


class OverallState(StrEnum):
    RECOVERABLE_VERIFIED = "recoverable_verified"
    PROTECTED_UNVERIFIED = "protected_unverified"
    AT_RISK = "at_risk"
    OVERDUE = "overdue"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ReasonCode(StrEnum):
    """Machine-readable causes. The UI maps these to text; it never parses."""

    BACKUP_OVERDUE = "backup_overdue"
    LATEST_RUN_FAILED = "latest_run_failed"
    ARTIFACT_MISSING = "artifact_missing"
    INTEGRITY_MISSING = "integrity_missing"
    INTEGRITY_FAILED = "integrity_failed"
    OFFSITE_MISSING = "offsite_missing"
    RESTORE_NEVER_VERIFIED = "restore_never_verified"
    RESTORE_VERIFICATION_EXPIRED = "restore_verification_expired"
    RESTORE_FAILED = "restore_failed"
    RTO_EXCEEDED = "rto_exceeded"
    REPORTER_STALE = "reporter_stale"


REASON_TEXT: dict[str, str] = {
    ReasonCode.BACKUP_OVERDUE: "The newest successful backup is older than this policy's RPO.",
    ReasonCode.LATEST_RUN_FAILED: "The most recent backup attempt failed.",
    ReasonCode.ARTIFACT_MISSING: (
        "The backup job reported success, but no artifact has been observed for it."
    ),
    ReasonCode.INTEGRITY_MISSING: (
        "This policy requires an integrity check and none has been recorded."
    ),
    ReasonCode.INTEGRITY_FAILED: "The artifact's integrity check did not pass.",
    ReasonCode.OFFSITE_MISSING: (
        "This policy requires an offsite copy and none has been observed."
    ),
    ReasonCode.RESTORE_NEVER_VERIFIED: "No successful restore drill has ever been recorded.",
    ReasonCode.RESTORE_VERIFICATION_EXPIRED: (
        "The last successful restore drill is older than this policy allows."
    ),
    ReasonCode.RESTORE_FAILED: "The most recent restore drill failed.",
    ReasonCode.RTO_EXCEEDED: "The restore drill succeeded but took longer than the RTO.",
    ReasonCode.REPORTER_STALE: (
        "The connector has not reported recently enough to trust this assessment."
    ),
}

# The reasons that should raise an incident. Deliberately not every reason:
# `restore_never_verified` on a policy nobody has drilled yet is a backlog
# item, not a page at 3am.
INCIDENT_REASONS: frozenset[str] = frozenset(
    {
        ReasonCode.BACKUP_OVERDUE,
        ReasonCode.LATEST_RUN_FAILED,
        ReasonCode.INTEGRITY_FAILED,
        ReasonCode.OFFSITE_MISSING,
        ReasonCode.RESTORE_FAILED,
        ReasonCode.RESTORE_VERIFICATION_EXPIRED,
    }
)

# How long a connector may go quiet before its evidence stops being
# trustworthy. Beyond this, "protected" would mean "protected as far as we
# knew last week".
DEFAULT_REPORTER_STALE_AFTER = timedelta(hours=36)


@dataclass(frozen=True)
class PolicyPromise:
    """What the policy promised, at the version being judged."""

    rpo_seconds: int
    rto_seconds: int | None = None
    requires_offsite: bool = False
    requires_integrity_check: bool = False
    restore_verification_ttl_seconds: int | None = None
    enabled: bool = True
    version: int = 1


@dataclass(frozen=True)
class RunEvidence:
    status: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ArtifactEvidence:
    """One observed artifact and everything hanging off it."""

    exists: bool = False
    presence: str = "present"
    size_bytes: int | None = None
    checksum: str | None = None
    created_at: datetime | None = None
    integrity_result: str | None = None
    integrity_checked_at: datetime | None = None
    offsite_present: bool = False
    offsite_site_key: str | None = None


@dataclass(frozen=True)
class DrillEvidence:
    result: str | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = None
    rto_met: bool | None = None


@dataclass(frozen=True)
class ProtectionEvidence:
    """Everything known about one policy, at one moment."""

    last_success_at: datetime | None = None
    last_attempt: RunEvidence = field(default_factory=RunEvidence)
    artifact: ArtifactEvidence = field(default_factory=ArtifactEvidence)
    drill: DrillEvidence = field(default_factory=DrillEvidence)
    reporter_seen_at: datetime | None = None
    consecutive_failures: int = 0


@dataclass
class ProtectionVerdict:
    backup_state: BackupState
    recoverability_state: RecoverabilityState
    overall_state: OverallState
    reasons: list[str]
    messages: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "backup_state": str(self.backup_state),
            "recoverability_state": str(self.recoverability_state),
            "overall_state": str(self.overall_state),
            "reasons": list(self.reasons),
            "messages": list(self.messages),
        }


def evaluate_protection(
    promise: PolicyPromise,
    evidence: ProtectionEvidence,
    *,
    now: datetime,
    reporter_stale_after: timedelta = DEFAULT_REPORTER_STALE_AFTER,
) -> ProtectionVerdict:
    """The single place a protection verdict is decided."""
    reasons: list[str] = []

    # --- can we trust anything at all? -----------------------------------
    if evidence.reporter_seen_at is None or (
        now - evidence.reporter_seen_at
    ) > reporter_stale_after:
        # Without a live reporter, every other signal is a memory. Saying
        # "protected" here would mean "protected as far as we knew then".
        reasons.append(str(ReasonCode.REPORTER_STALE))
        return _verdict(
            BackupState.UNKNOWN, RecoverabilityState.UNKNOWN, OverallState.UNKNOWN, reasons
        )

    # --- backup axis -------------------------------------------------------
    backup_state = _evaluate_backup(promise, evidence, now, reasons)

    # --- recoverability axis ------------------------------------------------
    recoverability_state = _evaluate_recoverability(promise, evidence, now, reasons)

    return _verdict(
        backup_state, recoverability_state, _overall(backup_state, recoverability_state), reasons
    )


def _evaluate_backup(
    promise: PolicyPromise,
    evidence: ProtectionEvidence,
    now: datetime,
    reasons: list[str],
) -> BackupState:
    # An explicit failure outranks age: a job that just failed is a
    # different problem from one that has not run in a while.
    if evidence.last_attempt.status == "failed":
        reasons.append(str(ReasonCode.LATEST_RUN_FAILED))
        return BackupState.FAILED

    if evidence.last_success_at is None:
        # Nothing has ever succeeded. Not "at risk" — there is no backup.
        reasons.append(str(ReasonCode.BACKUP_OVERDUE))
        return BackupState.OVERDUE

    age = (now - evidence.last_success_at).total_seconds()
    if age > promise.rpo_seconds:
        reasons.append(str(ReasonCode.BACKUP_OVERDUE))
        return BackupState.OVERDUE

    # The backup is fresh. Now: is there actually a copy, and is it usable?
    # This is where a green job stops being enough.
    artifact = evidence.artifact
    if not artifact.exists or artifact.presence == "missing":
        reasons.append(str(ReasonCode.ARTIFACT_MISSING))
        return BackupState.AT_RISK

    if promise.requires_integrity_check:
        if artifact.integrity_result is None:
            reasons.append(str(ReasonCode.INTEGRITY_MISSING))
            return BackupState.AT_RISK
        if artifact.integrity_result != "passed":
            reasons.append(str(ReasonCode.INTEGRITY_FAILED))
            return BackupState.AT_RISK
    elif artifact.integrity_result == "failed":
        # Not required, but it ran and it failed. Ignoring that because the
        # policy did not demand it would be perverse.
        reasons.append(str(ReasonCode.INTEGRITY_FAILED))
        return BackupState.AT_RISK

    if promise.requires_offsite and not artifact.offsite_present:
        reasons.append(str(ReasonCode.OFFSITE_MISSING))
        return BackupState.AT_RISK

    return BackupState.PROTECTED


def _evaluate_recoverability(
    promise: PolicyPromise,
    evidence: ProtectionEvidence,
    now: datetime,
    reasons: list[str],
) -> RecoverabilityState:
    drill = evidence.drill

    if drill.result == "failed":
        # A failed drill is evidence, and it says the opposite of what a
        # green backup job says. It wins.
        reasons.append(str(ReasonCode.RESTORE_FAILED))
        return RecoverabilityState.FAILED

    if drill.result != "passed" or drill.completed_at is None:
        reasons.append(str(ReasonCode.RESTORE_NEVER_VERIFIED))
        return RecoverabilityState.UNVERIFIED

    ttl = promise.restore_verification_ttl_seconds
    if ttl is not None and (now - drill.completed_at).total_seconds() > ttl:
        # A drill from a year ago proves something about a year ago.
        reasons.append(str(ReasonCode.RESTORE_VERIFICATION_EXPIRED))
        return RecoverabilityState.UNVERIFIED

    if promise.rto_seconds is not None:
        exceeded = drill.rto_met is False or (
            drill.duration_seconds is not None
            and drill.duration_seconds > promise.rto_seconds
        )
        if exceeded:
            # It restored, but not inside the time the business was
            # promised. Verified-but-too-slow is not verified.
            reasons.append(str(ReasonCode.RTO_EXCEEDED))
            return RecoverabilityState.UNVERIFIED

    return RecoverabilityState.VERIFIED


def _overall(backup: BackupState, recoverability: RecoverabilityState) -> OverallState:
    """Combine the two axes without letting either hide the other."""
    if backup is BackupState.UNKNOWN:
        return OverallState.UNKNOWN
    if backup is BackupState.FAILED or recoverability is RecoverabilityState.FAILED:
        return OverallState.FAILED
    if backup is BackupState.OVERDUE:
        return OverallState.OVERDUE
    if backup is BackupState.AT_RISK:
        return OverallState.AT_RISK
    # Backup is protected. The only question left is whether anyone has
    # proved it can be restored.
    if recoverability is RecoverabilityState.VERIFIED:
        return OverallState.RECOVERABLE_VERIFIED
    return OverallState.PROTECTED_UNVERIFIED


def _verdict(
    backup: BackupState,
    recoverability: RecoverabilityState,
    overall: OverallState,
    reasons: list[str],
) -> ProtectionVerdict:
    ordered: list[str] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    return ProtectionVerdict(
        backup_state=backup,
        recoverability_state=recoverability,
        overall_state=overall,
        reasons=ordered,
        messages=[REASON_TEXT.get(reason, reason) for reason in ordered],
    )


def incident_reasons(verdict: ProtectionVerdict) -> list[str]:
    """Which of a verdict's reasons deserve an incident."""
    return [reason for reason in verdict.reasons if reason in INCIDENT_REASONS]
