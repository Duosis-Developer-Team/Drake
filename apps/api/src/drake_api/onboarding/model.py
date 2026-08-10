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
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from drake_api.catalog.external_runtime import dependency_metadata as dependency_metadata_for


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
    # Its own kind since 0019. It used to borrow `service` with a flag in
    # `detail`, which made two different decisions read as one and put the
    # handler registry's key at odds with what it dispatched on.
    WORKLOAD_BINDING = "workload_binding"
    #: A dependency the project has and Drake does not run. Its own kind
    #: because it is neither a service (no workload) nor an in-cluster
    #: datastore Drake operates.
    DEPENDENCY = "dependency"


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

# Registered with a new project so each capability reports `not_configured`
# rather than nothing at all. Declared on the project plan item, so this is
# not a mutation the plan failed to mention.
PLACEHOLDER_INTEGRATIONS: tuple[str, ...] = (
    "prometheus",
    "github",
    "cluster-agent",
    "backup-reporter",
)

# Actions that make a persistent change and therefore need a handler.
ACTIONABLE_ACTIONS: frozenset[str] = frozenset({Action.CREATE, Action.LINK, Action.UPDATE_METADATA})


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
    "metric_profile_not_configured": (
        "This service declares no metrics profile, so Drake collects no application "
        "metrics for it. Health reports not_configured rather than a profile nothing "
        "would honour."
    ),
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
    "slo_objective_invalid": (
        "The SLO objective or window is outside what Drake can measure, so it is "
        "refused rather than stored as an unmeasurable promise."
    ),
    "slo_service_unknown": "The SLO references a service this manifest does not declare.",
    "binding_no_evidence": (
        "No observed workload matches this service yet, so Drake proposes no binding. "
        "A binding is only ever made from a workload the cluster agent has actually "
        "seen — a guessed one reports another workload's health as this service's. "
        "Bind it by hand, or analyse again once the agent has reported."
    ),
    "binding_ambiguous": (
        "More than one observed workload matches this service. Choosing one would "
        "attribute the wrong workload's health, so the choice is left to a human."
    ),
    "deployment_source_informational": (
        "Discovery observed how this project is deployed. Drake has no catalog field "
        "for it yet, so it is recorded as evidence and changes nothing."
    ),
    "applied_with_parent": (
        "Applied as part of the row it belongs to. Shown separately so the plan "
        "names every decision, and marked no-change so it does not claim an "
        "effect of its own."
    ),
    "immutable_field_change": (
        "The manifest would change a field that is part of this row's identity. "
        "Identity is the catalog's, not the manifest's."
    ),
}

# Metadata a manifest may change on an EXISTING catalog row. Everything not
# listed here is identity or authority: a project's key and scope, a
# repository's ownership, a tenant model, an RBAC relationship. A manifest
# states intent about a system; it does not get to re-parent it, re-scope
# it, or hand it to a different tenant.
MUTABLE_PROJECT_FIELDS: frozenset[str] = frozenset({"display_name", "criticality"})
MUTABLE_ENVIRONMENT_FIELDS: frozenset[str] = frozenset({"branch", "criticality"})
MUTABLE_SERVICE_FIELDS: frozenset[str] = frozenset(
    {"display_name", "component", "runtime", "metrics_profile", "workload_selector", "health"}
)
MUTABLE_SLO_FIELDS: frozenset[str] = frozenset(
    {"display_name", "indicator", "objective_ratio", "window_seconds"}
)

# Fields a manifest may never move on an existing row. Attempting one is a
# conflict, never a silent update.
IMMUTABLE_PROJECT_FIELDS: frozenset[str] = frozenset(
    {"project_key", "tenant_model", "repo_provider", "repo_owner", "repo_name"}
)
IMMUTABLE_ENVIRONMENT_FIELDS: frozenset[str] = frozenset(
    # `hosting_provider` joins the immutable set for the same reason as
    # `cluster_ref`: it is where the environment runs. A manifest changing it
    # is a relocation, which is a conflict for a human to settle rather than
    # a metadata update to apply quietly.
    {"environment_key", "runtime", "cluster_ref", "namespace", "hosting_provider"}
)
IMMUTABLE_SERVICE_FIELDS: frozenset[str] = frozenset({"service_key"})
# Class and engine are what a dependency IS; changing either is a different
# dependency wearing the same name, which is a conflict for a person.
# Provider and verification are what we currently believe about it, and both
# are auditable metadata updates.
MUTABLE_DEPENDENCY_FIELDS: frozenset[str] = frozenset(
    {"display_name", "provider", "verification", "store_scope"}
)
IMMUTABLE_DEPENDENCY_FIELDS: frozenset[str] = frozenset(
    {"dependency_key", "dependency_class", "engine"}
)

