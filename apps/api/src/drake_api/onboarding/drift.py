"""Manifest intent versus observed cluster inventory.

Pure and deterministic: expected workloads plus observed workloads in, an
explained comparison out. No I/O, no clock, no database — the same inputs
always produce the same report, which is what makes a drift result
something you can put in front of a person.

The distinction this module exists to keep:

    expected    the repository's `.drake/project.yaml` says this should run
    observed    the cluster agent has actually seen this running

Those are different claims, and collapsing them is how a project that was
never deployed shows up green. So an environment whose namespace has never
been observed does not report six missing workloads — it reports one
`namespace_not_observed`, because "we have no inventory for this namespace"
and "these six workloads are gone" are different problems with different
fixes.

**What is deliberately NOT compared.** Replica counts, rollout progress,
image tags, and anything else that changes on its own schedule. A drift
report that flips because a pod restarted trains people to ignore it. Drift
here means the SHAPE disagrees — something expected is absent, something
present was never declared — and health, which does move, is the service
health engine's job.

Label matching follows Kubernetes selector semantics: a workload matches
when its labels are a superset of the selector. Extra labels on the
workload are normal (Kustomize adds `app.kubernetes.io/part-of`, agents add
their own) and are not drift. Dict comparison also means label ORDER never
matters, which a serialized comparison would have got wrong.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DriftKind(StrEnum):
    """What the comparison concluded about one expectation or observation."""

    MATCHED = "matched"
    #: Declared by the manifest, absent from inventory for that namespace.
    EXPECTED_NOT_OBSERVED = "expected_not_observed"
    #: Running in a declared namespace, described by no manifest service.
    OBSERVED_NOT_EXPECTED = "observed_not_expected"
    #: The namespace itself has never been observed, so nothing inside it
    #: can be said to be missing — only unknown.
    NAMESPACE_NOT_OBSERVED = "namespace_not_observed"
    #: More than one observed workload matches one service's selector.
    #: Choosing would attribute the wrong workload's identity to a service.
    AMBIGUOUS = "ambiguous"
    #: A declared dataStore accounts for this workload. Separate from
    #: MATCHED because a datastore is not a service: it gets no
    #: service→workload binding, and conflating the two would inflate the
    #: binding count with things that were never bindings.
    DATASTORE_MATCHED = "datastore_matched"


#: Stable, human-readable causes. Server-owned, like ReasonCode in the
#: service-health policy: the UI maps these, it never parses prose.
REASON_TEXT: dict[DriftKind, str] = {
    DriftKind.MATCHED: "The manifest declares this service and inventory has seen it.",
    DriftKind.EXPECTED_NOT_OBSERVED: (
        "The manifest declares this service, and no workload in the namespace matches it."
    ),
    DriftKind.OBSERVED_NOT_EXPECTED: (
        "This workload is running in a declared namespace but no manifest service describes it."
    ),
    DriftKind.NAMESPACE_NOT_OBSERVED: (
        "The namespace has never appeared in cluster inventory, so nothing in it can be "
        "reported as present or missing."
    ),
    DriftKind.AMBIGUOUS: (
        "More than one observed workload matches this service, so Drake will not choose one."
    ),
    DriftKind.DATASTORE_MATCHED: (
        "The manifest declares this as a dataStore and inventory has seen a workload for it."
    ),
}


@dataclass(frozen=True, slots=True)
class ExpectedWorkload:
    """One service, in one environment, as the manifest declares it."""

    environment: str
    namespace: str
    service: str
    selector: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ObservedWorkload:
    """One workload the cluster agent has actually seen."""

    namespace: str
    kind: str
    name: str
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DriftItem:
    kind: DriftKind
    environment: str
    namespace: str
    #: Set for expectations; empty for a purely observed workload.
    service: str = ""
    #: Set when a workload is involved; empty when nothing was observed.
    workload_kind: str = ""
    workload_name: str = ""
    #: Populated for AMBIGUOUS, so the reader sees what the candidates were.
    candidates: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return REASON_TEXT[self.kind]

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": str(self.kind),
            "environment": self.environment,
            "namespace": self.namespace,
            "reason": self.reason,
        }
        if self.service:
            payload["service"] = self.service
        if self.workload_name:
            payload["workload"] = {"kind": self.workload_kind, "name": self.workload_name}
        if self.candidates:
            payload["candidates"] = list(self.candidates)
        return payload


@dataclass(frozen=True, slots=True)
class DriftReport:
    items: tuple[DriftItem, ...]

    def of_kind(self, kind: DriftKind) -> tuple[DriftItem, ...]:
        return tuple(item for item in self.items if item.kind is kind)

    @property
    def in_sync(self) -> bool:
        """True only when every item matched.

        An unobserved namespace is NOT in sync. It is the absence of
        evidence, and reporting it as agreement is the failure this module
        exists to prevent.
        """
        return all(
            item.kind in (DriftKind.MATCHED, DriftKind.DATASTORE_MATCHED) for item in self.items
        )

    def as_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[str(item.kind)] = counts.get(str(item.kind), 0) + 1
        return {
            "in_sync": self.in_sync,
            "counts": counts,
            "items": [item.as_dict() for item in self.items],
        }


def _matches(selector: Mapping[str, str], labels: Mapping[str, str]) -> bool:
    """Kubernetes selector semantics: labels must be a superset of selector.

    An empty selector matches nothing here, rather than everything. In
    Kubernetes an empty selector selects all pods; as an ONBOARDING
    expectation that would bind a service to whatever happened to be in the
    namespace, which is precisely the guess this module refuses to make.
    """
    if not selector:
        return False
    return all(labels.get(key) == value for key, value in selector.items())


def expected_workloads_from_manifest(document: Mapping[str, Any]) -> tuple[ExpectedWorkload, ...]:
    """Every (environment, service) pair the manifest declares.

    Services are project-level in the schema, so each declared service is
    expected in each declared Kubernetes environment. An environment with no
    namespace, or a non-Kubernetes runtime, expects nothing: there is
    nowhere to look.
    """
    spec = document.get("spec") or {}
    environments = spec.get("environments") or []
    services = spec.get("services") or []

    expected: list[ExpectedWorkload] = []
    for environment in environments:
        if str(environment.get("runtime") or "") != "kubernetes":
            continue
        namespace = str(environment.get("namespace") or "")
        if not namespace:
            continue
        env_name = str(environment.get("name") or "")
        for service in services:
            name = str(service.get("name") or "")
            if not name:
                continue
            selector = {str(k): str(v) for k, v in (service.get("workloadSelector") or {}).items()}
            expected.append(
                ExpectedWorkload(
                    environment=env_name,
                    namespace=namespace,
                    service=name,
                    selector=selector,
                )
            )
    return tuple(expected)


def expected_datastores_from_manifest(
    document: Mapping[str, Any],
) -> tuple[ExpectedWorkload, ...]:
    """Declared dataStores, per Kubernetes environment.

    Matched by NAME rather than by selector, because the schema gives a
    dataStore no `workloadSelector` — it is a named resource, and inventing
    a selector field to hold a guess would be worse than matching the name
    the manifest already had to state.

    A datastore whose workload is named something else simply will not match
    and is reported as expected-and-not-observed, which is honest: Drake
    cannot tell "named differently" from "absent" without being told.
    """
    spec = document.get("spec") or {}
    environments = spec.get("environments") or []
    datastores = spec.get("dataStores") or []

    expected: list[ExpectedWorkload] = []
    for environment in environments:
        if str(environment.get("runtime") or "") != "kubernetes":
            continue
        namespace = str(environment.get("namespace") or "")
        if not namespace:
            continue
        env_name = str(environment.get("name") or "")
        for store in datastores:
            name = str(store.get("name") or "")
            if not name:
                continue
            expected.append(
                ExpectedWorkload(
                    environment=env_name, namespace=namespace, service=name, selector={}
                )
            )
    return tuple(expected)


def evaluate_drift(
    expected: Iterable[ExpectedWorkload],
    observed: Iterable[ObservedWorkload],
    observed_namespaces: Iterable[str],
    expected_datastores: Iterable[ExpectedWorkload] = (),
) -> DriftReport:
    """Compare intent with inventory.

    `observed_namespaces` is separate from `observed` on purpose. A
    namespace that inventory has seen but which holds no workloads is a real
    and different answer from a namespace inventory has never seen, and
    inferring the namespace list from the workloads would make those two
    indistinguishable.

    `expected_datastores` accounts for workloads that are state rather than
    service — a database StatefulSet is declared, runs, and must not be
    reported as an undeclared surprise on every run. It is a separate input
    because a datastore earns no service→workload binding, and folding it
    into `expected` would inflate the binding count with non-bindings.
    """
    expected_list = list(expected)
    observed_list = list(observed)
    known_namespaces = set(observed_namespaces)

    items: list[DriftItem] = []
    claimed: set[tuple[str, str, str]] = set()

    # --- expectations ------------------------------------------------------
    for want in expected_list:
        if want.namespace not in known_namespaces:
            items.append(
                DriftItem(
                    kind=DriftKind.NAMESPACE_NOT_OBSERVED,
                    environment=want.environment,
                    namespace=want.namespace,
                    service=want.service,
                )
            )
            continue

        candidates = [
            found
            for found in observed_list
            if found.namespace == want.namespace and _matches(want.selector, found.labels)
        ]

        if not candidates:
            items.append(
                DriftItem(
                    kind=DriftKind.EXPECTED_NOT_OBSERVED,
                    environment=want.environment,
                    namespace=want.namespace,
                    service=want.service,
                )
            )
        elif len(candidates) > 1:
            items.append(
                DriftItem(
                    kind=DriftKind.AMBIGUOUS,
                    environment=want.environment,
                    namespace=want.namespace,
                    service=want.service,
                    candidates=tuple(sorted(f"{c.kind}/{c.name}" for c in candidates)),
                )
            )
            for found in candidates:
                claimed.add((found.namespace, found.kind, found.name))
        else:
            found = candidates[0]
            items.append(
                DriftItem(
                    kind=DriftKind.MATCHED,
                    environment=want.environment,
                    namespace=want.namespace,
                    service=want.service,
                    workload_kind=found.kind,
                    workload_name=found.name,
                )
            )
            claimed.add((found.namespace, found.kind, found.name))

    # --- declared datastores ----------------------------------------------
    for store in expected_datastores:
        if store.namespace not in known_namespaces:
            items.append(
                DriftItem(
                    kind=DriftKind.NAMESPACE_NOT_OBSERVED,
                    environment=store.environment,
                    namespace=store.namespace,
                    service=store.service,
                )
            )
            continue
        store_workload: ObservedWorkload | None = next(
            (
                candidate
                for candidate in observed_list
                if candidate.namespace == store.namespace and candidate.name == store.service
            ),
            None,
        )
        if store_workload is None:
            items.append(
                DriftItem(
                    kind=DriftKind.EXPECTED_NOT_OBSERVED,
                    environment=store.environment,
                    namespace=store.namespace,
                    service=store.service,
                )
            )
        else:
            items.append(
                DriftItem(
                    kind=DriftKind.DATASTORE_MATCHED,
                    environment=store.environment,
                    namespace=store.namespace,
                    service=store.service,
                    workload_kind=store_workload.kind,
                    workload_name=store_workload.name,
                )
            )
            claimed.add((store_workload.namespace, store_workload.kind, store_workload.name))

    # --- observations nothing claimed -------------------------------------
    #
    # Only within namespaces the manifest declares. A workload in some other
    # namespace is somebody else's, and reporting it here would turn every
    # project's drift view into a cluster-wide inventory dump.
    all_expected = expected_list + list(expected_datastores)
    declared_namespaces = {want.namespace for want in all_expected}
    environment_of = {want.namespace: want.environment for want in all_expected}

    for found in observed_list:
        if found.namespace not in declared_namespaces:
            continue
        if found.namespace not in known_namespaces:
            continue
        if (found.namespace, found.kind, found.name) in claimed:
            continue
        items.append(
            DriftItem(
                kind=DriftKind.OBSERVED_NOT_EXPECTED,
                environment=environment_of.get(found.namespace, ""),
                namespace=found.namespace,
                workload_kind=found.kind,
                workload_name=found.name,
            )
        )

    # Sorted so the report does not depend on API listing order. Two runs
    # over the same cluster state must produce byte-identical output, or
    # "did this change?" becomes unanswerable.
    return DriftReport(items=tuple(sorted(items, key=_sort_key)))


def _sort_key(item: DriftItem) -> tuple[str, str, str, str, str]:
    return (
        item.environment,
        item.namespace,
        str(item.kind),
        item.service,
        f"{item.workload_kind}/{item.workload_name}",
    )


def summarize(report: DriftReport) -> Sequence[str]:
    """One line per item, for logs and CLI output. Never includes labels."""
    return [
        f"{item.kind}: {item.environment}/{item.namespace}"
        + (f" service={item.service}" if item.service else "")
        + (f" workload={item.workload_kind}/{item.workload_name}" if item.workload_name else "")
        for item in report.items
    ]
