"""What Drake can say about a rollout, and how sure it is.

Two judgements live here, both pure and both deliberately conservative.

**Rollout state** is read from the workload's own numbers — generation
against observed generation, updated/ready/available against desired. No
name matching, no inference from timing alone.

**Evidence state** grades the commit → workflow → digest → workload chain
by what was actually observed. It never fills a gap with a guess: a
mutable tag with nothing behind it is `unverified`, and evidence that
disagrees with itself is `conflict` rather than a decision about which
half to believe.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

WORKLOAD_KINDS: frozenset[str] = frozenset({"Deployment", "StatefulSet", "DaemonSet"})

# An image reference pinned to a digest, e.g. `ghcr.io/o/r@sha256:…`.
_DIGEST_REF = re.compile(r"@(sha256:[0-9a-f]{64})$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")

# Provenance carried by the workload itself. These are the reviewed keys
# the agent already allowlists (`drake.duosis.com/` and the standard
# `app.kubernetes.io/` set), so nothing new reaches Drake because of this.
COMMIT_KEYS: tuple[str, ...] = (
    "drake.duosis.com/commit-sha",
    "app.kubernetes.io/version",
)
REPOSITORY_KEYS: tuple[str, ...] = ("drake.duosis.com/repository",)
RUN_ID_KEYS: tuple[str, ...] = ("drake.duosis.com/workflow-run-id",)
PROVIDER_KEYS: tuple[str, ...] = ("drake.duosis.com/workflow-provider",)

# How long a workload may sit mid-rollout before Drake stops calling it
# "progressing". Long enough for a real rolling update on a slow image
# pull; short enough that a wedged rollout is named rather than excused.
STALL_AFTER = timedelta(minutes=15)


class RolloutState(StrEnum):
    PENDING = "pending"
    PROGRESSING = "progressing"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    STALLED = "stalled"
    UNKNOWN = "unknown"


class EvidenceState(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"
    CONFLICT = "conflict"


class ComparisonVerdict(StrEnum):
    IMPROVED = "improved"
    STABLE = "stable"
    REGRESSED = "regressed"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class WorkloadObservation:
    """One agent-reported workload, reduced to what a rollout needs."""

    kind: str
    generation: int | None
    observed_generation: int | None
    desired_replicas: int | None
    ready_replicas: int | None
    updated_replicas: int | None
    available_replicas: int | None
    conditions: tuple[dict[str, Any], ...] = ()
    observed_at: datetime | None = None
    first_seen_at: datetime | None = None

    @property
    def generation_observed(self) -> bool:
        if self.generation is None or self.observed_generation is None:
            return False
        return self.observed_generation >= self.generation


@dataclass(frozen=True)
class RolloutVerdict:
    state: RolloutState
    reason: str | None = None
    complete: bool = False


def _condition(observation: WorkloadObservation, name: str) -> dict[str, Any] | None:
    for condition in observation.conditions:
        if str(condition.get("type", "")).lower() == name.lower():
            return condition
    return None


def evaluate_rollout(
    observation: WorkloadObservation, *, now: datetime, stall_after: timedelta = STALL_AFTER
) -> RolloutVerdict:
    """Decide how a rollout is going, from the workload's own numbers.

    Ordered from most structural to least: a controller that has not seen
    its own spec is not "degraded", it simply has not started, and saying
    otherwise would page someone for a rollout that is one second old.
    """
    # --- nothing to judge -------------------------------------------------
    if observation.generation is None or observation.desired_replicas is None:
        return RolloutVerdict(RolloutState.UNKNOWN, "incomplete_observation")

    # --- a controller reporting failure outranks the counters -------------
    progressing = _condition(observation, "Progressing")
    if progressing is not None and str(progressing.get("status")) == "False":
        reason = str(progressing.get("reason") or "progress_deadline_exceeded")
        return RolloutVerdict(RolloutState.FAILED, reason[:64])
    replica_failure = _condition(observation, "ReplicaFailure")
    if replica_failure is not None and str(replica_failure.get("status")) == "True":
        return RolloutVerdict(RolloutState.FAILED, "replica_failure")

    # --- the controller has not caught up yet -----------------------------
    if not observation.generation_observed:
        return RolloutVerdict(RolloutState.PENDING, "generation_not_observed")

    desired = observation.desired_replicas
    updated = observation.updated_replicas
    ready = observation.ready_replicas
    available = observation.available_replicas

    if desired == 0:
        # Scaled to zero on purpose is not a failed rollout; there is
        # simply nothing to roll out.
        return RolloutVerdict(RolloutState.HEALTHY, "scaled_to_zero", complete=True)

    if updated is None or ready is None:
        return RolloutVerdict(RolloutState.UNKNOWN, "incomplete_observation")

    if updated >= desired and ready >= desired and (available is None or available >= desired):
        return RolloutVerdict(RolloutState.HEALTHY, None, complete=True)

    # --- still moving, or stuck? -------------------------------------------
    started = observation.first_seen_at
    stalled = started is not None and (now - started) > stall_after
    if stalled:
        # No progress for a bounded stretch. `stalled` rather than `failed`:
        # Kubernetes has not given up, and neither should the label.
        return RolloutVerdict(RolloutState.STALLED, "no_progress_within_window")

    if ready == 0 and desired > 0:
        return RolloutVerdict(RolloutState.PROGRESSING, "no_ready_replicas_yet")
    if ready < desired:
        # Some capacity is serving and some is not. During a rolling update
        # this is normal; past the stall window it becomes `stalled` above.
        return RolloutVerdict(RolloutState.DEGRADED, "partial_availability")
    return RolloutVerdict(RolloutState.PROGRESSING, "update_in_progress")


@dataclass(frozen=True)
class ImageRef:
    name: str
    image: str
    digest: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {"name": self.name, "image": self.image, "digest": self.digest}


def parse_digest(reference: str | None) -> str | None:
    """Pull a digest out of an image reference, if it is pinned to one."""
    if not reference:
        return None
    match = _DIGEST_REF.search(reference)
    if match:
        return match.group(1)
    return reference if _DIGEST.match(reference) else None


def short_digest(digest: str | None) -> str | None:
    """`sha256:abcdef…` → `abcdef12`, for display only."""
    if not digest:
        return None
    return digest.split(":", 1)[-1][:12]


@dataclass(frozen=True)
class Provenance:
    """What Drake observed about where an image came from."""

    commit_sha: str | None = None
    workflow_provider: str | None = None
    workflow_repository: str | None = None
    workflow_run_id: str | None = None
    #: Digest declared by the workload spec (a pinned image reference).
    declared_digest: str | None = None
    #: Digest the kubelet actually resolved and pulled.
    running_digest: str | None = None


@dataclass(frozen=True)
class EvidenceVerdict:
    state: EvidenceState
    detail: dict[str, Any] = field(default_factory=dict)


def normalize_commit(value: str | None) -> str | None:
    """Accept a commit SHA, and only a commit SHA.

    `app.kubernetes.io/version` often holds a version string rather than a
    SHA; anything that is not SHA-shaped is dropped rather than stored as
    provenance it is not.
    """
    if not value:
        return None
    candidate = value.strip().lower().removeprefix("sha-").removeprefix("g")
    return candidate if _COMMIT.match(candidate) else None


def evaluate_evidence(provenance: Provenance) -> EvidenceVerdict:
    """Grade the commit → workflow → digest → workload chain.

    The rule that matters: Drake never upgrades a verdict to cover a gap.
    A missing link keeps the answer below `verified`, and two links that
    disagree produce `conflict` — not a decision about which to believe.
    """
    declared = provenance.declared_digest
    running = provenance.running_digest
    has_commit = bool(provenance.commit_sha)
    has_workflow = bool(provenance.workflow_run_id and provenance.workflow_repository)

    detail: dict[str, Any] = {
        "commit": has_commit,
        "workflow": has_workflow,
        "declared_digest": bool(declared),
        "running_digest": bool(running),
        "digest_match": None,
    }

    # The workload says it runs one build and the node pulled another. That
    # is a real disagreement about what is executing, and the honest answer
    # is to say so rather than pick a side.
    if declared and running:
        detail["digest_match"] = declared == running
        if declared != running:
            return EvidenceVerdict(EvidenceState.CONFLICT, detail)

    digest = declared or running
    if digest and has_commit and has_workflow:
        # Every link observed: the running build is traceable to a workflow
        # run and a commit.
        return EvidenceVerdict(EvidenceState.VERIFIED, detail)
    if digest or has_commit or has_workflow:
        # Something is known, but the chain does not close. Saying
        # "verified" here is how a deploy history becomes fiction.
        return EvidenceVerdict(EvidenceState.PARTIAL, detail)
    # A mutable tag and nothing else. It may well be correct; Drake simply
    # has no evidence for it.
    return EvidenceVerdict(EvidenceState.UNVERIFIED, detail)


def workflow_run_url(
    base_url: str, provider: str | None, repository: str | None, run_id: str | None
) -> str | None:
    """Compose a run link from a CONFIGURED base URL and typed parts.

    No URL is ever stored or accepted; a link exists only if an operator
    configured a base for the provider, and the path is built from values
    that already passed their column checks.
    """
    if not (base_url and provider == "github" and repository and run_id):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return None
    if not re.fullmatch(r"[0-9]{1,32}", run_id):
        return None
    return f"{base_url.rstrip('/')}/{repository}/actions/runs/{run_id}"
