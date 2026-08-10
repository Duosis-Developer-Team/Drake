"""The five absences must stay five different things.

`not_applicable`, `unknown`, `unavailable`, `stale` and `unhealthy` were one
word before this. Each pair below is a specific way a project could be
reported as fine when nothing has ever looked at it, so the tests are mostly
about what must NOT be equal.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import yaml
from drake_api.catalog.external_runtime import (
    Availability,
    DependencyClass,
    HostingProvider,
    RuntimeKind,
    Verification,
    dependency_is_workload,
    field_availability,
    health_for_external,
    metrics_profile_state,
)

OBSERVED_AT = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# not_applicable is not missing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["cluster", "namespace", "agent", "workload_binding"])
def test_kubernetes_only_fields_are_not_applicable_for_external(field: str) -> None:
    assert field_availability(RuntimeKind.EXTERNAL, field) is Availability.NOT_APPLICABLE


@pytest.mark.parametrize("field", ["cluster", "namespace", "agent", "workload_binding"])
def test_the_same_fields_do_apply_to_kubernetes(field: str) -> None:
    # None means "the question applies" — so a Kubernetes environment with no
    # cluster is a real gap, not an inapplicable field.
    assert field_availability(RuntimeKind.KUBERNETES, field) is None


def test_a_field_that_applies_everywhere_is_never_not_applicable() -> None:
    assert field_availability(RuntimeKind.EXTERNAL, "branch") is None


def test_not_applicable_is_distinct_from_unknown_and_unavailable() -> None:
    assert Availability.NOT_APPLICABLE != Availability.UNKNOWN
    assert Availability.UNKNOWN != Availability.UNAVAILABLE
    assert len({str(a) for a in Availability}) == 3


# --------------------------------------------------------------------------
# health and freshness
# --------------------------------------------------------------------------


def test_no_health_source_is_not_configured_and_never_healthy() -> None:
    verdict = health_for_external(health_source_configured=False, last_observed_at=None)
    assert verdict.status == "not_configured"
    assert verdict.availability is Availability.UNKNOWN
    assert verdict.status != "healthy"


def test_a_configured_source_with_no_observation_is_unavailable_not_stale() -> None:
    # `stale` says an answer aged. `unavailable` says there was never an
    # answer. Rendering the first for the second inherits the visual
    # language of data that merely went old.
    verdict = health_for_external(health_source_configured=True, last_observed_at=None)
    assert verdict.freshness == str(Availability.UNAVAILABLE)
    assert verdict.freshness != "stale"
    assert verdict.status == "unknown"


def test_freshness_becomes_meaningful_only_after_an_observation() -> None:
    verdict = health_for_external(health_source_configured=True, last_observed_at=OBSERVED_AT)
    assert verdict.freshness == "fresh"
    assert verdict.last_observed_at == OBSERVED_AT


def test_last_observed_at_is_never_derived_from_a_manifest_import() -> None:
    # There is no import-time input to this function at all, which is the
    # point: a manifest being read is not an observation of a runtime.
    verdict = health_for_external(health_source_configured=True, last_observed_at=None)
    assert verdict.last_observed_at is None
    assert verdict.as_dict()["last_observed_at"] is None


def test_unknown_is_not_unhealthy() -> None:
    verdict = health_for_external(health_source_configured=True, last_observed_at=None)
    assert verdict.status not in {"unhealthy", "critical", "degraded"}


# --------------------------------------------------------------------------
# verification: intent is not observation
# --------------------------------------------------------------------------


def test_verification_defaults_to_repository_intent() -> None:
    verdict = health_for_external(health_source_configured=False, last_observed_at=None)
    assert verdict.verification is Verification.REPOSITORY_INTENT


def test_the_three_verification_levels_are_distinct() -> None:
    assert len({str(v) for v in Verification}) == 3
    assert Verification.REPOSITORY_INTENT != Verification.OWNER_CONFIRMED
    assert Verification.OWNER_CONFIRMED != Verification.PROVIDER_OBSERVED


def test_repository_intent_does_not_imply_health() -> None:
    verdict = health_for_external(
        health_source_configured=False,
        last_observed_at=None,
        verification=Verification.REPOSITORY_INTENT,
    )
    assert verdict.status != "healthy"
    assert verdict.freshness == str(Availability.UNAVAILABLE)


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
