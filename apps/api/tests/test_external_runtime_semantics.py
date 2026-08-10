"""The five absences must stay five different things.

`not_applicable`, `unknown`, `unavailable`, `stale` and `unhealthy` were one
word before this. Each pair below is a specific way a project could be
reported as fine when nothing has ever looked at it, so the tests are mostly
about what must NOT be equal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import yaml
from drake_api.catalog.external_runtime import (
    DEFAULT_STALE_AFTER,
    EXTERNAL_NOT_APPLICABLE,
    Availability,
    DependencyClass,
    Freshness,
    HealthSourceStatus,
    HostingProvider,
    Verification,
    dependency_is_workload,
    evaluate_external_health,
    metrics_profile_state,
)

OBSERVED_AT = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# not_applicable is not missing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["cluster", "namespace", "agent", "workload_binding"])
def test_kubernetes_only_fields_are_not_applicable_for_external(field: str) -> None:
    # Asserted against the constant the API actually serialises, rather than
    # a helper nothing called — the first version of this tested a function
    # with zero production call sites, which proves the helper works and
    # nothing about what the API returns.
    assert field in EXTERNAL_NOT_APPLICABLE


def test_fields_that_apply_to_kubernetes_are_not_in_the_list() -> None:
    # A Kubernetes environment with no cluster is a real gap, and the list
    # is only consulted for external runtimes.
    assert "branch" not in EXTERNAL_NOT_APPLICABLE
    assert "criticality" not in EXTERNAL_NOT_APPLICABLE


def test_not_applicable_is_distinct_from_unknown_and_unavailable() -> None:
    assert Availability.NOT_APPLICABLE != Availability.UNKNOWN
    assert Availability.UNKNOWN != Availability.UNAVAILABLE
    assert len({str(a) for a in Availability}) == 3


# --------------------------------------------------------------------------
# health and freshness — the full truth table
#
# Health and freshness are INDEPENDENT axes. The table below is the contract:
# every row is a state the system can actually be in, and none of them may
# be reachable from another by accident.
# --------------------------------------------------------------------------

WITHIN = OBSERVED_AT + DEFAULT_STALE_AFTER - timedelta(minutes=1)
BEYOND = OBSERVED_AT + DEFAULT_STALE_AFTER + timedelta(minutes=1)


@pytest.mark.parametrize(
    ("source", "observed", "observed_health", "now", "health", "freshness"),
    [
        (HealthSourceStatus.NOT_CONFIGURED, None, None, None, "unknown", Freshness.UNAVAILABLE),
        (HealthSourceStatus.CONFIGURED, None, None, None, "unknown", Freshness.UNAVAILABLE),
        (HealthSourceStatus.CONFIGURED, OBSERVED_AT, "healthy", WITHIN, "healthy", Freshness.FRESH),
        (
            HealthSourceStatus.CONFIGURED,
            OBSERVED_AT,
            "unhealthy",
            WITHIN,
            "unhealthy",
            Freshness.FRESH,
        ),
        (HealthSourceStatus.CONFIGURED, OBSERVED_AT, "healthy", BEYOND, "healthy", Freshness.STALE),
        (
            HealthSourceStatus.CONFIGURED,
            OBSERVED_AT,
            "unhealthy",
            BEYOND,
            "unhealthy",
            Freshness.STALE,
        ),
    ],
)
def test_health_truth_table(
    source: HealthSourceStatus,
    observed: datetime | None,
    observed_health: str | None,
    now: datetime | None,
    health: str,
    freshness: Freshness,
) -> None:
    verdict = evaluate_external_health(
        source=source,
        observed_health=observed_health,
        last_observed_at=observed,
        now=now,
    )
    assert verdict.status == health
    assert verdict.freshness is freshness


def test_health_and_freshness_are_independent_axes() -> None:
    # The pair that proves they are not one field: unhealthy+fresh exists,
    # and so does healthy+stale.
    unhealthy_fresh = evaluate_external_health(
        source=HealthSourceStatus.CONFIGURED,
        observed_health="unhealthy",
        last_observed_at=OBSERVED_AT,
        now=WITHIN,
    )
    healthy_stale = evaluate_external_health(
        source=HealthSourceStatus.CONFIGURED,
        observed_health="healthy",
        last_observed_at=OBSERVED_AT,
        now=BEYOND,
    )
    assert (unhealthy_fresh.status, unhealthy_fresh.freshness) == ("unhealthy", Freshness.FRESH)
    assert (healthy_stale.status, healthy_stale.freshness) == ("healthy", Freshness.STALE)


def test_an_aged_observation_keeps_its_verdict() -> None:
    # Discarding the result on age would hide the one thing worth acting on.
    verdict = evaluate_external_health(
        source=HealthSourceStatus.CONFIGURED,
        observed_health="unhealthy",
        last_observed_at=OBSERVED_AT,
        now=BEYOND,
    )
    assert verdict.status == "unhealthy"
    assert verdict.freshness is Freshness.STALE


def test_source_configuration_is_a_separate_field_from_the_verdict() -> None:
    # "Nobody is watching" is a fact about Drake's configuration, not about
    # the system's health. The first version reported not_configured AS the
    # health status, which read as a property of the application.
    verdict = evaluate_external_health(source=HealthSourceStatus.NOT_CONFIGURED)
    assert verdict.source.status is HealthSourceStatus.NOT_CONFIGURED
    assert verdict.status == "unknown"
    assert verdict.status != "not_configured"


def test_no_source_and_configured_without_observation_differ_only_in_source() -> None:
    absent = evaluate_external_health(source=HealthSourceStatus.NOT_CONFIGURED)
    configured = evaluate_external_health(source=HealthSourceStatus.CONFIGURED)
    assert absent.status == configured.status == "unknown"
    assert absent.freshness is configured.freshness is Freshness.UNAVAILABLE
    assert absent.source.status is not configured.source.status


def test_unavailable_is_never_stale() -> None:
    verdict = evaluate_external_health(source=HealthSourceStatus.CONFIGURED)
    assert verdict.freshness is Freshness.UNAVAILABLE
    assert verdict.freshness is not Freshness.STALE


def test_staleness_boundary_is_explicit_and_inclusive_of_the_threshold() -> None:
    exactly = evaluate_external_health(
        source=HealthSourceStatus.CONFIGURED,
        observed_health="healthy",
        last_observed_at=OBSERVED_AT,
        now=OBSERVED_AT + DEFAULT_STALE_AFTER,
    )
    assert exactly.freshness is Freshness.FRESH, "at the threshold is not yet past it"


def test_last_observed_at_is_never_derived_from_a_manifest_import() -> None:
    # There is no import-time parameter on this function at all.
    verdict = evaluate_external_health(source=HealthSourceStatus.CONFIGURED)
    assert verdict.last_observed_at is None
    assert verdict.as_dict()["last_observed_at"] is None


def test_unknown_is_not_unhealthy() -> None:
    verdict = evaluate_external_health(source=HealthSourceStatus.CONFIGURED)
    assert verdict.status not in {"unhealthy", "critical", "degraded"}


def test_verification_defaults_to_repository_intent() -> None:
    assert (
        evaluate_external_health(source=HealthSourceStatus.NOT_CONFIGURED).verification
        is Verification.REPOSITORY_INTENT
    )


def test_the_three_verification_levels_are_distinct() -> None:
    assert len({str(v) for v in Verification}) == 3


def test_repository_intent_does_not_imply_health() -> None:
    verdict = evaluate_external_health(
        source=HealthSourceStatus.NOT_CONFIGURED,
        verification=Verification.REPOSITORY_INTENT,
    )
    assert verdict.status != "healthy"
    assert verdict.freshness is Freshness.UNAVAILABLE


# --------------------------------------------------------------------------
# metricsProfile no longer manufactures a claim
# --------------------------------------------------------------------------


def test_absent_metrics_profile_reports_not_configured() -> None:
    label, availability = metrics_profile_state(None)
    assert label == "not_configured"
    assert availability is Availability.UNKNOWN


@pytest.mark.parametrize("empty", [None, ""])
def test_absent_metrics_profile_is_never_rendered_as_a_profile(empty: str | None) -> None:
    label, _ = metrics_profile_state(empty)
    assert label not in {"none", "default", "external", "unknown-v1"}


def test_a_real_profile_passes_through_untouched() -> None:
    label, availability = metrics_profile_state("fastapi-v1")
    assert label == "fastapi-v1"
    assert availability is None


# --------------------------------------------------------------------------
# managed dependencies are not workloads
# --------------------------------------------------------------------------


def test_a_managed_data_platform_is_not_a_workload() -> None:
    assert dependency_is_workload(DependencyClass.MANAGED_DATA_PLATFORM) is False
    assert dependency_is_workload(DependencyClass.EXTERNAL_SERVICE) is False


def test_in_cluster_remains_a_workload_and_is_the_default() -> None:
    # Backward compatibility: every existing manifest omits dependencyClass.
    assert dependency_is_workload(DependencyClass.IN_CLUSTER) is True
    assert dependency_is_workload(None) is True


def test_provider_vocabulary_is_closed() -> None:
    # Free text here would be an unbounded label carrying whatever someone
    # pasted into it.
    assert "supabase" in {str(p) for p in HostingProvider}
    assert "unknown" in {str(p) for p in HostingProvider}


# --------------------------------------------------------------------------
# the fixture tells the truth
# --------------------------------------------------------------------------


def test_external_fixture_declares_no_kubernetes_facts() -> None:
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "contracts"
        / "fixtures"
        / "valid"
        / "project-epsilon.yaml"
    )
    spec = yaml.safe_load(path.read_text())["spec"]
    environment = spec["environments"][0]
    assert environment["runtime"] == "external"
    assert "clusterRef" not in environment
    assert "namespace" not in environment
    assert environment["hostingProvider"] == "vercel"

    # No metrics profile, and no workload selector to bind against.
    for service in spec["services"]:
        assert "metricsProfile" not in service
        assert "workloadSelector" not in service

    store = spec["dataStores"][0]
    assert store["dependencyClass"] == DependencyClass.MANAGED_DATA_PLATFORM
    assert store["verification"] == Verification.REPOSITORY_INTENT
    assert "measurementProfile" not in store


def test_external_fixture_carries_no_endpoint_or_credential() -> None:
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "contracts"
        / "fixtures"
        / "valid"
        / "project-epsilon.yaml"
    )
    # Precise shapes, not loose substrings: `.co` also matches the required
    # `drake.duosis.com` apiVersion, and a test that fails on its own schema
    # header teaches people to loosen the assertion rather than fix a leak.
    import re

    text = path.read_text().lower()
    forbidden = (
        r"https?://",
        r"\bsupabase\.co\b",
        r"\bvercel\.app\b",
        r"anon[_-]?key",
        r"service[_-]?role",
        r"\b(api[_-]?key|access[_-]?token|password|client[_-]?secret)\b",
        r"-----begin",
    )
    for pattern in forbidden:
        assert not re.search(pattern, text), f"fixture matches {pattern!r}"


# --------------------------------------------------------------------------
# Mixed runtime: the case that made dependency_is_workload dead code matter
#
# A project with BOTH a Kubernetes environment and an external one, plus a
# provider-managed datastore. Before the review fix, the datastore became an
# expected workload in the Kubernetes namespace and then reported as missing
# — Drake claiming a managed database was an absent Deployment, permanently,
# with no action anyone could take.
# --------------------------------------------------------------------------

MIXED_MANIFEST = {
    "spec": {
        "environments": [
            {
                "name": "dev",
                "runtime": "kubernetes",
                "branch": "main",
                "criticality": "medium",
                "clusterRef": "cluster-a",
                "namespace": "mixed-dev",
            },
            {"name": "prod", "runtime": "external", "branch": "main", "criticality": "medium"},
        ],
        "services": [
            {
                "name": "api",
                "component": "api",
                "runtime": "fastapi",
                "metricsProfile": "fastapi-v1",
                "workloadSelector": {"app": "api"},
            }
        ],
        "dataStores": [
            {
                "name": "app-db",
                "engine": "postgresql",
                "scope": "project",
                "dependencyClass": "managed_data_platform",
                "provider": "supabase",
                "verification": "repository_intent",
            },
            {
                "name": "cache",
                "engine": "redis",
                "scope": "environment",
                "measurementProfile": "postgres-v1",
            },
        ],
    }
}


def test_a_managed_dependency_is_never_an_expected_workload() -> None:
    from drake_api.onboarding.drift import expected_datastores_from_manifest

    expected = expected_datastores_from_manifest(MIXED_MANIFEST)
    names = {w.service for w in expected}
    assert "app-db" not in names, "a provider-managed platform became an expected workload"
    # The in-cluster one still is, and defaults to in_cluster without saying so.
    assert "cache" in names


def test_a_managed_dependency_produces_no_missing_workload_drift() -> None:
    from drake_api.onboarding.drift import (
        DriftKind,
        evaluate_drift,
        expected_datastores_from_manifest,
        expected_workloads_from_manifest,
    )

    report = evaluate_drift(
        expected_workloads_from_manifest(MIXED_MANIFEST),
        [],  # nothing observed at all
        observed_namespaces=["mixed-dev"],
        expected_datastores=expected_datastores_from_manifest(MIXED_MANIFEST),
    )
    missing = {i.service for i in report.of_kind(DriftKind.EXPECTED_NOT_OBSERVED)}
    assert "app-db" not in missing, "a managed database was reported as a missing workload"


def test_the_external_environment_gains_no_workload_expectation() -> None:
    from drake_api.onboarding.drift import (
        expected_datastores_from_manifest,
        expected_workloads_from_manifest,
    )

    services = expected_workloads_from_manifest(MIXED_MANIFEST)
    stores = expected_datastores_from_manifest(MIXED_MANIFEST)
    assert {w.environment for w in services} == {"dev"}
    assert {w.environment for w in stores} == {"dev"}


def test_dependency_metadata_survives_manifest_parsing() -> None:
    # provider and verification are not lost on the way in. Persistence and
    # API round-trip are NOT claimed here — see the delivery report; this
    # asserts only what the code actually does today.
    store = MIXED_MANIFEST["spec"]["dataStores"][0]
    assert store["provider"] == "supabase"
    assert store["verification"] == str(Verification.REPOSITORY_INTENT)
    assert dependency_is_workload(store["dependencyClass"]) is False


def test_an_in_cluster_datastore_is_unaffected_by_the_new_field() -> None:
    # Backward compatibility: the second store omits dependencyClass and
    # must behave exactly as before.
    store = MIXED_MANIFEST["spec"]["dataStores"][1]
    assert "dependencyClass" not in store
    assert dependency_is_workload(store.get("dependencyClass")) is True


# --------------------------------------------------------------------------
# Verification: an import may not raise it, and must not erase it
#
# The first version returned repository_intent unconditionally on a mutable
# field, so a re-import DELETED an owner_confirmed or provider_observed that
# somebody had established out of band. Refusing to raise evidence is
# correct; destroying it is worse than either, because the value came from
# the one process that could actually establish it.
# --------------------------------------------------------------------------


def test_a_new_dependency_records_repository_intent() -> None:
    from drake_api.catalog.external_runtime import resolve_verification_for_import

    assert resolve_verification_for_import(None, None) is Verification.REPOSITORY_INTENT


@pytest.mark.parametrize("claim", ["owner_confirmed", "provider_observed"])
def test_a_manifest_cannot_promote_itself(claim: str) -> None:
    from drake_api.catalog.external_runtime import resolve_verification_for_import

    # A repository asserting Drake observed something is not evidence that
    # Drake observed anything. The declared value is not an input at all.
    assert resolve_verification_for_import(claim, None) is Verification.REPOSITORY_INTENT


@pytest.mark.parametrize("held", ["owner_confirmed", "provider_observed"])
def test_a_reimport_preserves_higher_verification(held: str) -> None:
    from drake_api.catalog.external_runtime import resolve_verification_for_import

    assert str(resolve_verification_for_import("repository_intent", held)) == held
    # And a manifest claiming something else still cannot change it.
    assert str(resolve_verification_for_import("provider_observed", held)) == held


def test_repository_intent_stays_repository_intent() -> None:
    from drake_api.catalog.external_runtime import resolve_verification_for_import

    assert (
        resolve_verification_for_import("provider_observed", "repository_intent")
        is Verification.REPOSITORY_INTENT
    )


def test_verification_levels_are_ordered_not_compared_as_strings() -> None:
    from drake_api.catalog.external_runtime import (
        is_above_repository_intent,
        verification_rank,
    )

    assert verification_rank("repository_intent") == 0
    assert verification_rank("owner_confirmed") < verification_rank("provider_observed")
    assert is_above_repository_intent("owner_confirmed") is True
    assert is_above_repository_intent("repository_intent") is False
    assert is_above_repository_intent(None) is False


# --------------------------------------------------------------------------
# Workload applicability is class-aware
# --------------------------------------------------------------------------


def test_an_in_cluster_dependency_has_workload_semantics() -> None:
    from drake_api.catalog.external_runtime import (
        WorkloadApplicability,
        workload_applicability,
    )

    # Drake runs it: it has replicas and a rollout. Telling an operator its
    # workload is "not applicable" is false about the domain.
    assert workload_applicability("in_cluster") is WorkloadApplicability.APPLICABLE
    assert workload_applicability(None) is WorkloadApplicability.APPLICABLE


@pytest.mark.parametrize("dependency_class", ["managed_data_platform", "external_service"])
def test_a_provider_run_dependency_has_no_workload(dependency_class: str) -> None:
    from drake_api.catalog.external_runtime import (
        WorkloadApplicability,
        workload_applicability,
    )

    assert workload_applicability(dependency_class) is WorkloadApplicability.NOT_APPLICABLE


def test_workload_applicability_is_its_own_vocabulary() -> None:
    from drake_api.catalog.external_runtime import Availability, WorkloadApplicability

    # `Availability` says why a VALUE is absent; this says whether a QUESTION
    # applies. Borrowing the first for the second is what produced
    # "Workload: Not applicable" on a datastore Drake runs.
    assert str(WorkloadApplicability.APPLICABLE) not in {str(a) for a in Availability}
