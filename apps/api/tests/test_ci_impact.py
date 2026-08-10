"""Targeted-test-selection regression for scripts/ci_impact.py.

Skipping a test is a safety decision, so these tests care most about the
cases where the classifier must NOT be clever: an unknown path, an empty
diff, a lockfile, anything security-shaped. Each of those has to widen back
out to the full suite, and there are assertions here that fail loudly if
someone later "optimises" one of them away.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("ci_impact", REPO_ROOT / "scripts" / "ci_impact.py")
assert _spec and _spec.loader
ci_impact = importlib.util.module_from_spec(_spec)
sys.modules["ci_impact"] = ci_impact
_spec.loader.exec_module(ci_impact)

classify = ci_impact.classify

HEAVY = ("run_e2e", "run_k3d_runtime", "run_integration")
ALL_JOBS = (
    "run_contracts",
    "run_web",
    "run_python",
    "run_go",
    "run_integration",
    "run_chart",
    "run_k3d_runtime",
    "run_e2e",
)


def test_docs_only_runs_no_application_test() -> None:
    result = classify(["README.md", "docs/adr/ADR-0026-x.md", "docs/runbooks/AGENT_DISCONNECT.md"])
    assert result["docs_only"] is True
    assert result["full_suite"] is False
    for job in ALL_JOBS:
        assert result[job] is False, f"{job} should not run for a docs-only change"


def test_docs_only_still_runs_the_security_gates() -> None:
    # The whole point: "it's only docs" is a claim the diff makes about
    # itself. Secret scanning is what checks that claim.
    result = classify(["README.md"])
    assert result["run_secret_scan"] is True
    assert result["run_dependency_scan"] is True


def test_a_single_non_docs_file_defeats_docs_only() -> None:
    result = classify(["README.md", "apps/web/src/app/page.tsx"])
    assert result["docs_only"] is False
    assert result["run_web"] is True


def test_unknown_path_fails_safe_to_the_full_suite() -> None:
    result = classify(["some/brand/new/thing.rs"])
    assert result["unknown"] is True
    assert result["full_suite"] is True
    for job in ALL_JOBS:
        assert result[job] is True, "an unrecognised path must run everything"
    assert "some/brand/new/thing.rs" in result["unmatched_paths"]


def test_empty_diff_fails_safe() -> None:
    # A failed diff and a genuinely empty diff look identical here, so the
    # only safe reading is "I do not know".
    result = classify([])
    assert result["unknown"] is True
    assert result["full_suite"] is True


def test_frontend_only_does_not_run_listener_or_k3d_smoke() -> None:
    result = classify(["apps/web/src/app/projects/page.tsx", "apps/web/src/components/Card.tsx"])
    assert result["frontend"] is True
    assert result["run_web"] is True
    assert result["run_e2e"] is True
    assert result["run_k3d_runtime"] is False
    assert result["run_chart"] is False
    assert result["run_go"] is False


def test_backend_module_does_not_drag_in_the_browser_e2e() -> None:
    result = classify(["apps/api/src/drake_api/catalog/router_clusters.py"])
    assert result["backend"] is True
    assert result["run_python"] is True
    assert result["run_integration"] is True
    assert result["run_e2e"] is False
    assert result["run_web"] is False
    assert result["run_k3d_runtime"] is False


def test_agent_only_runs_go_and_the_agent_chart() -> None:
    result = classify(["apps/cluster-agent/internal/inventory/collect.go"])
    assert result["agent"] is True
    assert result["run_go"] is True
    assert result["run_web"] is False
    assert result["run_python"] is False


def test_chart_only_runs_chart_validation() -> None:
    result = classify(["deploy/drake/templates/service.yaml"])
    assert result["helm_chart"] is True
    assert result["run_chart"] is True
    assert result["run_k3d_runtime"] is True
    assert result["run_web"] is False


@pytest.mark.parametrize(
    "path",
    [
        "deploy/drake/templates/internal-listener.yaml",
        "apps/api/src/drake_api/agents/run_internal_listener.py",
        "apps/api/src/drake_api/agents/internal_app.py",
    ],
)
def test_listener_or_pki_change_always_runs_the_k3d_runtime_smoke(path: str) -> None:
    # Three production rollouts died on defects only this smoke can see.
    # If this assertion ever fails, the gate that would have caught them is
    # being skipped.
    result = classify([path])
    assert result["listener_or_pki"] is True
    assert result["run_k3d_runtime"] is True


@pytest.mark.parametrize(
    "path",
    [
        "apps/api/src/drake_api/auth/session.py",
        "apps/api/src/drake_api/authz/rbac.py",
        "apps/api/src/drake_api/settings.py",
        "apps/api/src/drake_api/agents/router_internal.py",
    ],
)
def test_security_sensitive_change_takes_the_full_suite(path: str) -> None:
    result = classify([path])
    assert result["security_sensitive"] is True
    assert result["full_suite"] is True
    for job in ALL_JOBS:
        assert result[job] is True


@pytest.mark.parametrize("path", ["pnpm-lock.yaml", "uv.lock", "apps/cluster-agent/go.sum"])
def test_lockfile_change_takes_the_full_suite(path: str) -> None:
    result = classify([path])
    assert result["dependency_or_lockfile"] is True
    assert result["full_suite"] is True


@pytest.mark.parametrize(
    "path",
    [".github/workflows/ci.yml", "scripts/ci_impact.py", "scripts/chart_smoke_k3d.sh"],
)
def test_ci_or_test_infrastructure_change_takes_the_full_suite(path: str) -> None:
    # A change to the thing that decides what runs must be proven by
    # running everything, or the proof is circular.
    result = classify([path])
    assert result["ci_or_test_infrastructure"] is True
    assert result["full_suite"] is True


def test_migration_change_runs_integration_and_python() -> None:
    result = classify(["apps/api/alembic/versions/0021_something.py"])
    assert result["database_or_migration"] is True
    assert result["run_integration"] is True
    assert result["run_python"] is True


def test_shared_contract_change_runs_both_sides() -> None:
    result = classify(["packages/contracts/src/schemas/cluster.ts"])
    assert result["shared_contract"] is True
    assert result["run_contracts"] is True
    assert result["run_web"] is True
    assert result["run_python"] is True
    assert result["run_e2e"] is True


def test_models_are_treated_as_shared_and_as_database() -> None:
    result = classify(["apps/api/src/drake_api/models/cluster.py"])
    assert result["shared_contract"] is True
    assert result["database_or_migration"] is True
    assert result["run_integration"] is True
    assert result["run_e2e"] is True


def test_security_gates_are_never_selectable() -> None:
    # Every category, individually: none of them may switch a gate off.
    for path in [
        "README.md",
        "apps/web/src/x.tsx",
        "apps/api/src/drake_api/x.py",
        "deploy/drake/templates/x.yaml",
        "apps/cluster-agent/x.go",
        "unknown/path.zzz",
    ]:
        result = classify([path])
        assert result["run_secret_scan"] is True, path
        assert result["run_dependency_scan"] is True, path


def test_mixed_change_is_the_union_not_the_first_match() -> None:
    result = classify(
        ["apps/web/src/app/page.tsx", "apps/cluster-agent/main.go", "deploy/drake/values.yaml"]
    )
    assert result["run_web"] is True
    assert result["run_go"] is True
    assert result["run_chart"] is True


def test_every_declared_category_is_reported() -> None:
    result = classify(["README.md"])
    for category in ci_impact.CATEGORIES:
        assert category in result, f"{category} missing from the classifier output"


def test_glob_matches_a_directory_and_its_descendants() -> None:
    assert classify(["deploy/drake/values.yaml"])["helm_chart"] is True
    assert classify(["deploy/drake/templates/deep/nested.yaml"])["helm_chart"] is True
