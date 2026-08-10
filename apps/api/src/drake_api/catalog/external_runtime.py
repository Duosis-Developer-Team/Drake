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

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
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


#: Weakest to strongest. Used to answer "is this a promotion?" without
#: string comparisons that would silently reorder if a level is added.
_VERIFICATION_RANK: dict[str, int] = {
    Verification.REPOSITORY_INTENT: 0,
    Verification.OWNER_CONFIRMED: 1,
    Verification.PROVIDER_OBSERVED: 2,
}


def verification_rank(value: str | None) -> int:
    return _VERIFICATION_RANK.get(str(value or Verification.REPOSITORY_INTENT), 0)


def is_above_repository_intent(value: str | None) -> bool:
    return verification_rank(value) > 0


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


class HealthSourceStatus(StrEnum):
    """Whether anything is configured to observe this at all.

    Separate from the verdict on purpose. "Nobody is watching" and "we
    looked and it is fine" are different facts, and the first version of
    this collapsed them: no source produced `not_configured` as the HEALTH
    status, which reads as a property of the system rather than of Drake's
    configuration.
    """

    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    #: No observation has ever been recorded. Not the same as `stale`, which
    #: is a statement about an answer that aged.
    UNAVAILABLE = "unavailable"


#: How old an observation may be before it stops being trusted. Explicit,
#: because "fresh" without a threshold silently meant "any observation ever
#: recorded", so nothing could ever go stale.
DEFAULT_STALE_AFTER = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class HealthSource:
    """What is configured to observe an external runtime, if anything."""

    status: HealthSourceStatus = HealthSourceStatus.NOT_CONFIGURED

    def as_dict(self) -> dict[str, Any]:
        return {"status": str(self.status)}


@dataclass(frozen=True, slots=True)
class HealthVerdict:
    """Health and freshness as INDEPENDENT axes.

    A record can be unhealthy and fresh (we just looked, it is broken) or
    healthy and stale (it was fine, but that was hours ago). Folding them
    into one field loses whichever of the two the reader needed.
    """

    status: str
    freshness: Freshness
    source: HealthSource
    availability: Availability | None = None
    verification: Verification | None = None
    last_observed_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "freshness": str(self.freshness),
            "source": self.source.as_dict(),
        }
        if self.availability is not None:
            payload["availability"] = str(self.availability)
            payload["reason"] = REASON_TEXT[self.availability]
        if self.verification is not None:
            payload["verification"] = str(self.verification)
        payload["last_observed_at"] = (
            self.last_observed_at.isoformat() if self.last_observed_at else None
        )
        return payload


def evaluate_external_health(
    *,
    source: HealthSourceStatus = HealthSourceStatus.NOT_CONFIGURED,
    observed_health: str | None = None,
    last_observed_at: datetime | None = None,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    verification: Verification = Verification.REPOSITORY_INTENT,
) -> HealthVerdict:
    """The whole external health state machine, in one pure function.

    `now` is passed in rather than read from the clock, so the same inputs
    always give the same verdict and a staleness boundary can actually be
    tested. `last_observed_at` may only be set by a real observation — there
    is deliberately no parameter here that a manifest import could fill.
    """
    if last_observed_at is None:
        # No observation: health is unknown regardless of whether something
        # is configured to look. The SOURCE carries that distinction.
        return HealthVerdict(
            status="unknown",
            freshness=Freshness.UNAVAILABLE,
            source=HealthSource(source),
            availability=(
                Availability.UNKNOWN
                if source is HealthSourceStatus.NOT_CONFIGURED
                else Availability.UNAVAILABLE
            ),
            verification=verification,
            last_observed_at=None,
        )

    moment = now or last_observed_at
    aged = (moment - last_observed_at) > stale_after
    # The observed verdict SURVIVES ageing. A stale unhealthy record is
    # still unhealthy; discarding the result on age would hide the one
    # thing worth acting on.
    return HealthVerdict(
        status=observed_health or "unknown",
        freshness=Freshness.STALE if aged else Freshness.FRESH,
        source=HealthSource(source),
        availability=None if observed_health else Availability.UNKNOWN,
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


class WorkloadApplicability(StrEnum):
    """Whether workload semantics mean anything for a dependency.

    Its own vocabulary rather than borrowing `Availability`: that enum says
    why a VALUE is absent, and this says whether a QUESTION applies. An
    in-cluster datastore is a workload with replicas and a rollout, so
    labelling it `not_applicable` — which the first version did for every
    dependency — was wrong about the domain, not merely about wording.
    """

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"


def workload_applicability(dependency_class: str | None) -> WorkloadApplicability:
    """Drake runs an in-cluster dependency; it does not run the others."""
    if dependency_is_workload(dependency_class):
        return WorkloadApplicability.APPLICABLE
    return WorkloadApplicability.NOT_APPLICABLE


def dependency_is_workload(dependency_class: str | None) -> bool:
    """Only an in-cluster dependency is a workload.

    A managed data platform has no Deployment, no Pod and no replica count.
    Listing one among workloads invites somebody to ask why it will not
    restart.
    """
    return (dependency_class or DependencyClass.IN_CLUSTER) == DependencyClass.IN_CLUSTER


def resolve_verification_for_import(
    declared: str | None,
    existing: str | None = None,
) -> Verification:
    """What an import may record, given what is already known.

    Two failure modes, in opposite directions, and this refuses both:

    **Promotion.** A manifest asserting `provider_observed` is a repository
    claiming Drake observed something, which is not evidence that Drake
    observed anything. What the manifest declares is therefore ignored
    entirely — it is not an input to the answer.

    **Erasure.** The first version returned `repository_intent`
    unconditionally, and `verification` is a mutable field, so a re-import
    overwrote an `owner_confirmed` or `provider_observed` that somebody had
    established out of band. Refusing to raise evidence is correct; deleting
    it is not, and it is worse because the destroyed value came from the one
    process that could actually establish it.

    So: preserve whatever exists, and default to `repository_intent` when
    nothing does. Raising a level stays an out-of-band action.
    """
    if existing and existing in _VERIFICATION_RANK:
        return Verification(existing)
    return Verification.REPOSITORY_INTENT


def dependency_metadata(
    store: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Manifest dataStore -> the shape `project_dependencies` stores.

    Deliberately drops `connectionSecretRef`. It is only a reference name and
    the schema forbids a value there, but nothing downstream needs it, and a
    field nobody reads is a field that can only ever leak.
    """
    dependency_class = str(store.get("dependencyClass") or DependencyClass.IN_CLUSTER)
    provider = str(store.get("provider") or "")
    return {
        "dependency_key": str(store.get("name") or ""),
        "display_name": str(store.get("name") or ""),
        "dependency_class": dependency_class,
        "engine": str(store.get("engine") or ""),
        "store_scope": str(store.get("scope") or ""),
        # A provider on an in-cluster store would claim something about
        # infrastructure Drake runs itself; the database refuses it too.
        "provider": provider if provider and dependency_class != DependencyClass.IN_CLUSTER else "",
        # Preserved from the catalog when it exists; never raised by a
        # manifest, never lowered by a re-import.
        "verification": str(
            resolve_verification_for_import(
                store.get("verification"),
                (existing or {}).get("verification"),
            )
        ),
    }