# Kubernetes kinds Drake can bind a service to. Mirrors the CHECK on
# `service_workload_bindings.workload_kind`.
BINDABLE_WORKLOAD_KINDS: frozenset[str] = frozenset({"Deployment", "StatefulSet", "DaemonSet"})

# What an SLO objective may be. A 0% or >100% objective is not a promise
# anyone can measure against.
MIN_SLO_OBJECTIVE = 0.0
MAX_SLO_OBJECTIVE = 100.0
MIN_SLO_WINDOW_DAYS = 1
MAX_SLO_WINDOW_DAYS = 90


# What a plan item's mutation payload may carry, per entity kind. A field
# outside its entity's list never reaches the payload, so a manifest cannot
# smuggle one through a plan and into apply.
PAYLOAD_ALLOWLIST: dict[str, frozenset[str]] = {
    str(EntityKind.PROJECT): frozenset(
        {
            "project_key",
            "display_name",
            "criticality",
            "tenant_model",
            "repo_provider",
            "repo_owner",
            "repo_name",
            "default_branch",
            "owners",
        }
    ),
    str(EntityKind.ENVIRONMENT): frozenset(
        {
            "environment_key",
            "runtime",
            "branch",
            "criticality",
            "cluster_ref",
            "namespace",
            "hosting_provider",
        }
    ),
    str(EntityKind.SERVICE): frozenset(
        {
            "service_key",
            "display_name",
            "component",
            "runtime",
            "metrics_profile",
            "workload_selector",
            "health",
        }
    ),
    str(EntityKind.DEPENDENCY): frozenset(
        {
            "dependency_key",
            "display_name",
            "dependency_class",
            "engine",
            "store_scope",
            "provider",
            "verification",
        }
    ),
    str(EntityKind.SLO_PROFILE): frozenset(
        {
            "slo_key",
            "display_name",
            "indicator",
            "objective_ratio",
            "window_seconds",
            "service_ref",
        }
    ),
    str(EntityKind.WORKLOAD_BINDING): frozenset(
        {
            "environment_key",
            "service_key",
            "workload_kind",
            "workload_name",
            "namespace",
            "cluster_ref",
        }
    ),
    str(EntityKind.REPOSITORY): frozenset({"provider", "owner", "name", "external_id"}),
}

# A plan item is a proposal, not a data file. The bound is small on purpose:
# anything that does not fit is not a mutation payload.
MAX_PAYLOAD_BYTES = 4096

# Shapes that must never appear in a payload, whatever field they arrive in.
# The manifest policy already refuses these, so this is the second wall
# rather than the first.
_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(
        r"\b(?:password|api[_-]?key|secret[_-]?key|client[_-]?secret|auth[_-]?token)"
        r"\b\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"://[^/\s@:]+:[^@\s]+@"),
)


class PayloadRejectedError(ValueError):
    """A mutation payload that must not be stored, named by rule."""

    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


def _assert_safe(value: Any) -> None:
    if isinstance(value, str):
        for pattern in _SECRET_SHAPES:
            if pattern.search(value):
                raise PayloadRejectedError("credential_shaped_value")
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_safe(str(key))
            _assert_safe(item)
    elif isinstance(value, list):
        for item in value:
            _assert_safe(item)


def build_payload(entity_kind: str, values: dict[str, Any]) -> dict[str, Any]:
    """The canonical mutation payload an apply handler will execute.

    Bound to the plan, and therefore to the plan digest and to the approval.
    Three properties make it safe to store and to show:

    - **Allowlisted.** Only fields this entity kind declares. An unknown one
      is refused rather than dropped, because dropping it silently would let
      a manifest carry a field nobody notices is being ignored.
    - **Canonical.** The same intent produces the same payload, so an
      unchanged manifest does not churn the digest.
    - **Credential-free.** Checked again here even though the manifest
      policy already refused these shapes — a payload is rendered in a
      browser and written to audit metadata.
    """
    allowed = PAYLOAD_ALLOWLIST.get(entity_kind)
    if allowed is None:
        raise PayloadRejectedError("unknown_entity_kind")
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise PayloadRejectedError("field_not_allowlisted")

    payload = {key: canonical(value) for key, value in sorted(values.items())}
    _assert_safe(payload)
    if len(json.dumps(payload, sort_keys=True, separators=(",", ":"))) > MAX_PAYLOAD_BYTES:
        raise PayloadRejectedError("payload_too_large")
    return payload


