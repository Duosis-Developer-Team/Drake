"""What an onboarding plan is, decided in one place.

Pure and deterministic: manifest intent plus catalog state in, a proposal
out. No I/O, no clock beyond the one it is handed, no database.

The source-of-truth boundary this module enforces:

    Drake catalog       authoritative runtime projection
    .drake/project.yaml versioned repository INTENT
    GitHub discovery    evidence
    Kubernetes agent    observed runtime state

A manifest states what a repository WANTS. It does not get to be right. So
the planner's job is to say, item by item, what applying that intent would
actually do to the catalog — and to refuse rather than choose whenever the
answer is ambiguous.

Four rules do most of the work:

**Nothing is deleted.** There is no `delete` action. A service that
disappeared from a repository becomes an `unmapped` observation, because a
manifest edit is not evidence that a running service stopped existing, and
a catalog that deletes on a diff will one day delete on a mistake.

**Nothing is overwritten silently.** An existing catalog row that the
manifest describes differently is a `conflict`, not an update. `link` and
`update_metadata` are for cases where the existing row and the intent agree
on identity.

**Ambiguity blocks.** Two catalog rows that both match one manifest entry
produce a `conflict`. Picking whichever sorted first would file a service
under the wrong environment, and nobody would know.

**A manifest cannot choose infrastructure or authority.** Clusters, owner
teams, tenants and permissions are resolved against server-controlled
catalog rows. A manifest naming a cluster Drake does not have is an
`unmapped` item, never a reason to create one.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SessionState(StrEnum):
    DRAFT = "draft"
    DISCOVERY_PENDING = "discovery_pending"
    ANALYZING = "analyzing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    APPROVED = "approved"
    APPLYING = "applying"
    IMPORTED = "imported"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NOT_CONFIGURED = "not_configured"
    STALE = "stale"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class Action(StrEnum):
    CREATE = "create"
    LINK = "link"
    UPDATE_METADATA = "update_metadata"
    NO_CHANGE = "no_change"
    CONFLICT = "conflict"
    UNMAPPED = "unmapped"
    UNSUPPORTED = "unsupported"


class EntityKind(StrEnum):
    PROJECT = "project"
    ENVIRONMENT = "environment"
    SERVICE = "service"
    OWNER_TEAM = "owner_team"
    REPOSITORY = "repository"
    CLUSTER_BINDING = "cluster_binding"
    NAMESPACE_BINDING = "namespace_binding"
    METRIC_PROFILE = "metric_profile"
    SLO_PROFILE = "slo_profile"
    DEPLOYMENT_SOURCE = "deployment_source"


# Actions that stop an apply. `unmapped` is included on purpose: a manifest
# naming a cluster or a metric profile Drake does not have is a decision
# someone has to make, not a gap to paper over.
BLOCKING_ACTIONS: frozenset[str] = frozenset({Action.CONFLICT, Action.UNMAPPED, Action.UNSUPPORTED})

# Bumped whenever discovery or planning rules change. Part of an analysis's
# identity, so a smarter analyzer produces a NEW analysis of the same commit
# rather than colliding with the old one.
ANALYZER_VERSION = 1

# What a plan may propose at once. A repository that would produce more than
# this is not something to review in a browser.
MAX_PLAN_ITEMS = 200


# Human-readable reasons. Server-owned: there is no endpoint that accepts a
# reason string, so this dictionary is the whole vocabulary.
REASON_TEXT: dict[str, str] = {
    "project_key_taken": (
        "A different project already uses this key. Onboarding would have to "
        "take it over, which is not something Drake does silently."
    ),
    "project_already_linked": "This repository is already linked to this project.",
    "environment_ambiguous": ("More than one catalog environment matches this manifest entry."),
    "service_ambiguous": "More than one catalog service matches this manifest entry.",
    "cluster_unknown": (
        "The manifest references a cluster Drake does not have. Clusters are "
        "operator-registered infrastructure; a manifest cannot create one."
    ),
    "owner_team_unknown": "The manifest references an owning team Drake does not have.",
    "metric_profile_unknown": (
        "The manifest references a metric profile that is not in the curated registry."
    ),
    "slo_profile_unknown": "The manifest references an SLO profile Drake does not have.",
    "catalog_only": (
        "This exists in the catalog but not in the manifest. Drake reports it and "
        "does not remove it — a manifest edit is not evidence a service stopped running."
    ),
    "metadata_differs": "The catalog row exists; some of its metadata would change.",
    "identical": "The catalog already matches the manifest.",
    "namespace_conflict": (
        "The manifest binds this environment to a namespace already used by "
        "another environment on the same cluster."
    ),
    "manifest_absent": "The repository has no .drake/project.yaml at this commit.",
    "manifest_invalid": "The manifest did not pass schema or policy validation.",
    "analysis_truncated": (
        "The analysis stopped at a budget, so this plan describes part of the repository."
    ),
}


@dataclass(frozen=True)
class PlanItem:
    """One proposal. Every field is a key or a code — never repository text."""

    entity_kind: str
    action: str
    item_key: str
    proposed_name: str | None = None
    existing_entity_id: str | None = None
    existing_name: str | None = None
    reason_code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.action in BLOCKING_ACTIONS

    def canonical(self) -> dict[str, Any]:
        """The shape the plan digest is computed over.

        Deliberately excludes anything non-deterministic — no timestamps, no
        row ids Drake generated — so re-planning identical inputs yields an
        identical digest and an approval can be checked against it.
        """
        return {
            "entity_kind": self.entity_kind,
            "action": self.action,
            "item_key": self.item_key,
            "proposed_name": self.proposed_name,
            "existing_entity_id": self.existing_entity_id,
            "reason_code": self.reason_code,
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "entity_kind": self.entity_kind,
            "action": self.action,
            "item_key": self.item_key,
            "proposed_name": self.proposed_name,
            "existing_entity_id": self.existing_entity_id,
            "existing_name": self.existing_name,
            "reason_code": self.reason_code,
            "reason": REASON_TEXT.get(self.reason_code or "", ""),
            "detail": dict(self.detail),
            "blocking": self.blocking,
        }


@dataclass
class Plan:
    items: list[PlanItem] = field(default_factory=list)

    @property
    def blocking_items(self) -> int:
        return sum(1 for item in self.items if item.blocking)

    @property
    def state(self) -> str:
        return "needs_review" if self.blocking_items else "ready"

    def digest(self) -> str:
        """Stable across re-planning of identical inputs.

        Sorted, so item ORDER cannot change the digest — otherwise a plan
        that proposes the same things in a different order would read as a
        different plan and invalidate an approval for no reason.
        """
        material = json.dumps(
            sorted((item.canonical() for item in self.items), key=lambda entry: entry["item_key"]),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode()).hexdigest()[:64]

    def summary(self) -> dict[str, int]:
        counts = dict.fromkeys((str(action) for action in Action), 0)
        for item in self.items:
            counts[item.action] += 1
        return counts


@dataclass(frozen=True)
class CatalogSnapshot:
    """What the catalog already contains, as far as this plan needs to know.

    Passed in rather than queried, so the planner stays pure and every
    decision it makes is reproducible from its inputs alone.
    """

    #: project_key -> project id
    projects: dict[str, str] = field(default_factory=dict)
    #: project_key -> repository row id already linked to it
    project_repository: dict[str, str] = field(default_factory=dict)
    #: (project_key, environment_key) -> environment id
    environments: dict[tuple[str, str], str] = field(default_factory=dict)
    #: (project_key, service_key) -> service id
    services: dict[tuple[str, str], str] = field(default_factory=dict)
    #: cluster_ref -> cluster id
    clusters: dict[str, str] = field(default_factory=dict)
    #: owner team key -> team id
    owner_teams: dict[str, str] = field(default_factory=dict)
    #: curated metric profile keys
    metric_profiles: frozenset[str] = frozenset()
    #: curated SLO profile keys
    slo_profiles: frozenset[str] = frozenset()
    #: (cluster_ref, namespace) -> environment key already bound there
    namespace_bindings: dict[tuple[str, str], str] = field(default_factory=dict)
    #: service keys the catalog has for this project that the manifest omits
    catalog_only_services: tuple[str, ...] = ()


def build_plan(
    document: dict[str, Any],
    snapshot: CatalogSnapshot,
    *,
    repository_row_id: str,
    truncated: bool = False,
) -> Plan:
    """Reconcile one validated manifest against the catalog.

    The manifest has already passed schema and policy validation, so this
    function is about IDENTITY, not about safety: which catalog rows the
    intent refers to, which it would create, and which it cannot resolve.
    """
    spec = document.get("spec") or {}
    metadata = document.get("metadata") or {}
    project_key = str(metadata.get("name") or "")
    items: list[PlanItem] = []

    # --- project -----------------------------------------------------------
    existing_project = snapshot.projects.get(project_key)
    linked_repository = snapshot.project_repository.get(project_key)
    if existing_project is None:
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.PROJECT),
                action=str(Action.CREATE),
                item_key=f"project:{project_key}",
                proposed_name=project_key,
            )
        )
    elif linked_repository == repository_row_id:
        # Already ours. Re-onboarding is a no-op rather than a second import.
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.PROJECT),
                action=str(Action.NO_CHANGE),
                item_key=f"project:{project_key}",
                proposed_name=project_key,
                existing_entity_id=existing_project,
                existing_name=project_key,
                reason_code="project_already_linked",
            )
        )
    elif linked_repository is None:
        # The key exists but belongs to no repository. Linking is a proposal
        # a human can accept; taking it over silently is not.
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.PROJECT),
                action=str(Action.LINK),
                item_key=f"project:{project_key}",
                proposed_name=project_key,
                existing_entity_id=existing_project,
                existing_name=project_key,
            )
        )
    else:
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.PROJECT),
                action=str(Action.CONFLICT),
                item_key=f"project:{project_key}",
                proposed_name=project_key,
                existing_entity_id=existing_project,
                existing_name=project_key,
                reason_code="project_key_taken",
            )
        )

    items.append(
        PlanItem(
            entity_kind=str(EntityKind.REPOSITORY),
            action=str(Action.LINK) if existing_project is None else str(Action.NO_CHANGE),
            item_key=f"repository:{project_key}",
            proposed_name=str((spec.get("repository") or {}).get("name") or ""),
            detail={"provider": str((spec.get("repository") or {}).get("provider") or "")},
        )
    )

    # --- owner teams --------------------------------------------------------
    for owner in spec.get("owners") or []:
        team = str(owner.get("team") or "")
        known = snapshot.owner_teams.get(team)
        # A team key Drake has not seen before is INTRODUCED by this
        # manifest, not missing from it — the first project any team owns
        # would otherwise be permanently unonboardable. The key is a
        # bounded label on a project; it grants nothing. Authority still
        # comes from RBAC grants, which no manifest can touch.
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.OWNER_TEAM),
                action=str(Action.LINK) if known else str(Action.CREATE),
                item_key=f"owner_team:{team}",
                proposed_name=team,
                existing_entity_id=known,
                detail={"grants_no_permissions": True},
            )
        )

    # --- environments -------------------------------------------------------
    for environment in spec.get("environments") or []:
        environment_key = str(environment.get("name") or "")
        item_key = f"environment:{environment_key}"
        existing = snapshot.environments.get((project_key, environment_key))
        if existing is None:
            items.append(
                PlanItem(
                    entity_kind=str(EntityKind.ENVIRONMENT),
                    action=str(Action.CREATE),
                    item_key=item_key,
                    proposed_name=environment_key,
                    detail={"runtime": str(environment.get("runtime") or "")},
                )
            )
        else:
            items.append(
                PlanItem(
                    entity_kind=str(EntityKind.ENVIRONMENT),
                    action=str(Action.LINK),
                    item_key=item_key,
                    proposed_name=environment_key,
                    existing_entity_id=existing,
                    existing_name=environment_key,
                )
            )

        if str(environment.get("runtime") or "") != "kubernetes":
            continue

        cluster_ref = str(environment.get("clusterRef") or "")
        cluster_id = snapshot.clusters.get(cluster_ref)
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.CLUSTER_BINDING),
                action=str(Action.LINK) if cluster_id else str(Action.UNMAPPED),
                item_key=f"cluster_binding:{environment_key}",
                proposed_name=cluster_ref,
                existing_entity_id=cluster_id,
                reason_code=None if cluster_id else "cluster_unknown",
            )
        )

        namespace = str(environment.get("namespace") or "")
        occupant = snapshot.namespace_bindings.get((cluster_ref, namespace))
        if occupant is not None and occupant != environment_key:
            # Two environments in one namespace would make every workload
            # signal ambiguous between them.
            items.append(
                PlanItem(
                    entity_kind=str(EntityKind.NAMESPACE_BINDING),
                    action=str(Action.CONFLICT),
                    item_key=f"namespace_binding:{environment_key}",
                    proposed_name=namespace,
                    existing_name=occupant,
                    reason_code="namespace_conflict",
                )
            )
        else:
            items.append(
                PlanItem(
                    entity_kind=str(EntityKind.NAMESPACE_BINDING),
                    action=str(Action.LINK),
                    item_key=f"namespace_binding:{environment_key}",
                    proposed_name=namespace,
                    detail={"cluster_ref": cluster_ref},
                )
            )

    # --- services -----------------------------------------------------------
    for service in spec.get("services") or []:
        service_key = str(service.get("name") or "")
        existing = snapshot.services.get((project_key, service_key))
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.SERVICE),
                action=str(Action.LINK) if existing else str(Action.CREATE),
                item_key=f"service:{service_key}",
                proposed_name=service_key,
                existing_entity_id=existing,
                existing_name=service_key if existing else None,
                detail={
                    "component": str(service.get("component") or ""),
                    "runtime": str(service.get("runtime") or ""),
                },
            )
        )

        profile = str(service.get("metricsProfile") or "")
        known_profile = profile in snapshot.metric_profiles
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.METRIC_PROFILE),
                action=str(Action.LINK) if known_profile else str(Action.UNMAPPED),
                item_key=f"metric_profile:{service_key}",
                proposed_name=profile,
                reason_code=None if known_profile else "metric_profile_unknown",
            )
        )

    # --- services the catalog has and the manifest does not -----------------
    for service_key in snapshot.catalog_only_services:
        # Reported, never removed. This is the item that makes "Drake does
        # not delete on a diff" visible rather than merely true.
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.SERVICE),
                action=str(Action.UNMAPPED),
                item_key=f"service_catalog_only:{service_key}",
                existing_name=service_key,
                reason_code="catalog_only",
            )
        )

    # --- SLO profiles -------------------------------------------------------
    for slo in spec.get("slos") or []:
        name = str(slo.get("name") or "")
        indicator = str(slo.get("indicator") or "")
        known_indicator = indicator in snapshot.slo_profiles
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.SLO_PROFILE),
                action=str(Action.LINK) if known_indicator else str(Action.UNMAPPED),
                item_key=f"slo_profile:{name}",
                proposed_name=name,
                reason_code=None if known_indicator else "slo_profile_unknown",
                detail={"indicator": indicator, "service_ref": str(slo.get("serviceRef") or "")},
            )
        )

    if truncated:
        # A plan built on a partial analysis says so, in the plan, as a
        # blocking item. A truncated scan is never a green light.
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.PROJECT),
                action=str(Action.UNSUPPORTED),
                item_key="analysis:truncated",
                reason_code="analysis_truncated",
            )
        )

    return Plan(items=items[:MAX_PLAN_ITEMS])


def deployment_source_item(detections: list[dict[str, str]]) -> PlanItem | None:
    """A deployment-source proposal, from observed metadata only.

    `None` when discovery saw nothing that indicates how the project is
    deployed — an absent proposal is more useful than a guessed one, because
    a wrong deployment source silently correlates the wrong revisions.
    """
    for detection in detections:
        if str(detection.get("kind") or "") == "deployment":
            return PlanItem(
                entity_kind=str(EntityKind.DEPLOYMENT_SOURCE),
                action=str(Action.LINK),
                item_key="deployment_source:primary",
                proposed_name=str(detection.get("value") or ""),
                detail={"evidence": str(detection.get("evidence") or "")},
            )
    return None
