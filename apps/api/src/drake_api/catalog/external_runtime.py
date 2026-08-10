"""What Drake may say about a runtime it does not run.

The states here exist because five different absences were previously one
word. They are not synonyms and the whole point of this module is that they
cannot collapse into each other:

    not_applicable   the question does not apply to this runtime
    unknown          the question applies; nothing has answered it
    unavailable      an answer would exist, but none has been obtained yet
    stale            an answer was obtained and is now too old to trust
    unhealthy        an answer was obtained and it is bad

An external application has no cluster. That is `not_applicable` — not
`missing`, and emphatically not drift. A drift report that lists "no
cluster" against a Vercel-hosted application is describing Drake's schema,
not the application.

Verification is the second axis, and it is the one that gets lost. A
repository importing a provider SDK is `repository_intent`: it is evidence
about source code. It is not evidence that a production connection exists,
works, or is even pointed at that provider. Promoting intent to observation
is how a project comes to be reported healthy on the strength of a
`package.json`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class RuntimeKind(StrEnum):
    KUBERNETES = "kubernetes"
    EXTERNAL = "external"


class HostingProvider(StrEnum):
    """Closed vocabulary, mirrored by the manifest schema and a check
    constraint. Free text here would become an unbounded label, and
    unbounded labels carry both cardinality problems and whatever somebody
    happened to paste."""

    VERCEL = "vercel"
    NETLIFY = "netlify"
    CLOUDFLARE = "cloudflare"
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"
    FLY = "fly"
    RENDER = "render"
    HEROKU = "heroku"
    SUPABASE = "supabase"
    SELF_MANAGED = "self-managed"
    OTHER = "other"
    UNKNOWN = "unknown"


class DependencyClass(StrEnum):
    #: Drake runs it and can measure it. The historical meaning, and the
    #: default, so nothing already recorded changes meaning.
    IN_CLUSTER = "in_cluster"
    #: A provider operates it. Drake may know it exists; it does not run it,
    #: cannot restart it, and must not present it as a workload.
    MANAGED_DATA_PLATFORM = "managed_data_platform"
    EXTERNAL_SERVICE = "external_service"


class Verification(StrEnum):
    """How a claim is known — ordered weakest first."""

    REPOSITORY_INTENT = "repository_intent"
    OWNER_CONFIRMED = "owner_confirmed"
    PROVIDER_OBSERVED = "provider_observed"


class Availability(StrEnum):
    """Why a value is absent, when it is."""

    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


#: Fields that are meaningless for an external runtime. Reported as
#: `not_applicable`, never as missing, and never counted as drift.
EXTERNAL_NOT_APPLICABLE: frozenset[str] = frozenset(
    {"cluster", "namespace", "agent", "workload_binding", "inventory"}
)

REASON_TEXT: dict[Availability, str] = {
    Availability.NOT_APPLICABLE: (
        "This runtime has no such concept, so there is nothing to report."
    ),
    Availability.UNKNOWN: "Nothing has observed this yet.",
    Availability.UNAVAILABLE: "No successful observation has been recorded.",
}


def field_availability(runtime: str, field: str) -> Availability | None:
    """`not_applicable` for Kubernetes-only fields on an external runtime.

    Returns None when the field genuinely applies, so callers can tell
    "answer it" from "do not ask".
    """
    if runtime == RuntimeKind.EXTERNAL and field in EXTERNAL_NOT_APPLICABLE:
        return Availability.NOT_APPLICABLE
    return None


@dataclass(frozen=True, slots=True)
class HealthVerdict:
    """What Drake is prepared to say, and why.

    `status` reuses the service-health vocabulary rather than inventing a
    parallel one; `availability` explains an absence when there is one.
    """

    status: str
    availability: Availability | None = None
    verification: Verification | None = None
    last_observed_at: datetime | None = None

    @property
    def freshness(self) -> str:
        """Freshness only means something after an observation.

        Without one it is `unavailable` — which is not `stale`. Stale is a
        statement about an answer that has aged; unavailable is the absence
        of any answer at all, and treating them alike would let a project
        that has never been observed inherit the visual language of one
        whose data merely went old.
        """
        if self.last_observed_at is None:
            return str(Availability.UNAVAILABLE)
        return "fresh"

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status, "freshness": self.freshness}
        if self.availability is not None:
            payload["availability"] = str(self.availability)
            payload["reason"] = REASON_TEXT[self.availability]
        if self.verification is not None:
            payload["verification"] = str(self.verification)
        payload["last_observed_at"] = (
            self.last_observed_at.isoformat() if self.last_observed_at else None
        )
        return payload


def health_for_external(
    *,
    health_source_configured: bool,
    last_observed_at: datetime | None,
    verification: Verification = Verification.REPOSITORY_INTENT,
) -> HealthVerdict:
    """Health for a runtime with no agent.

    Three inputs, and none of them is a manifest import. A manifest being
    read tells you a file exists; `last_observed_at` may only be set by an
    actual observation, or the field means nothing.
    """
    if not health_source_configured:
        return HealthVerdict(
            status="not_configured",
            availability=Availability.UNKNOWN,
            verification=verification,
            last_observed_at=None,
        )
    if last_observed_at is None:
        return HealthVerdict(
            status="unknown",
            availability=Availability.UNAVAILABLE,
            verification=verification,
            last_observed_at=None,
        )
    return HealthVerdict(
        status="unknown",
        availability=None,
        verification=verification,
        last_observed_at=last_observed_at,
    )


def metrics_profile_state(metrics_profile: str | None) -> tuple[str, Availability | None]:
    """A service with no metrics profile reports `not_configured`.

    NULL is now a legal value for that column, and it means precisely "no
    metrics source". It must never be rendered as healthy, and it must never
    be filled in with a plausible-looking profile to make a form validate.
    """
    if metrics_profile:
        return metrics_profile, None
    return "not_configured", Availability.UNKNOWN


def dependency_is_workload(dependency_class: str | None) -> bool:
    """Only an in-cluster dependency is a workload.

    A managed data platform has no Deployment, no Pod and no replica count.
    Listing one among workloads invites somebody to ask why it will not
    restart.
    """
    return (dependency_class or DependencyClass.IN_CLUSTER) == DependencyClass.IN_CLUSTER