def build_changes(
    entity_kind: str,
    existing: dict[str, Any],
    proposed: dict[str, Any],
    fields: list[str],
) -> dict[str, dict[str, Any]]:
    """`{field: {"before": ..., "after": ...}}`, canonical on both sides.

    The reviewer sees the values, which is what makes an approval informed.
    They are still allowlisted and still credential-checked: a `before` is
    as much a stored value as an `after`.
    """
    allowed = PAYLOAD_ALLOWLIST.get(entity_kind)
    if allowed is None:
        raise PayloadRejectedError("unknown_entity_kind")
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise PayloadRejectedError("field_not_allowlisted")

    changes = {
        name: {"before": canonical(existing.get(name)), "after": canonical(proposed.get(name))}
        for name in sorted(fields)
    }
    _assert_safe(changes)
    if len(json.dumps(changes, sort_keys=True, separators=(",", ":"))) > MAX_PAYLOAD_BYTES:
        raise PayloadRejectedError("payload_too_large")
    return changes


def canonical(value: Any) -> Any:
    """One representation per value, so comparison cannot produce churn.

    Key order, `None` versus absent, and `""` versus `None` are all made
    equivalent — otherwise a manifest that changed nothing would re-plan as
    `update_metadata` on every analysis and every plan digest would differ.
    """
    if isinstance(value, dict):
        return {
            key: canonical(value[key])
            for key in sorted(value)
            if canonical(value[key]) not in (None, "", {}, [])
        }
    if isinstance(value, list):
        return [canonical(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def metadata_differences(
    existing: dict[str, Any], proposed: dict[str, Any], mutable: frozenset[str]
) -> list[str]:
    """Which MUTABLE fields actually differ, canonically.

    Only fields the manifest states are compared: a manifest that omits a
    field is not asking for it to be cleared.
    """
    changed: list[str] = []
    for name in sorted(mutable):
        if name not in proposed:
            continue
        if canonical(proposed[name]) != canonical(existing.get(name)):
            changed.append(name)
    return changed


def immutable_conflicts(
    existing: dict[str, Any], proposed: dict[str, Any], immutable: frozenset[str]
) -> list[str]:
    """Which IDENTITY fields the manifest would move. Any one is a conflict."""
    return [
        name
        for name in sorted(immutable)
        if name in proposed and canonical(proposed[name]) != canonical(existing.get(name))
    ]


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
    #: The canonical values apply will execute. Bound to the plan, and so to
    #: the digest and to the approval — apply reads these and never re-reads
    #: the manifest for a value.
    payload: dict[str, Any] = field(default_factory=dict)
    #: `{field: {"before", "after"}}` for an update, so an approval is
    #: informed rather than a list of field names.
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)

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
            # The VALUES are part of the identity of a plan. Without them an
            # approval would bind a shape and leave the content free to
            # change underneath it.
            "payload": self.payload,
            "changes": self.changes,
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
            "payload": dict(self.payload),
            "changes": dict(self.changes),
            "blocking": self.blocking,
            # An item that is not actionable states so, and why, rather than
            # leaving a reader to infer it from an absent handler.
            "materialized": self.action in ACTIONABLE_ACTIONS,
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
    #: (project_key, dependency_key) -> dependency id
    dependencies: dict[tuple[str, str], str] = field(default_factory=dict)
    #: (project_key, dependency_key) -> what the catalog currently records
    dependency_metadata: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
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

    # --- current metadata, so a difference can be SEEN rather than assumed --
    #: project_key -> the mutable metadata the catalog currently holds
    project_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: (project_key, environment_key) -> current metadata
    environment_metadata: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    #: (project_key, service_key) -> current metadata
    service_metadata: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    #: (project_key, slo_key) -> (id, current metadata)
    slo_definitions: dict[tuple[str, str], tuple[str, dict[str, Any]]] = field(default_factory=dict)
    #: (environment_key, service_key) -> workloads the cluster agent has
    #: OBSERVED for this service. Evidence, not intent: a binding is only
    #: ever proposed from something that was actually seen running.
    observed_workloads: dict[tuple[str, str], tuple[dict[str, str], ...]] = field(
        default_factory=dict
    )
    #: (environment_key, service_key) -> the binding the catalog already has
    existing_bindings: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)


