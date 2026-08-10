"""Hermes onboarding: two environments, ten bindings, one excluded namespace.

Hermes is the first project where the same workload names exist in more
than one namespace — `core-service` runs in `hermes-dev`, in `hermes-test`,
and in a third `hermes` namespace that is not part of this project. That
third one has been in ImagePullBackOff for a month, so if namespaces were
ever conflated its failure would surface as dev's and test's failure.

These tests pin that separation down, and pin the binding count, because
"ten bindings" is only a meaningful acceptance criterion if something fails
when it silently becomes nine or eleven.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from drake_api.onboarding.drift import (
    DriftKind,
    ObservedWorkload,
    evaluate_drift,
    expected_datastores_from_manifest,
    expected_workloads_from_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST = REPO_ROOT / "packages" / "contracts" / "onboarding" / "hermes.project.yaml"

DEPLOYMENTS = (
    "auth-service",
    "core-service",
    "frontend",
    "hermes-mcp",
    "reporting-service",
)
DATABASES = ("auth-db", "core-db")
ENVIRONMENTS = {"dev": "hermes-dev", "test": "hermes-test"}


def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def observed_reality() -> list[ObservedWorkload]:
    """Exactly what a read-only look at the cluster returned on 2026-08-10.

    Completed Jobs are excluded on purpose: they are spawned by the
    CronJobs, they come and go on a schedule, and feeding churn into a drift
    report is how the report stops being read.
    """
    workloads: list[ObservedWorkload] = []
    for namespace in ENVIRONMENTS.values():
        for name in DEPLOYMENTS:
            workloads.append(ObservedWorkload(namespace, "Deployment", name, {"app": name}))
        for name in DATABASES:
            workloads.append(ObservedWorkload(namespace, "StatefulSet", name, {"app": name}))
        for name in ("hermes-api-cleanup", "task-auto-archive"):
            workloads.append(ObservedWorkload(namespace, "CronJob", name, {}))
    workloads.append(ObservedWorkload("hermes-test", "CronJob", "hermes-weekly-backup", {}))
    return workloads


# --------------------------------------------------------------------------
# Exactly two environments
# --------------------------------------------------------------------------


def test_manifest_declares_exactly_dev_and_test() -> None:
    environments = manifest()["spec"]["environments"]
    assert {e["name"] for e in environments} == {"dev", "test"}
    assert {e["namespace"] for e in environments} == {"hermes-dev", "hermes-test"}


def test_the_generic_hermes_namespace_is_not_an_environment() -> None:
    # It exists on the cluster and is deliberately out of scope. A bare
    # `hermes` namespace would be a natural third entry and must not be one.
    namespaces = {e["namespace"] for e in manifest()["spec"]["environments"]}
    assert "hermes" not in namespaces


def test_generic_namespace_workloads_cannot_become_dev_or_test_evidence() -> None:
    # The decisive case: `hermes/core-service` has the same NAME as the
    # workloads in dev and test. Matching on name alone would let a
    # namespace nobody onboarded answer for two that were.
    expected = expected_workloads_from_manifest(manifest())
    observed = [
        ObservedWorkload("hermes", "Deployment", name, {"app": name}) for name in DEPLOYMENTS
    ]
    report = evaluate_drift(
        expected,
        observed,
        observed_namespaces=["hermes", "hermes-dev", "hermes-test"],
        expected_datastores=expected_datastores_from_manifest(manifest()),
    )
    assert not report.of_kind(DriftKind.MATCHED), "a foreign namespace satisfied an expectation"
    assert all(item.namespace != "hermes" for item in report.items)


# --------------------------------------------------------------------------
# Ten bindings
# --------------------------------------------------------------------------


def test_exactly_ten_service_workload_bindings() -> None:
    expected = expected_workloads_from_manifest(manifest())
    assert len(expected) == 10, "five services across two environments"
    assert len([w for w in expected if w.environment == "dev"]) == 5
    assert len([w for w in expected if w.environment == "test"]) == 5


def test_all_ten_bindings_match_observed_reality() -> None:
    report = evaluate_drift(
        expected_workloads_from_manifest(manifest()),
        observed_reality(),
        observed_namespaces=list(ENVIRONMENTS.values()),
        expected_datastores=expected_datastores_from_manifest(manifest()),
    )
    matched = report.of_kind(DriftKind.MATCHED)
    assert len(matched) == 10
    assert {i.environment for i in matched} == {"dev", "test"}


def test_databases_are_datastores_not_service_bindings() -> None:
    # Counting them as services would report fourteen bindings for a system
    # that has ten, and give state a service→workload binding it never has.
    services = {s["name"] for s in manifest()["spec"]["services"]}
    stores = {d["name"] for d in manifest()["spec"]["dataStores"]}
    assert stores == set(DATABASES)
    assert not (services & stores)

    report = evaluate_drift(
        expected_workloads_from_manifest(manifest()),
        observed_reality(),
        observed_namespaces=list(ENVIRONMENTS.values()),
        expected_datastores=expected_datastores_from_manifest(manifest()),
    )
    assert len(report.of_kind(DriftKind.DATASTORE_MATCHED)) == 4
    # And no database appears as an undeclared surprise.
    surprises = {i.workload_name for i in report.of_kind(DriftKind.OBSERVED_NOT_EXPECTED)}
    assert not (surprises & set(DATABASES))


def test_cronjobs_are_reported_as_undeclared_rather_than_hidden() -> None:
    # Five scheduled jobs are running that the manifest does not describe.
    # That is a real finding with a real resolution (declare them, or decide
    # they are out of model) — so it is surfaced, not filtered away.
    report = evaluate_drift(
        expected_workloads_from_manifest(manifest()),
        observed_reality(),
        observed_namespaces=list(ENVIRONMENTS.values()),
        expected_datastores=expected_datastores_from_manifest(manifest()),
    )
    undeclared = report.of_kind(DriftKind.OBSERVED_NOT_EXPECTED)
    assert {i.workload_kind for i in undeclared} == {"CronJob"}
    assert len(undeclared) == 5


# --------------------------------------------------------------------------
# dev and test never merge
# --------------------------------------------------------------------------


def test_dev_and_test_findings_are_separately_attributed() -> None:
    report = evaluate_drift(
        expected_workloads_from_manifest(manifest()),
        observed_reality(),
        observed_namespaces=list(ENVIRONMENTS.values()),
        expected_datastores=expected_datastores_from_manifest(manifest()),
    )
    for item in report.items:
        assert item.environment in {"dev", "test"}
        assert ENVIRONMENTS[item.environment] == item.namespace


def test_a_healthy_dev_does_not_make_test_look_observed() -> None:
    # The failure this prevents: five services matched in dev, test's
    # namespace never seen, and a combined view calling the project fine.
    dev_only = [w for w in observed_reality() if w.namespace == "hermes-dev"]
    report = evaluate_drift(
        expected_workloads_from_manifest(manifest()),
        dev_only,
        observed_namespaces=["hermes-dev"],
        expected_datastores=expected_datastores_from_manifest(manifest()),
    )
    dev = {i.kind for i in report.items if i.environment == "dev"}
    test = {i.kind for i in report.items if i.environment == "test"}
    assert DriftKind.MATCHED in dev
    assert test == {DriftKind.NAMESPACE_NOT_OBSERVED}
    assert report.in_sync is False


def test_a_broken_test_does_not_drag_dev_down() -> None:
    # And the reverse: test losing every workload must not unmatch dev.
    without_test_deployments = [
        w
        for w in observed_reality()
        if not (w.namespace == "hermes-test" and w.kind == "Deployment")
    ]
    report = evaluate_drift(
        expected_workloads_from_manifest(manifest()),
        without_test_deployments,
        observed_namespaces=list(ENVIRONMENTS.values()),
        expected_datastores=expected_datastores_from_manifest(manifest()),
    )
    dev_matched = [i for i in report.of_kind(DriftKind.MATCHED) if i.environment == "dev"]
    test_missing = [
        i for i in report.of_kind(DriftKind.EXPECTED_NOT_OBSERVED) if i.environment == "test"
    ]
    assert len(dev_matched) == 5
    assert len(test_missing) == 5


# --------------------------------------------------------------------------
# The MCP boundary
# --------------------------------------------------------------------------


def test_mcp_is_a_service_and_never_a_datastore() -> None:
    document = manifest()
    mcp = next(s for s in document["spec"]["services"] if s["name"] == "hermes-mcp")
    assert mcp["component"] == "mcp"
    assert "hermes-mcp" not in {d["name"] for d in document["spec"]["dataStores"]}


def test_mcp_declares_no_database_or_credential_reference() -> None:
    # Its only upstream is core-service's public API over HTTP. A datastore
    # or secret reference here would model a boundary the deployment
    # deliberately does not cross.
    mcp = next(s for s in manifest()["spec"]["services"] if s["name"] == "hermes-mcp")
    serialized = yaml.safe_dump(mcp).lower()
    for forbidden in ("postgres", "database", "db", "secret", "token", "credential", "password"):
        assert forbidden not in serialized, f"MCP service declares {forbidden!r}"


def test_mcp_binds_per_environment_like_every_other_service() -> None:
    expected = expected_workloads_from_manifest(manifest())
    mcp = [w for w in expected if w.service == "hermes-mcp"]
    assert {w.environment for w in mcp} == {"dev", "test"}


# --------------------------------------------------------------------------
# Nothing sensitive, nothing invented
# --------------------------------------------------------------------------


def test_manifest_carries_no_secret_or_connection_material() -> None:
    text = MANIFEST.read_text().lower()
    for forbidden in (
        "password",
        "postgresql://",
        "postgres://",
        "redis://",
        "bearer ",
        "-----begin",
        "connectionstring",
        "client_secret",
    ):
        assert forbidden not in text, f"manifest contains {forbidden!r}"


def test_tenant_model_is_none_as_evidenced() -> None:
    assert manifest()["spec"]["tenantModel"]["mode"] == "none"


@pytest.mark.parametrize("service", DEPLOYMENTS)
def test_every_observed_deployment_is_declared(service: str) -> None:
    assert service in {s["name"] for s in manifest()["spec"]["services"]}


def test_report_is_deterministic_for_hermes() -> None:
    expected = expected_workloads_from_manifest(manifest())
    stores = expected_datastores_from_manifest(manifest())
    forward = observed_reality()
    backward = list(reversed(forward))
    first = evaluate_drift(
        expected, forward, list(ENVIRONMENTS.values()), expected_datastores=stores
    )
    second = evaluate_drift(
        expected, backward, list(reversed(list(ENVIRONMENTS.values()))), expected_datastores=stores
    )
    assert first.as_dict() == second.as_dict()
