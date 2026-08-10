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


# --------------------------------------------------------------------------
# Integration group selection
#
# The load-bearing test here is the coverage one: if a new integration suite
# is added and not placed in a group, it would silently stop running on every
# narrow backend PR. That must fail loudly instead.
# --------------------------------------------------------------------------


def _collected_integration_files() -> set[str]:
    """Every file pytest collects under `-m integration`, asked of pytest itself.

    Deliberately not a glob over `*_integration.py`: the question is what CI
    actually runs, and a suite could be marked without matching a filename
    convention. An empty result is a FAILURE rather than a skip — this test
    exists to notice a suite dropping out, so "I collected nothing" must not
    be the quiet path.
    """
    import shutil
    import subprocess

    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover - developer machine only
        pytest.skip("uv unavailable")

    proc = subprocess.run(  # noqa: S603
        [uv, "run", "pytest", "-m", "integration", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    files = {
        line.split("::", 1)[0].rsplit("/", 1)[-1]
        for line in proc.stdout.splitlines()
        if "::" in line and "tests/" in line
    }
    assert files, (
        "pytest collected no integration tests, so this guard proves nothing. "
        f"rc={proc.returncode}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    return files


def test_every_integration_suite_belongs_to_exactly_one_group() -> None:
    collected = _collected_integration_files()
    mapped: dict[str, list[str]] = {}
    for group, files in ci_impact.INTEGRATION_GROUPS.items():
        for path in files:
            mapped.setdefault(path.rsplit("/", 1)[-1], []).append(group)

    unmapped = sorted(collected - set(mapped))
    assert not unmapped, (
        "these integration suites are in no group, so a narrow backend PR "
        f"would silently skip them: {unmapped}"
    )

    duplicated = {name: groups for name, groups in mapped.items() if len(groups) > 1}
    assert not duplicated, f"suites mapped to more than one group: {duplicated}"

    stale = sorted(set(mapped) - collected)
    assert not stale, f"groups reference files pytest does not collect: {stale}"


def test_narrow_backend_change_selects_only_its_group() -> None:
    result = classify(["apps/api/src/drake_api/telemetry/query.py"])
    assert result["integration_is_narrow"] is True
    assert result["integration_groups"] == "telemetry"
    selection = str(result["integration_selection"]).split()
    assert selection, "a narrow change must still run its own integration suites"
    assert all(p.startswith("apps/api/tests/") for p in selection)
    assert any("telemetry" in p for p in selection)
    assert not any("github" in p for p in selection)


def test_catalog_change_selects_catalog_and_cluster_groups() -> None:
    result = classify(["apps/api/src/drake_api/catalog/router_clusters.py"])
    assert set(str(result["integration_groups"]).split()) == {
        "projects_catalog",
        "clusters_inventory",
    }


@pytest.mark.parametrize(
    "path",
    [
        "apps/api/src/drake_api/db.py",
        "apps/api/src/drake_api/main.py",
        "apps/api/src/drake_api/correlation.py",
        "apps/api/src/drake_api/some_new_module/thing.py",
    ],
)
def test_unmapped_backend_path_runs_every_integration_suite(path: str) -> None:
    # A module nobody mapped is a module nobody reasoned about.
    result = classify([path])
    assert result["integration_is_narrow"] is False
    assert result["integration_selection"] == ""


def test_full_suite_never_narrows_integration() -> None:
    for path in ["uv.lock", ".github/workflows/ci.yml", "apps/api/src/drake_api/settings.py"]:
        result = classify([path])
        assert result["full_suite"] is True
        assert result["integration_selection"] == "", path


def test_editing_one_integration_suite_selects_only_its_group() -> None:
    result = classify(["apps/api/tests/test_rbac_integration.py"])
    assert result["integration_is_narrow"] is True
    assert result["integration_groups"] == "auth_rbac"


def test_docs_change_selects_no_integration_at_all() -> None:
    result = classify(["README.md"])
    assert result["run_integration"] is False
    assert result["integration_selection"] == ""
    assert result["integration_groups"] == ""


def test_mixed_backend_paths_union_their_groups() -> None:
    result = classify(
        [
            "apps/api/src/drake_api/telemetry/query.py",
            "apps/api/src/drake_api/github_app/client.py",
        ]
    )
    assert set(str(result["integration_groups"]).split()) == {"telemetry", "integrations"}


def test_one_unmapped_path_defeats_an_otherwise_narrow_diff() -> None:
    result = classify(
        [
            "apps/api/src/drake_api/telemetry/query.py",
            "apps/api/src/drake_api/db.py",
        ]
    )
    assert result["integration_is_narrow"] is False
    assert result["integration_selection"] == ""