def _row_item(
    *,
    entity_kind: str,
    item_key: str,
    name: str,
    existing_id: str | None,
    existing: dict[str, Any],
    proposed: dict[str, Any],
    mutable: frozenset[str],
    immutable: frozenset[str],
    detail: dict[str, Any] | None = None,
    already_linked: bool = False,
) -> PlanItem:
    """One existing catalog row, compared rather than assumed.

    The three outcomes are genuinely different and used to be collapsed into
    `link`, which is how a manifest edit could be approved and then quietly
    do nothing:

    - identity would move  → `conflict`. Identity is the catalog's.
    - mutable data differs → `update_metadata`, naming the fields.
    - nothing differs      → `no_change`.

    `link` survives for the one case it honestly describes: an unclaimed row
    this repository is about to take ownership of, with nothing to update.
    """
    if not existing:
        # Nothing to compare against. Absence of evidence is not evidence of
        # a difference: without the row's current metadata the planner
        # cannot claim a conflict OR an update, so it proposes the link it
        # can justify and leaves the rest alone.
        return PlanItem(
            entity_kind=entity_kind,
            action=str(Action.NO_CHANGE) if already_linked else str(Action.LINK),
            item_key=item_key,
            proposed_name=name,
            existing_entity_id=existing_id,
            existing_name=name,
            reason_code="project_already_linked" if already_linked else None,
            detail=dict(detail or {}),
        )

    moved = immutable_conflicts(existing, proposed, immutable)
    if moved:
        return PlanItem(
            entity_kind=entity_kind,
            action=str(Action.CONFLICT),
            item_key=item_key,
            proposed_name=name,
            existing_entity_id=existing_id,
            existing_name=name,
            reason_code="immutable_field_change",
            detail={"fields": moved},
        )

    changed = metadata_differences(existing, proposed, mutable)
    if changed:
        return PlanItem(
            entity_kind=entity_kind,
            action=str(Action.UPDATE_METADATA),
            item_key=item_key,
            proposed_name=name,
            existing_entity_id=existing_id,
            existing_name=name,
            reason_code="metadata_differs",
            detail={**(detail or {}), "fields": changed},
            # Exactly the fields that differ, and only those. Apply executes
            # this and re-reads nothing.
            payload=build_payload(entity_kind, {name_: proposed[name_] for name_ in changed}),
            changes=build_changes(entity_kind, existing, proposed, changed),
        )

    return PlanItem(
        entity_kind=entity_kind,
        action=str(Action.LINK) if not already_linked else str(Action.NO_CHANGE),
        item_key=item_key,
        proposed_name=name,
        existing_entity_id=existing_id,
        existing_name=name,
        reason_code="project_already_linked" if already_linked else "identical",
        detail=dict(detail or {}),
    )


def _project_metadata(document: dict[str, Any]) -> dict[str, Any]:
    spec = document.get("spec") or {}
    metadata = document.get("metadata") or {}
    repository = spec.get("repository") or {}
    environments = spec.get("environments") or []
    return {
        "project_key": str(metadata.get("name") or ""),
        "display_name": str(metadata.get("displayName") or metadata.get("name") or ""),
        # The project inherits the most severe environment it declares.
        "criticality": max(
            (str(item.get("criticality") or "low") for item in environments),
            key=_CRITICALITY.index,
            default="low",
        ),
        "tenant_model": str((spec.get("tenantModel") or {}).get("mode") or ""),
        "repo_provider": str(repository.get("provider") or ""),
        "repo_owner": str(repository.get("owner") or ""),
        "repo_name": str(repository.get("name") or ""),
    }


def _environment_metadata(environment: dict[str, Any]) -> dict[str, Any]:
    return {
        "environment_key": str(environment.get("name") or ""),
        "runtime": str(environment.get("runtime") or ""),
        "branch": str(environment.get("branch") or ""),
        "criticality": str(environment.get("criticality") or ""),
        "cluster_ref": str(environment.get("clusterRef") or ""),
        "namespace": str(environment.get("namespace") or ""),
        # Only meaningful for an external runtime; a Kubernetes environment
        # is run by whoever runs the cluster, and the database refuses a
        # provider on one.
        "hosting_provider": str(environment.get("hostingProvider") or ""),
    }


