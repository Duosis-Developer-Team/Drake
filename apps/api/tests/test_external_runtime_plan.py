"""An external runtime must not acquire Kubernetes identity it does not have.

`build_plan` already skips cluster and namespace bindings when an
environment's runtime is not `kubernetes`. That behaviour was correct and
untested, which is the dangerous combination: deleting one `continue` would
give a Vercel-hosted application an `unmapped` cluster binding, and
`unmapped` is a blocking action, so the project would appear to be waiting
on a cluster that is not part of its architecture at all.

These tests pin it from both sides — the external case gains no Kubernetes
entity, and the Kubernetes case keeps every one it had — so the guard
cannot be removed silently in either direction.
"""

from __future__ import annotations

import pytest
from drake_api.onboarding.model import CatalogSnapshot, EntityKind, build_plan

KUBERNETES_ENTITY_KINDS = {
    str(EntityKind.CLUSTER_BINDING),
    str(EntityKind.NAMESPACE_BINDING),
    str(EntityKind.WORKLOAD_BINDING),
}


def document(*environments: dict) -> dict:
    return {
        "apiVersion": "drake.duosis.com/v1alpha1",
        "kind": "ProjectObservability",
        "metadata": {"name": "example", "displayName": "Example"},
        "spec": {
            "repository": {
                "provider": "github",
                "owner": "example-org",
                "name": "example",
                "defaultBranch": "main",
            },
            "owners": [{"team": "platform", "role": "primary"}],
            "environments": list(environments),
            "services": [
                {
                    "name": "web",
                    "component": "web",
                    "runtime": "nextjs",
                    "metricsProfile": "nextjs-v1",
                }
            ],
            "tenantModel": {"mode": "none"},
        },
    }


EXTERNAL_ENV = {
    "name": "prod",
    "runtime": "external",
    "branch": "main",
    "criticality": "medium",
}
KUBERNETES_ENV = {
    "name": "dev",
    "runtime": "kubernetes",
    "branch": "main",
    "criticality": "medium",
    "clusterRef": "cluster-a",
    "namespace": "example-dev",
}


def plan_for(*environments: dict, snapshot: CatalogSnapshot | None = None):
    return build_plan(
        document(*environments),
        snapshot or CatalogSnapshot(metric_profiles=frozenset({"nextjs-v1"})),
        repository_row_id="00000000-0000-0000-0000-000000000001",
    )


def kinds(plan) -> set[str]:
    return {item.entity_kind for item in plan.items}


def test_external_environment_produces_no_kubernetes_binding() -> None:
    plan = plan_for(EXTERNAL_ENV)
    assert not (kinds(plan) & KUBERNETES_ENTITY_KINDS), (
        "an external runtime acquired a Kubernetes binding it has no basis for"
    )


def test_external_environment_still_produces_the_environment_itself() -> None:
    # Skipping cluster identity must not skip the environment. Otherwise an
    # externally hosted project could not be onboarded at all.
    plan = plan_for(EXTERNAL_ENV)
    assert str(EntityKind.ENVIRONMENT) in kinds(plan)


def test_external_environment_is_not_blocked_by_a_missing_cluster() -> None:
    # `unmapped` is a blocking action. If an external environment produced
    # an unmapped cluster binding, the project would wait forever on a
    # cluster that is not part of its architecture.
    plan = plan_for(EXTERNAL_ENV)
    unmapped = [i for i in plan.items if i.action == "unmapped"]
    assert not any(i.entity_kind in KUBERNETES_ENTITY_KINDS for i in unmapped)


def test_an_empty_catalog_does_not_invent_a_cluster_for_an_external_project() -> None:
    # No clusters registered at all — the state Drake is actually in.
    plan = plan_for(
        EXTERNAL_ENV, snapshot=CatalogSnapshot(metric_profiles=frozenset({"nextjs-v1"}))
    )
    assert not any(i.entity_kind == str(EntityKind.CLUSTER_BINDING) for i in plan.items)
    assert not any("cluster" in (i.proposed_name or "").lower() for i in plan.items)


def test_kubernetes_environment_still_gets_its_bindings() -> None:
    # The other half of the guard: narrowing external must not narrow
    # Kubernetes. LogiSlot and Hermes depend on this.
    plan = plan_for(KUBERNETES_ENV)
    assert str(EntityKind.CLUSTER_BINDING) in kinds(plan)


def test_mixed_project_binds_only_the_kubernetes_environment() -> None:
    plan = plan_for(KUBERNETES_ENV, EXTERNAL_ENV)
    bindings = [i for i in plan.items if i.entity_kind == str(EntityKind.CLUSTER_BINDING)]
    assert len(bindings) == 1
    assert all("prod" not in i.item_key for i in bindings)


@pytest.mark.parametrize("runtime", ["external", "", "serverless", "unknown"])
def test_only_kubernetes_earns_kubernetes_bindings(runtime: str) -> None:
    # Anything that is not exactly `kubernetes` must not produce cluster
    # identity — including an empty or unrecognised value, which is the
    # fail-safe direction.
    environment = {**EXTERNAL_ENV, "runtime": runtime}
    plan = plan_for(environment)
    assert not (kinds(plan) & KUBERNETES_ENTITY_KINDS)


def test_external_environment_carries_no_namespace_in_its_metadata() -> None:
    # A namespace on an external environment would be a fabricated
    # Kubernetes fact, and downstream code keys workload attribution off it.
    plan = plan_for(EXTERNAL_ENV)
    environment_items = [i for i in plan.items if i.entity_kind == str(EntityKind.ENVIRONMENT)]
    assert environment_items
    for item in environment_items:
        proposed = item.detail.get("proposed") or {}
        assert not proposed.get("namespace")
        assert not proposed.get("cluster_ref")
