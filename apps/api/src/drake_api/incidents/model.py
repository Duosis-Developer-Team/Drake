"""What an incident is, and which evaluations are allowed to create one.

The rules live here rather than in the processor so they can be read — and
tested — without a database. Two of them carry most of the weight:

**A verdict Drake could not trust is not evidence.** `unknown`, `stale`,
`not_configured`, a partial result, or anything served from last-good
means Drake failed to look. Opening an incident on that would page someone
because a Prometheus was restarting.

**One bad reading is not an incident.** Two consecutive trustworthy
`critical` evaluations are required, and two consecutive trustworthy
`healthy` ones to resolve. A single sample is a spike; the second one is a
pattern.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from drake_api.service_health.policy import ReasonCode

# Statuses that are a real measurement of the workload. Everything else is
# a statement about Drake's ability to measure, and must never move a
# streak in either direction.
MEASURED_STATUSES: frozenset[str] = frozenset({"healthy", "degraded", "critical"})

# Reason codes that mean the reading itself is untrustworthy. Their
# presence disqualifies an evaluation even when a status slipped through.
UNRELIABLE_REASONS: frozenset[str] = frozenset(
    {
        str(ReasonCode.DATASOURCE_NOT_CONFIGURED),
        str(ReasonCode.DATASOURCE_UNAVAILABLE),
        str(ReasonCode.QUERY_FAILED),
        str(ReasonCode.TELEMETRY_STALE),
        str(ReasonCode.PARTIAL_RESULT),
        str(ReasonCode.BINDING_DISABLED),
        str(ReasonCode.BINDING_UNRESOLVED),
        str(ReasonCode.NO_BINDING),
    }
)

# Every reason code the server may persist. A code outside this set is
# dropped rather than stored: the history table must never become a place
# where an unreviewed string arrives.
KNOWN_REASONS: frozenset[str] = frozenset(str(code) for code in ReasonCode)

# How many consecutive trustworthy readings a decision needs.
OPEN_THRESHOLD = 2
RESOLVE_THRESHOLD = 2

# A critical verdict with no recognised cause. Real, and better named than
# left blank — it still tells a reader the workload failed its policy.
GENERIC_CRITICAL_REASON = "health_critical"

# Which cause leads the incident title, most structural first. A workload
# with no ready replicas is not "also throttled"; it is down.
_REASON_PRIORITY: tuple[str, ...] = (
    str(ReasonCode.NO_READY_REPLICAS),
    str(ReasonCode.CRASH_LOOP),
    str(ReasonCode.OOM_KILLED),
    str(ReasonCode.RESTART_SPIKE),
    str(ReasonCode.HIGH_ERROR_RATE),
    str(ReasonCode.HIGH_LATENCY),
    str(ReasonCode.MEMORY_PRESSURE),
    str(ReasonCode.CPU_PRESSURE),
    str(ReasonCode.CPU_THROTTLING),
    str(ReasonCode.PARTIAL_AVAILABILITY),
    str(ReasonCode.ROLLOUT_INCOMPLETE),
)

# Short titles, server-owned. There is no endpoint that accepts a title, so
# every incident name in Drake comes from exactly this dictionary.
INCIDENT_TITLES: dict[str, str] = {
    str(ReasonCode.NO_READY_REPLICAS): "No replicas ready",
    str(ReasonCode.CRASH_LOOP): "Container crash loop",
    str(ReasonCode.OOM_KILLED): "Container out of memory",
    str(ReasonCode.RESTART_SPIKE): "Repeated container restarts",
    str(ReasonCode.HIGH_ERROR_RATE): "High error rate",
    str(ReasonCode.HIGH_LATENCY): "High latency",
    str(ReasonCode.MEMORY_PRESSURE): "Memory limit reached",
    str(ReasonCode.CPU_PRESSURE): "CPU limit reached",
    str(ReasonCode.CPU_THROTTLING): "Severe CPU throttling",
    str(ReasonCode.PARTIAL_AVAILABILITY): "Replicas missing",
    str(ReasonCode.ROLLOUT_INCOMPLETE): "Rollout stuck",
    GENERIC_CRITICAL_REASON: "Service health critical",
}

TITLE_MAX_LENGTH = 200


@dataclass(frozen=True)
class Evaluation:
    """One health verdict, reduced to what an incident decision needs.

    Deliberately not the whole health payload: section details and metric
    values have no place in incident storage, and carrying them here would
    make it easy for one to end up there.
    """

    binding_id: uuid.UUID
    environment_service_id: uuid.UUID
    project_id: uuid.UUID
    environment_id: uuid.UUID
    service_id: uuid.UUID
    binding_revision: int
    lifecycle: str
    resolved: bool
    status: str
    reasons: tuple[str, ...]
    computed_at: datetime
    partial: bool
    served_from_last_good: bool
    project_key: str = ""
    environment_key: str = ""
    service_key: str = ""

    @property
    def evaluation_id(self) -> str:
        """Identity of the verdict, not of the request that fetched it.

        Derived from `computed_at`, so a cached or last-good response —
        which carries the timestamp it was ORIGINALLY computed at — hashes
        to the same value and is recognised as already processed. `served_at`
        is deliberately not an input: it changes on every read and would
        turn one observation into an endless stream of new ones.
        """
        material = f"{self.binding_id}:{self.binding_revision}:{self.computed_at.isoformat()}"
        return hashlib.sha256(material.encode()).hexdigest()[:64]

    @property
    def safe_reasons(self) -> tuple[str, ...]:
        """Reason codes from the server's own dictionary, order preserved."""
        return tuple(reason for reason in self.reasons if reason in KNOWN_REASONS)

    @property
    def binding_usable(self) -> bool:
        return self.lifecycle == "active" and self.resolved

    @property
    def reliable(self) -> bool:
        """Whether this verdict may move a streak.

        A `degraded` reading is reliable — it is a real measurement — even
        though it opens nothing. That is the distinction that keeps a
        flapping service from accumulating a critical streak across
        readings where it was merely degraded.
        """
        if not self.binding_usable:
            return False
        if self.partial or self.served_from_last_good:
            return False
        if self.status not in MEASURED_STATUSES:
            return False
        return not any(reason in UNRELIABLE_REASONS for reason in self.reasons)

    @property
    def opens_incidents(self) -> bool:
        return self.reliable and self.status == "critical"

    @property
    def proves_recovery(self) -> bool:
        return self.reliable and self.status == "healthy"

    def primary_reason(self) -> str:
        for candidate in _REASON_PRIORITY:
            if candidate in self.reasons:
                return candidate
        return GENERIC_CRITICAL_REASON

    def title(self) -> str:
        """A name composed from server-controlled parts only.

        The catalog keys are bounded identifiers, not free text — there is
        no field anywhere in this sprint through which a person can name an
        incident.
        """
        label = INCIDENT_TITLES.get(self.primary_reason(), INCIDENT_TITLES[GENERIC_CRITICAL_REASON])
        service = self.service_key or "service"
        where = f" ({self.environment_key})" if self.environment_key else ""
        return f"{service}{where}: {label}"[:TITLE_MAX_LENGTH]


def evaluation_from_health(
    payload: dict[str, Any], context: dict[str, Any], ids: dict[str, uuid.UUID]
) -> Evaluation:
    """Adapt a Sprint 5 health payload without reinterpreting it.

    Statuses and reason codes are carried across verbatim. This function
    decides nothing — if it did, there would be two places that know what
    `critical` means.
    """
    computed_at = payload["computed_at"]
    if isinstance(computed_at, str):
        computed_at = datetime.fromisoformat(computed_at)
    return Evaluation(
        binding_id=uuid.UUID(context["id"]),
        environment_service_id=ids["environment_service_id"],
        project_id=ids["project_id"],
        environment_id=ids["environment_id"],
        service_id=ids["service_id"],
        binding_revision=int(context["revision"]),
        lifecycle=str(context["lifecycle"]),
        resolved=bool(context["resolved"]),
        status=str(payload["status"]),
        reasons=tuple(str(reason) for reason in payload.get("reasons", [])),
        computed_at=computed_at,
        partial=bool(payload.get("partial", False)),
        served_from_last_good=bool(payload.get("served_from_last_good", False)),
        project_key=str(context.get("project_key", "")),
        environment_key=str(context.get("environment_key", "")),
        service_key=str(context.get("service_key", "")),
    )