def _service_metadata(service: dict[str, Any]) -> dict[str, Any]:
    return {
        "service_key": str(service.get("name") or ""),
        "display_name": str(service.get("displayName") or service.get("name") or ""),
        "component": str(service.get("component") or ""),
        "runtime": str(service.get("runtime") or ""),
        # "" carries through to NULL: no metrics source, reported as
        # not_configured rather than as a profile that does not exist.
        "metrics_profile": str(service.get("metricsProfile") or ""),
        "workload_selector": service.get("workloadSelector") or {},
        "health": service.get("health") or {},
    }


def slo_metadata(slo: dict[str, Any]) -> dict[str, Any]:
    """Manifest SLO → the shape `slo_definitions` stores.

    `objective` is a PERCENTAGE in the manifest and a RATIO in the database.
    Converting here, once, is what keeps the two from drifting by a factor
    of a hundred.
    """
    return {
        "slo_key": str(slo.get("name") or ""),
        "display_name": str(slo.get("name") or ""),
        "indicator": str(slo.get("indicator") or ""),
        "objective_ratio": round(float(slo.get("objective") or 0) / 100.0, 7),
        "window_seconds": int(slo.get("windowDays") or 0) * 86_400,
        "service_ref": str(slo.get("serviceRef") or ""),
    }


