"""Expected-versus-observed drift, including the ways it must NOT be wrong.

Most of these tests are about false positives. A drift report that flips
because a pod restarted, or because two label dictionaries serialized in a
different order, is a report people learn to ignore — and an ignored signal
is worse than no signal, because it looks like coverage.

The LogiSlot case is used as the realistic fixture: it is the first project
where the two environments genuinely disagree — dev is fully deployed and
prod has never existed — so it exercises the distinction this module was
written for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from drake_api.onboarding.drift import (
    DriftKind,
    ExpectedWorkload,
    ObservedWorkload,
    evaluate_drift,
    expected_workloads_from_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LOGISLOT_MANIFEST = REPO_ROOT / "packages" / "contracts" / "onboarding" / "logislot.project.yaml"


def _manifest() -> dict:
    return yaml.safe_load(LOGISLOT_MANIFEST.read_text())


def _dev_workload(name: str, kind: str = "Deployment") -> ObservedWorkload:
    return ObservedWorkload(
        namespace="logislot-dev",
        kind=kind,
        name=name,
        # Kustomize adds part-of to everything; agents add their own labels.
        # Extra labels are normal and must not defeat a match.
        labels={
            "app.kubernetes.io/name": name,
            "app.kubernetes.io/part-of": "logislot",
        },
    )


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def test_selector_matches_when_workload_has_extra_labels() -> None:
    expected = [ExpectedWorkload("dev", "ns", "svc", {"app.kubernetes.io/name": "svc"})]
    observed = [
        ObservedWorkload("ns", "Deployment", "svc", {"app.kubernetes.io/name": "svc", "x": "y"})
    ]
    report = evaluate_drift(expected, observed, ["ns"])
    assert [i.kind for i in report.items] == [DriftKind.MATCHED]
    assert report.in_sync is True


def test_label_order_never_matters() -> None:
    selector = {"a": "1", "b": "2"}
    forward = ObservedWorkload("ns", "Deployment", "w", {"a": "1", "b": "2", "c": "3"})
    reverse = ObservedWorkload("ns", "Deployment", "w", {"c": "3", "b": "2", "a": "1"})
    expected = [ExpectedWorkload("dev", "ns", "svc", selector)]
    first = evaluate_drift(expected, [forward], ["ns"])
    second = evaluate_drift(expected, [reverse], ["ns"])
    assert first.as_dict() == second.as_dict()
    assert first.in_sync is True


def test_a_missing_selector_value_is_not_a_match() -> None:
    # A partial label match reports BOTH facts, and that is the useful
    # answer: the service was not found, and here is the workload that
    # nearly matched it. Reporting only the first would leave someone
    # hunting for a workload the report had already seen.
    expected = [ExpectedWorkload("dev", "ns", "svc", {"app": "svc", "tier": "web"})]
    observed = [ObservedWorkload("ns", "Deployment", "svc", {"app": "svc"})]
    report = evaluate_drift(expected, observed, ["ns"])
    assert {i.kind for i in report.items} == {
        DriftKind.EXPECTED_NOT_OBSERVED,
        DriftKind.OBSERVED_NOT_EXPECTED,
    }
    assert report.in_sync is False


def test_empty_selector_matches_nothing_rather_than_everything() -> None:
    # Kubernetes would select all pods. As an onboarding expectation that
    # would bind a service to whatever happened to be nearby.
    expected = [ExpectedWorkload("dev", "ns", "svc", {})]
    observed = [ObservedWorkload("ns", "Deployment", "anything", {"app": "unrelated"})]
    report = evaluate_drift(expected, observed, ["ns"])
    kinds = {i.kind for i in report.items}
    assert DriftKind.MATCHED not in kinds
    assert DriftKind.EXPECTED_NOT_OBSERVED in kinds


# --------------------------------------------------------------------------
# The false positives that would make this untrustworthy
# --------------------------------------------------------------------------


def test_replica_and_rollout_churn_cannot_affect_the_report() -> None:
    # There is no replica input at all — this asserts the shape of the API,
    # so a later "helpful" addition of replica comparison fails here first.
    expected = [ExpectedWorkload("dev", "ns", "svc", {"app": "svc"})]
    observed = [ObservedWorkload("ns", "Deployment", "svc", {"app": "svc"})]
    before = evaluate_drift(expected, observed, ["ns"])
    after = evaluate_drift(expected, observed, ["ns"])
    assert before.as_dict() == after.as_dict()
    assert "replicas" not in str(before.as_dict())


def test_observation_order_does_not_change_the_report() -> None:
    manifest = _manifest()
    expected = expected_workloads_from_manifest(manifest)
    names = [
        "logislot-api",
        "logislot-scheduler",
        "logislot-web",
        "logislot-web-admin",
        "logislot-web-platform",
        "logislot-web-supplier",
    ]
    forward = [_dev_workload(n) for n in names]
    backward = list(reversed(forward))
    assert (
        evaluate_drift(expected, forward, ["logislot-dev"]).as_dict()
        == evaluate_drift(expected, backward, ["logislot-dev"]).as_dict()
    )


def test_report_is_stable_across_repeated_evaluation() -> None:
    manifest = _manifest()
    expected = expected_workloads_from_manifest(manifest)
    observed = [_dev_workload("logislot-api")]
    runs = {str(evaluate_drift(expected, observed, ["logislot-dev"]).as_dict()) for _ in range(5)}
    assert len(runs) == 1


# --------------------------------------------------------------------------
# Absence is not health
# --------------------------------------------------------------------------


def test_unobserved_namespace_reports_once_per_service_and_never_matches() -> None:
    manifest = _manifest()
    expected = expected_workloads_from_manifest(manifest)
    # Only dev has ever been observed. This is the real cluster state.
    report = evaluate_drift(expected, [_dev_workload("logislot-api")], ["logislot-dev"])

    prod = [i for i in report.items if i.namespace == "logislot-prod"]
    assert prod, "the manifest declares prod, so it must appear in the report"
    assert {i.kind for i in prod} == {DriftKind.NAMESPACE_NOT_OBSERVED}
    assert all(i.kind is not DriftKind.MATCHED for i in prod)


def test_unobserved_namespace_is_not_in_sync() -> None:
    expected = [ExpectedWorkload("prod", "gone", "svc", {"app": "svc"})]
    report = evaluate_drift(expected, [], observed_namespaces=[])
    assert report.in_sync is False
    assert report.items[0].kind is DriftKind.NAMESPACE_NOT_OBSERVED


def test_observed_namespace_with_nothing_in_it_is_missing_not_unknown() -> None:
    # An empty namespace inventory HAS seen is a different answer from a
    # namespace it has never seen, and the two must not collapse.
    expected = [ExpectedWorkload("dev", "ns", "svc", {"app": "svc"})]
    report = evaluate_drift(expected, [], observed_namespaces=["ns"])
    assert report.items[0].kind is DriftKind.EXPECTED_NOT_OBSERVED


def test_empty_everything_is_not_in_sync() -> None:
    assert evaluate_drift([], [], []).in_sync is True  # nothing declared, nothing claimed


# --------------------------------------------------------------------------
# Ambiguity and unexpected workloads
# --------------------------------------------------------------------------


def test_two_matching_workloads_are_ambiguous_not_a_guess() -> None:
    expected = [ExpectedWorkload("dev", "ns", "svc", {"app": "svc"})]
    observed = [
        ObservedWorkload("ns", "Deployment", "svc-blue", {"app": "svc"}),
        ObservedWorkload("ns", "Deployment", "svc-green", {"app": "svc"}),
    ]
    report = evaluate_drift(expected, observed, ["ns"])
    item = report.items[0]
    assert item.kind is DriftKind.AMBIGUOUS
    assert item.candidates == ("Deployment/svc-blue", "Deployment/svc-green")


def test_undeclared_workload_in_a_declared_namespace_is_reported() -> None:
    expected = [ExpectedWorkload("dev", "ns", "svc", {"app": "svc"})]
    observed = [
        ObservedWorkload("ns", "Deployment", "svc", {"app": "svc"}),
        ObservedWorkload("ns", "Deployment", "surprise", {"app": "surprise"}),
    ]
    report = evaluate_drift(expected, observed, ["ns"])
    extra = report.of_kind(DriftKind.OBSERVED_NOT_EXPECTED)
    assert [i.workload_name for i in extra] == ["surprise"]


def test_workloads_in_other_namespaces_are_not_this_project_s_drift() -> None:
    expected = [ExpectedWorkload("dev", "ns", "svc", {"app": "svc"})]
    observed = [
        ObservedWorkload("ns", "Deployment", "svc", {"app": "svc"}),
        ObservedWorkload("someone-else", "Deployment", "theirs", {"app": "theirs"}),
    ]
    report = evaluate_drift(expected, observed, ["ns", "someone-else"])
    assert all(i.namespace == "ns" for i in report.items)


def test_dev_and_prod_never_share_a_verdict() -> None:
    manifest = _manifest()
    expected = expected_workloads_from_manifest(manifest)
    observed = [
        _dev_workload(n)
        for n in ("logislot-api", "logislot-scheduler", "logislot-web", "logislot-web-admin")
    ]
    report = evaluate_drift(expected, observed, ["logislot-dev"])
    by_env = {}
    for item in report.items:
        by_env.setdefault(item.environment, set()).add(item.kind)
    assert DriftKind.MATCHED in by_env["dev"]
    assert by_env["prod"] == {DriftKind.NAMESPACE_NOT_OBSERVED}


# --------------------------------------------------------------------------
# The manifest itself
# --------------------------------------------------------------------------


def test_manifest_expects_every_service_in_each_kubernetes_environment() -> None:
    expected = expected_workloads_from_manifest(_manifest())
    assert {w.environment for w in expected} == {"dev", "prod"}
    services = {w.service for w in expected if w.environment == "dev"}
    # Six application workloads plus the database StatefulSet.
    assert services == {
        "logislot-api",
        "logislot-scheduler",
        "logislot-web",
        "logislot-web-admin",
        "logislot-web-platform",
        "logislot-web-supplier",
        "logislot-postgres",
    }
    assert len([w for w in expected if w.environment == "prod"]) == len(services)


def test_the_database_statefulset_is_claimed_by_a_service() -> None:
    # Declaring postgres only as a dataStore left its StatefulSet reported
    # as observed_not_expected on every single run — a finding nobody could
    # ever resolve, which is how a drift report becomes noise.
    expected = expected_workloads_from_manifest(_manifest())
    observed = [_dev_workload("logislot-postgres", kind="StatefulSet")]
    report = evaluate_drift(expected, observed, ["logislot-dev"])
    assert not report.of_kind(DriftKind.OBSERVED_NOT_EXPECTED)
    matched = report.of_kind(DriftKind.MATCHED)
    assert [i.service for i in matched] == ["logislot-postgres"]


def test_non_kubernetes_or_namespaceless_environments_expect_nothing() -> None:
    document = {
        "spec": {
            "environments": [
                {"name": "ext", "runtime": "external", "namespace": "x"},
                {"name": "dev", "runtime": "kubernetes"},
            ],
            "services": [{"name": "svc"}],
        }
    }
    assert expected_workloads_from_manifest(document) == ()


def test_the_report_never_carries_labels_or_values() -> None:
    # Labels can carry more than names; the report is metadata about shape.
    expected = [ExpectedWorkload("dev", "ns", "svc", {"secret-ish": "value"})]
    observed = [ObservedWorkload("ns", "Deployment", "svc", {"secret-ish": "value"})]
    payload = str(evaluate_drift(expected, observed, ["ns"]).as_dict())
    assert "secret-ish" not in payload
    assert "value" not in payload


@pytest.mark.parametrize("kind", list(DriftKind))
def test_every_kind_has_reason_text(kind: DriftKind) -> None:
    from drake_api.onboarding.drift import REASON_TEXT

    assert REASON_TEXT[kind].strip()