_CRITICALITY = ["low", "medium", "high", "critical"]


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
                # Named in the plan because creating a project also registers
                # these placeholders. Every persistent mutation is represented.
                detail={"registers_integrations": list(PLACEHOLDER_INTEGRATIONS)},
                payload=build_payload(
                    str(EntityKind.PROJECT),
                    {
                        **_project_metadata(document),
                        "default_branch": str(
                            (spec.get("repository") or {}).get("defaultBranch") or ""
                        ),
                        "owners": [
                            {
                                "team": str(owner.get("team") or ""),
                                "role": str(owner.get("role") or "primary"),
                            }
                            for owner in spec.get("owners") or []
                        ],
                    },
                ),
            )
        )
    elif linked_repository in (repository_row_id, None):
        # Ours already, or unclaimed. Either way the row stays and the only
        # question is whether the manifest would CHANGE anything about it —
        # which `link` used to hide, so an edit silently did nothing.
        items.append(
            _row_item(
                entity_kind=str(EntityKind.PROJECT),
                item_key=f"project:{project_key}",
                name=project_key,
                existing_id=existing_project,
                existing=snapshot.project_metadata.get(project_key, {}),
                proposed=_project_metadata(document),
                mutable=MUTABLE_PROJECT_FIELDS,
                immutable=IMMUTABLE_PROJECT_FIELDS,
                already_linked=linked_repository == repository_row_id,
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

    # A real row of its own (`github_repository_projects`), so it stays
    # actionable and has its own handler.
    items.append(
        PlanItem(
            entity_kind=str(EntityKind.REPOSITORY),
            action=str(Action.LINK)
            if linked_repository != repository_row_id
            else str(Action.NO_CHANGE),
            item_key=f"repository:{project_key}",
            proposed_name=str((spec.get("repository") or {}).get("name") or ""),
            reason_code=None if linked_repository != repository_row_id else "identical",
            detail={"provider": str((spec.get("repository") or {}).get("provider") or "")},
            payload=build_payload(
                str(EntityKind.REPOSITORY),
                {
                    "provider": str((spec.get("repository") or {}).get("provider") or ""),
                    "owner": str((spec.get("repository") or {}).get("owner") or ""),
                    "name": str((spec.get("repository") or {}).get("name") or ""),
                },
            )
            if linked_repository != repository_row_id
            else {},
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
                action=str(Action.NO_CHANGE),
                item_key=f"owner_team:{team}",
                proposed_name=team,
                # A team KEY is not a row id. `existing_entity_id` is a uuid
                # column, and `project_owners` is keyed by the team string —
                # so the key stays where it belongs, in the name.
                existing_name=team if known else None,
                reason_code="applied_with_parent",
                detail={
                    "grants_no_permissions": True,
                    "parent": f"project:{project_key}",
                },
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
                    payload=build_payload(
                        str(EntityKind.ENVIRONMENT), _environment_metadata(environment)
                    ),
                )
            )
        else:
            items.append(
                _row_item(
                    entity_kind=str(EntityKind.ENVIRONMENT),
                    item_key=item_key,
                    name=environment_key,
                    existing_id=existing,
                    existing=snapshot.environment_metadata.get((project_key, environment_key), {}),
                    proposed=_environment_metadata(environment),
                    mutable=MUTABLE_ENVIRONMENT_FIELDS,
                    immutable=IMMUTABLE_ENVIRONMENT_FIELDS,
                )
            )

        if str(environment.get("runtime") or "") != "kubernetes":
            continue

        cluster_ref = str(environment.get("clusterRef") or "")
        cluster_id = snapshot.clusters.get(cluster_ref)
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.CLUSTER_BINDING),
                action=str(Action.NO_CHANGE) if cluster_id else str(Action.UNMAPPED),
                item_key=f"cluster_binding:{environment_key}",
                proposed_name=cluster_ref,
                existing_entity_id=cluster_id,
                reason_code="applied_with_parent" if cluster_id else "cluster_unknown",
                detail={"parent": f"environment:{environment_key}"} if cluster_id else {},
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
                    action=str(Action.NO_CHANGE),
                    item_key=f"namespace_binding:{environment_key}",
                    proposed_name=namespace,
                    reason_code="applied_with_parent",
                    detail={
                        "cluster_ref": cluster_ref,
                        "parent": f"environment:{environment_key}",
                    },
                )
            )

    # --- services -----------------------------------------------------------
    for service in spec.get("services") or []:
        service_key = str(service.get("name") or "")
        existing = snapshot.services.get((project_key, service_key))
        detail = {
            "component": str(service.get("component") or ""),
            "runtime": str(service.get("runtime") or ""),
        }
        if existing is None:
            items.append(
                PlanItem(
                    entity_kind=str(EntityKind.SERVICE),
                    action=str(Action.CREATE),
                    item_key=f"service:{service_key}",
                    proposed_name=service_key,
                    detail=detail,
                    payload=build_payload(str(EntityKind.SERVICE), _service_metadata(service)),
                )
            )
        else:
            items.append(
                _row_item(
                    entity_kind=str(EntityKind.SERVICE),
                    item_key=f"service:{service_key}",
                    name=service_key,
                    existing_id=existing,
                    existing=snapshot.service_metadata.get((project_key, service_key), {}),
                    proposed=_service_metadata(service),
                    mutable=MUTABLE_SERVICE_FIELDS,
                    immutable=IMMUTABLE_SERVICE_FIELDS,
                    detail=detail,
                )
            )

        profile = str(service.get("metricsProfile") or "")
        # Three states, not two. An ABSENT profile is now a legal, deliberate
        # answer — the schema only requires one where the project has a
        # Kubernetes environment — and it means `not_configured`. Treating it
        # as `unmapped` blocked the apply, so an external project could pass
        # validation and then never be importable at all.
        #
        # A profile that is DECLARED but unknown stays `unmapped`: that is
        # somebody naming a registry key Drake does not have, which is still
        # a decision for a person.
        if not profile:
            action = str(Action.NO_CHANGE)
            reason = "metric_profile_not_configured"
        elif profile in snapshot.metric_profiles:
            action = str(Action.NO_CHANGE)
            reason = "applied_with_parent"
        else:
            action = str(Action.UNMAPPED)
            reason = "metric_profile_unknown"
        items.append(
            PlanItem(
                entity_kind=str(EntityKind.METRIC_PROFILE),
                action=action,
                item_key=f"metric_profile:{service_key}",
                proposed_name=profile,
                reason_code=reason,
                detail=(
                    {"parent": f"service:{service_key}"} if action == str(Action.NO_CHANGE) else {}
                ),
            )
        )

    # --- dependencies -------------------------------------------------------
    #
    # No workload binding, no expected workload, no namespace. A dependency
    # Drake does not run has nothing to bind to, and inventing a binding was
    # exactly what made a managed database report as a missing Deployment.
    for store in spec.get("dataStores") or []:
        dependency_key = str(store.get("name") or "")
        if not dependency_key:
            continue
        proposed = dependency_metadata_for(store)
        existing_id = snapshot.dependencies.get((project_key, dependency_key))
        detail = {
            "dependency_class": proposed["dependency_class"],
            "provider": proposed["provider"] or "unknown",
            "verification": proposed["verification"],
            "workload_applicability": "not_applicable",
        }
        if existing_id is None:
            items.append(
                PlanItem(
                    entity_kind=str(EntityKind.DEPENDENCY),
                    action=str(Action.CREATE),
                    item_key=f"dependency:{dependency_key}",
                    proposed_name=dependency_key,
                    detail=detail,
                    payload=build_payload(str(EntityKind.DEPENDENCY), proposed),
                )
            )
        else:
            items.append(
                _row_item(
                    entity_kind=str(EntityKind.DEPENDENCY),
                    item_key=f"dependency:{dependency_key}",
                    name=dependency_key,
                    existing_id=existing_id,
                    existing=snapshot.dependency_metadata.get((project_key, dependency_key), {}),
                    proposed=proposed,
                    mutable=MUTABLE_DEPENDENCY_FIELDS,
                    immutable=IMMUTABLE_DEPENDENCY_FIELDS,
                    detail=detail,
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

    # --- SLOs ---------------------------------------------------------------
    # These used to be a profile-name check that apply ignored entirely, so a
    # manifest could declare an objective and Drake would store nothing.
    declared_services = {str(item.get("name") or "") for item in spec.get("services") or []}
    for slo in spec.get("slos") or []:
        items.append(_slo_item(slo, snapshot, project_key=project_key, declared=declared_services))

    # --- service → workload bindings ---------------------------------------
    # Only ever from OBSERVED workloads. Without a binding nothing downstream
    # has anything to attach to; with a guessed one, another workload's
    # health is reported as this service's.
    for environment in spec.get("environments") or []:
        environment_key = str(environment.get("name") or "")
        if str(environment.get("runtime") or "") != "kubernetes":
            continue
        for service in spec.get("services") or []:
            items.append(
                _binding_item(
                    snapshot,
                    environment_key=environment_key,
                    service_key=str(service.get("name") or ""),
                    namespace=str(environment.get("namespace") or ""),
                    cluster_ref=str(environment.get("clusterRef") or ""),
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


def _slo_item(
    slo: dict[str, Any],
    snapshot: CatalogSnapshot,
    *,
    project_key: str,
    declared: set[str],
) -> PlanItem:
    """One manifest SLO, reconciled against `slo_definitions`.

    Refused before anything is stored when the promise is unmeasurable: an
    objective outside (0, 100], a window Drake's evaluator does not support,
    an indicator it cannot compute, or a service the manifest never declared.
    Storing one of those would be recording a target nobody can ever meet or
    miss.
    """
    proposed = slo_metadata(slo)
    key = str(proposed["slo_key"])
    item_key = f"slo_profile:{key}"

    objective = float(slo.get("objective") or 0)
    window_days = int(slo.get("windowDays") or 0)
    if not (MIN_SLO_OBJECTIVE < objective <= MAX_SLO_OBJECTIVE) or not (
        MIN_SLO_WINDOW_DAYS <= window_days <= MAX_SLO_WINDOW_DAYS
    ):
        return PlanItem(
            entity_kind=str(EntityKind.SLO_PROFILE),
            action=str(Action.CONFLICT),
            item_key=item_key,
            proposed_name=key,
            reason_code="slo_objective_invalid",
        )
    if str(proposed["indicator"]) not in snapshot.slo_profiles:
        return PlanItem(
            entity_kind=str(EntityKind.SLO_PROFILE),
            action=str(Action.UNMAPPED),
            item_key=item_key,
            proposed_name=key,
            reason_code="slo_profile_unknown",
        )
    service_ref = str(proposed["service_ref"])
    if service_ref and service_ref not in declared:
        return PlanItem(
            entity_kind=str(EntityKind.SLO_PROFILE),
            action=str(Action.UNMAPPED),
            item_key=item_key,
            proposed_name=key,
            reason_code="slo_service_unknown",
        )

    existing = snapshot.slo_definitions.get((project_key, key))
    if existing is None:
        return PlanItem(
            entity_kind=str(EntityKind.SLO_PROFILE),
            action=str(Action.CREATE),
            item_key=item_key,
            proposed_name=key,
            detail={"indicator": proposed["indicator"], "service_ref": service_ref},
            payload=build_payload(str(EntityKind.SLO_PROFILE), proposed),
        )
    existing_id, current = existing
    changed = metadata_differences(current, proposed, MUTABLE_SLO_FIELDS)
    if not changed:
        return PlanItem(
            entity_kind=str(EntityKind.SLO_PROFILE),
            action=str(Action.NO_CHANGE),
            item_key=item_key,
            proposed_name=key,
            existing_entity_id=existing_id,
            existing_name=key,
            reason_code="identical",
            detail={"indicator": proposed["indicator"], "fields": []},
        )
    return PlanItem(
        entity_kind=str(EntityKind.SLO_PROFILE),
        action=str(Action.UPDATE_METADATA),
        item_key=item_key,
        proposed_name=key,
        existing_entity_id=existing_id,
        existing_name=key,
        reason_code="metadata_differs",
        detail={"indicator": proposed["indicator"], "fields": changed},
        # The whole definition: an SLO update rewrites the row, so the
        # payload is the row, not the delta.
        payload=build_payload(str(EntityKind.SLO_PROFILE), proposed),
        changes=build_changes(str(EntityKind.SLO_PROFILE), current, proposed, changed),
    )


def _binding_item(
    snapshot: CatalogSnapshot,
    *,
    environment_key: str,
    service_key: str,
    namespace: str,
    cluster_ref: str,
) -> PlanItem:
    """A service → workload binding, proposed only from observed evidence.

    The manifest says a service exists; it does not say which workload runs
    it. Only the cluster agent knows that, and only after it has actually
    seen one. So:

    - nothing observed → `unmapped`. Not an error and not a guess: an
      operator binds it by hand, or the agent reports and the next analysis
      proposes it.
    - several observed → `unmapped`. Picking one would attribute another
      workload's health, restarts and deployments to this service.
    - exactly one       → `create`, or `no_change` if the catalog already
      has it.

    Recorded under the `service` entity kind with a distinguishing item key,
    because the plan-item vocabulary is a database CHECK and this slice adds
    no migration. The `detail` carries the discriminator.
    """
    item_key = f"workload_binding:{environment_key}:{service_key}"
    observed = snapshot.observed_workloads.get((environment_key, service_key), ())
    existing = snapshot.existing_bindings.get((environment_key, service_key))

    if existing is not None:
        return PlanItem(
            entity_kind=str(EntityKind.WORKLOAD_BINDING),
            action=str(Action.NO_CHANGE),
            item_key=item_key,
            proposed_name=str(existing.get("workload_name") or ""),
            existing_entity_id=str(existing.get("id") or ""),
            existing_name=str(existing.get("workload_name") or ""),
            reason_code="identical",
            detail={"workload_kind": str(existing.get("workload_kind") or "")},
        )
    if not observed:
        # `no_change`, not `unmapped`. Nothing is wrong and nothing is
        # ambiguous — the agent simply has not reported a matching workload
        # yet, which is the normal state of a project being onboarded for
        # the first time. Blocking the import on it would mean no project
        # could ever be onboarded before its agent had run.
        return PlanItem(
            entity_kind=str(EntityKind.WORKLOAD_BINDING),
            action=str(Action.NO_CHANGE),
            item_key=item_key,
            proposed_name=service_key,
            reason_code="binding_no_evidence",
        )
    if len(observed) > 1:
        return PlanItem(
            entity_kind=str(EntityKind.WORKLOAD_BINDING),
            action=str(Action.UNMAPPED),
            item_key=item_key,
            proposed_name=service_key,
            reason_code="binding_ambiguous",
            detail={"candidates": len(observed)},
        )

    workload = observed[0]
    return PlanItem(
        entity_kind=str(EntityKind.WORKLOAD_BINDING),
        action=str(Action.CREATE),
        item_key=item_key,
        proposed_name=str(workload.get("name") or ""),
        payload=build_payload(
            str(EntityKind.WORKLOAD_BINDING),
            {
                "environment_key": environment_key,
                "service_key": service_key,
                "workload_kind": str(workload.get("kind") or ""),
                "workload_name": str(workload.get("name") or ""),
                "namespace": namespace,
                "cluster_ref": cluster_ref,
            },
        ),
    )


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
                # `no_change`, not `link`. The catalog has no column for a
                # deployment source yet, so apply cannot honour a `link` —
                # and a plan item apply silently skips is exactly what the
                # parity invariant forbids. The evidence is still kept as a
                # discovery finding; only the CLAIM that apply will act on it
                # is withdrawn.
                action=str(Action.NO_CHANGE),
                item_key="deployment_source:primary",
                proposed_name=str(detection.get("value") or ""),
                reason_code="deployment_source_informational",
                detail={
                    "evidence": str(detection.get("evidence") or ""),
                    # A bounded code, not prose: a client decides on this,
                    # never on the sentence beside it.
                    "not_materialized_reason": "catalog_projection_not_supported",
                },
            )
    return None
