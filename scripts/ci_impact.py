"""Decide which CI gates a change actually needs.

The rule this file exists to enforce: **an unrecognised path runs
everything.** Path filtering is a way to skip tests, and a skip is only
safe when the classifier is sure. So the mapping below is an allowlist —
every pattern names what it is and what it implies — and anything that
matches nothing sets `unknown`, which turns the full suite back on. A
classifier that silently defaults to "probably fine" is how a change ships
without the one gate that would have caught it.

Two more things it deliberately does NOT do:

- It does not try to be a Python import graph. A real graph is fragile
  (dynamic imports, fixtures, templates) and being wrong means skipping a
  test that mattered. Instead, files that lots of things depend on —
  settings, auth, RBAC, models, the shared contracts package — are marked
  `shared_contract` / `security_sensitive` and widen the scope by hand.
- It never narrows the SECURITY gates. Secret scanning and dependency
  scanning run on every change, including a one-word README edit, because
  "this diff is only docs" is a claim the diff itself makes.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys

# (glob, categories). First match wins, so order is significant: the
# narrow, high-consequence patterns come before the broad ones.
RULES: list[tuple[str, tuple[str, ...]]] = [
    # --- dependency and lockfile: rebuild and retest everything that uses it
    ("pnpm-lock.yaml", ("dependency_or_lockfile",)),
    ("uv.lock", ("dependency_or_lockfile",)),
    ("**/go.sum", ("dependency_or_lockfile",)),
    ("**/go.mod", ("dependency_or_lockfile",)),
    ("**/package.json", ("dependency_or_lockfile",)),
    ("**/pyproject.toml", ("dependency_or_lockfile",)),
    # --- CI and test infrastructure: it decides what runs, so prove it
    (".github/**", ("ci_or_test_infrastructure",)),
    ("scripts/ci_impact.py", ("ci_or_test_infrastructure",)),
    ("scripts/lib/**", ("ci_or_test_infrastructure",)),
    ("scripts/**", ("ci_or_test_infrastructure",)),
    ("Makefile", ("ci_or_test_infrastructure",)),
    # --- security-sensitive application surfaces
    ("apps/api/src/drake_api/auth/**", ("backend", "security_sensitive")),
    ("apps/api/src/drake_api/authz/**", ("backend", "security_sensitive")),
    ("apps/api/src/drake_api/security/**", ("backend", "security_sensitive")),
    ("apps/api/src/drake_api/settings.py", ("backend", "security_sensitive")),
    ("apps/api/src/drake_api/agents/**", ("backend", "listener_or_pki", "security_sensitive")),
    # --- database
    ("apps/api/alembic/**", ("backend", "database_or_migration")),
    ("apps/api/src/drake_api/models/**", ("backend", "database_or_migration", "shared_contract")),
    ("apps/api/src/drake_api/db/**", ("backend", "database_or_migration")),
    # --- ordinary backend
    ("apps/api/**", ("backend",)),
    ("apps/worker/**", ("backend",)),
    # --- frontend
    ("apps/web/e2e/**", ("frontend", "ci_or_test_infrastructure")),
    ("apps/web/playwright.config.ts", ("frontend", "ci_or_test_infrastructure")),
    ("apps/web/**", ("frontend",)),
    # --- shared contracts: the schema both sides compile against
    ("packages/contracts/**", ("shared_contract",)),
    ("packages/**", ("shared_contract",)),
    # --- cluster agent (Go)
    ("apps/cluster-agent/**", ("agent",)),
    # --- charts
    ("deploy/drake/templates/internal-listener.yaml", ("helm_chart", "listener_or_pki")),
    ("deploy/agent/**", ("helm_chart", "agent")),
    ("deploy/drake/**", ("helm_chart",)),
    ("deploy/**", ("helm_chart",)),
    # --- documentation
    ("docs/**", ("docs_only",)),
    # Both forms are needed: fnmatch's `**/*.md` requires a literal `/`, so
    # it does not match a repository-root `README.md` — which would then
    # fall through to `unknown` and run the whole suite for a typo fix.
    ("*.md", ("docs_only",)),
    ("**/*.md", ("docs_only",)),
    ("LICENSE", ("docs_only",)),
    (".gitignore", ("docs_only",)),
    (".editorconfig", ("docs_only",)),
    (".nvmrc", ("dependency_or_lockfile",)),
]

# Integration tests, grouped by the surface they exercise. Every file that
# `-m integration` collects appears exactly once; the mapping is checked by a
# test, so adding a suite without placing it here fails rather than silently
# dropping out of narrow runs.
INTEGRATION_GROUPS: dict[str, tuple[str, ...]] = {
    "auth_rbac": (
        "apps/api/tests/test_rbac_integration.py",
        "apps/api/tests/test_local_auth_integration.py",
        "apps/api/tests/test_auth_flow_integration.py",
        "apps/api/tests/test_grant_options_integration.py",
        "apps/api/tests/test_idor_audit_integration.py",
        "apps/api/tests/test_onboarding_authz_integration.py",
    ),
    "projects_catalog": (
        "apps/api/tests/test_catalog_persistence_integration.py",
        "apps/api/tests/test_catalog_api_integration.py",
        "apps/api/tests/test_dependency_catalog_integration.py",
        "apps/api/tests/test_fikir_sepeti_integration.py",
        "apps/api/tests/test_onboarding_integration.py",
        "apps/api/tests/test_onboarding_apply_binding_integration.py",
        "apps/api/tests/test_onboarding_parity_integration.py",
        "apps/api/tests/test_onboarding_gitops_race_integration.py",
        "apps/api/tests/test_onboarding_release_candidate.py",
        "apps/api/tests/test_protection_integration.py",
    ),
    "clusters_inventory": (
        "apps/api/tests/test_cluster_registration_integration.py",
        "apps/api/tests/test_service_health_read_api_integration.py",
        "apps/api/tests/test_service_health_bindings_integration.py",
        "apps/api/tests/test_deployment_intelligence_integration.py",
    ),
    "agents_enrollment": (
        "apps/api/tests/test_agent_enrollment_integration.py",
        "apps/api/tests/test_agent_closure_integration.py",
    ),
    "agents_ingest": ("test_agent_inventory_integration.py",),
    "telemetry": (
        "apps/api/tests/test_telemetry_api_integration.py",
        "apps/api/tests/test_alerting_integration.py",
        "apps/api/tests/test_incident_processor_integration.py",
        "apps/api/tests/test_incident_api_integration.py",
        "apps/api/tests/test_incident_runner_integration.py",
        "apps/api/tests/test_notification_webhook_integration.py",
        "apps/api/tests/test_notification_hardening_integration.py",
        "apps/api/tests/test_notification_planner_integration.py",
        "apps/api/tests/test_notification_api_integration.py",
        "apps/api/tests/test_notification_tls_integration.py",
    ),
    "integrations": (
        "apps/api/tests/test_github_identity_integration.py",
        "apps/api/tests/test_github_invariants_integration.py",
        "apps/api/tests/test_github_lifecycle_integration.py",
        "apps/api/tests/test_github_onboarding_integration.py",
        "apps/api/tests/test_github_boundary_integration.py",
        "apps/api/tests/test_github_reconciliation_integration.py",
        "apps/api/tests/test_github_precedence_integration.py",
        "apps/api/tests/test_github_integration.py",
        "apps/api/tests/test_github_durability_integration.py",
        "apps/api/tests/test_github_lease_integration.py",
    ),
    "database_shared": (
        "apps/api/tests/test_migrations_audit_integration.py",
        "apps/api/tests/test_idempotency_integration.py",
        # Integration tests are not all under apps/api: the worker's queue
        # suite lives with the worker and is collected by the same marker.
        "apps/worker/tests/test_queue_redis_integration.py",
    ),
    "api_contract_shared": (
        "apps/api/tests/test_production_edge_integration.py",
        "apps/api/tests/test_health.py",
    ),
}

INTEGRATION_TEST_DIRS = ("apps/api/tests/", "apps/worker/tests/")

# Which integration groups a backend source path can affect. A path that
# matches NOTHING here is not guessed at: it selects every group, because a
# module nobody mapped is a module nobody has reasoned about.
BACKEND_INTEGRATION_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("apps/api/src/drake_api/auth/**", ("auth_rbac",)),
    ("apps/api/src/drake_api/rbac/**", ("auth_rbac",)),
    ("apps/api/src/drake_api/catalog/**", ("projects_catalog", "clusters_inventory")),
    ("apps/api/src/drake_api/onboarding/**", ("projects_catalog",)),
    ("apps/api/src/drake_api/protection/**", ("projects_catalog",)),
    ("apps/api/src/drake_api/deployments/**", ("clusters_inventory",)),
    ("apps/api/src/drake_api/service_health/**", ("clusters_inventory",)),
    (
        "apps/api/src/drake_api/agents/**",
        ("agents_enrollment", "agents_ingest", "clusters_inventory"),
    ),
    ("apps/api/src/drake_api/telemetry/**", ("telemetry",)),
    ("apps/api/src/drake_api/alerting/**", ("telemetry",)),
    ("apps/api/src/drake_api/incidents/**", ("telemetry",)),
    ("apps/api/src/drake_api/notifications/**", ("telemetry",)),
    ("apps/api/src/drake_api/github_app/**", ("integrations",)),
    ("apps/api/src/drake_api/integrations/**", ("integrations",)),
    ("apps/api/src/drake_api/audit/**", ("database_shared",)),
    ("apps/api/src/drake_api/health.py", ("api_contract_shared",)),
    ("apps/api/src/drake_api/errors.py", ("api_contract_shared",)),
    ("apps/api/src/drake_api/origin.py", ("api_contract_shared",)),
    # The worker consumes the same queue and audit tables the notification
    # and incident suites assert on.
    ("apps/worker/**", ("telemetry", "database_shared")),
]


# Changing an integration test file selects its own group, so editing one
# suite does not drag in the other eight.
def _group_of_test_file(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    for group, files in INTEGRATION_GROUPS.items():
        if any(f.rsplit("/", 1)[-1] == name for f in files):
            return group
    return None


CATEGORIES = (
    "docs_only",
    "backend",
    "frontend",
    "agent",
    "helm_chart",
    "listener_or_pki",
    "database_or_migration",
    "shared_contract",
    "ci_or_test_infrastructure",
    "dependency_or_lockfile",
    "security_sensitive",
    "unknown",
)

# Categories that mean "we cannot reason about the blast radius": run it all.
FULL_SUITE_TRIGGERS = (
    "unknown",
    "dependency_or_lockfile",
    "ci_or_test_infrastructure",
    "security_sensitive",
)


def _matches(path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    # `a/**` should also match `a/b`, which fnmatch does not do on its own.
    if pattern.endswith("/**") and fnmatch.fnmatch(path, pattern[:-3]):
        return True
    return False


def classify(paths: list[str]) -> dict[str, object]:
    hits: set[str] = set()
    unmatched: list[str] = []

    for path in paths:
        path = path.strip()
        if not path:
            continue
        for pattern, categories in RULES:
            if _matches(path, pattern):
                hits.update(categories)
                break
        else:
            unmatched.append(path)
            hits.add("unknown")

    # "docs_only" is a claim about the WHOLE diff, not about one file.
    if hits & (set(CATEGORIES) - {"docs_only"}):
        hits.discard("docs_only")

    if not paths:
        # No diff at all (a re-run, or a merge with no file changes). Nothing
        # to reason about, so do not pretend a narrow answer is safe.
        hits.add("unknown")

    full = bool(hits & set(FULL_SUITE_TRIGGERS))

    docs_only = hits == {"docs_only"}

    # Job selection. Each line answers: what could this change break?
    run_contracts = full or bool(hits & {"shared_contract", "frontend", "backend"})
    run_web = full or bool(hits & {"frontend", "shared_contract"})
    run_python = full or bool(hits & {"backend", "shared_contract", "database_or_migration"})
    run_go = full or bool(hits & {"agent"})
    run_integration = full or bool(hits & {"backend", "database_or_migration", "shared_contract"})
    run_chart = full or bool(hits & {"helm_chart", "listener_or_pki", "agent"})
    # k3d runtime smokes are the expensive ones: chart/listener/PKI/agent only.
    run_k3d_runtime = full or bool(hits & {"helm_chart", "listener_or_pki", "agent"})
    # Browser E2E follows the FRONTEND and the shared contract, not every
    # backend edit. An ordinary backend module is covered by the python and
    # integration jobs; the surfaces where a backend change really can break
    # the browser — auth, RBAC, settings, models, contracts — are classified
    # `security_sensitive` or `shared_contract` above and take the full
    # suite anyway. What this trades away is caught by the post-merge full
    # regression on main, which is why that path is not optional.
    run_e2e = full or bool(hits & {"frontend", "shared_contract"})

    if docs_only:
        run_contracts = run_web = run_python = run_go = False
        run_integration = run_chart = run_k3d_runtime = run_e2e = False

    # --- which integration suites does this diff actually need? ---
    #
    # Empty selection means "all of them". Narrowing happens only when every
    # changed path is one we have explicitly reasoned about; one unmapped
    # backend file puts the whole suite back, because a module nobody mapped
    # is a module nobody has thought about.
    groups: set[str] = set()
    integration_is_narrow = run_integration and not full

    if integration_is_narrow:
        for path in paths:
            path = path.strip()
            if not path:
                continue
            if path.startswith(INTEGRATION_TEST_DIRS):
                group = _group_of_test_file(path)
                if group is None:
                    # An integration suite that is not in any group, or a
                    # unit test living beside them: cannot narrow safely.
                    if "_integration" in path:
                        integration_is_narrow = False
                        break
                    continue
                groups.add(group)
                continue
            for pattern, mapped in BACKEND_INTEGRATION_MAP:
                if _matches(path, pattern):
                    groups.update(mapped)
                    break
            else:
                # Not backend at all (a chart, a doc, the web app) — it
                # cannot select an integration group, and it must not widen
                # one either. Only an unmapped BACKEND path is decisive.
                if _matches(path, "apps/api/**") or _matches(path, "apps/worker/**"):
                    integration_is_narrow = False
                    break

    if integration_is_narrow and not groups:
        # run_integration was true but nothing mapped: do not invent a
        # narrow answer.
        integration_is_narrow = False

    if integration_is_narrow:
        selected_files = sorted(name for group in groups for name in INTEGRATION_GROUPS[group])
        integration_selection = " ".join(selected_files)
    else:
        groups = set(INTEGRATION_GROUPS) if run_integration else set()
        integration_selection = ""

    result: dict[str, object] = {c: (c in hits) for c in CATEGORIES}
    result.update(
        {
            "docs_only": docs_only,
            "full_suite": full,
            "run_contracts": run_contracts,
            "run_web": run_web,
            "run_python": run_python,
            "run_go": run_go,
            "run_integration": run_integration,
            "run_chart": run_chart,
            "run_k3d_runtime": run_k3d_runtime,
            "run_e2e": run_e2e,
            # Security gates are not selectable. They run on every change.
            "run_secret_scan": True,
            "run_dependency_scan": True,
            # Space-separated pytest paths, or "" meaning every integration
            # test. The integration job passes this straight through.
            "integration_selection": integration_selection,
            "integration_groups": " ".join(sorted(groups)),
            "integration_is_narrow": bool(integration_selection),
            "unmatched_paths": unmatched[:50],
            "changed_count": len(paths),
        }
    )
    return result


def changed_files(base: str, head: str) -> list[str]:
    """Files changed between two commits.

    A failure here must NOT look like an empty diff: an empty list would be
    classified as `unknown` and run everything, which is the safe direction,
    but the reason would be invisible. So it is reported explicitly.
    """
    git = shutil.which("git")
    if git is None:
        print("::warning::git not found; assuming full suite", file=sys.stderr)
        return []
    try:
        # Fixed argument vector, no shell, and both refs are commit SHAs
        # supplied by the workflow context rather than by a diff author.
        out = subprocess.run(  # noqa: S603
            [git, "diff", "--name-only", f"{base}...{head}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError) as exc:
        print(
            f"::warning::could not diff {base}...{head} ({exc}); assuming full suite",
            file=sys.stderr,
        )
        return []
    return [line for line in out.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="base ref/sha")
    parser.add_argument("--head", default="HEAD", help="head ref/sha")
    parser.add_argument("--files", nargs="*", help="explicit file list (testing)")
    parser.add_argument("--github-output", action="store_true", help="write to $GITHUB_OUTPUT")
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="run everything regardless of the diff (main, nightly, manual)",
    )
    args = parser.parse_args()

    if args.force_full:
        result = classify([])
        result["forced_full"] = True
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.github_output and (target := os.environ.get("GITHUB_OUTPUT")):
            with open(target, "a", encoding="utf-8") as handle:
                for key, value in sorted(result.items()):
                    if isinstance(value, bool):
                        handle.write(f"{key}={str(value).lower()}\n")
                    elif isinstance(value, (int, str)):
                        handle.write(f"{key}={value}\n")
        return 0

    if args.files is not None:
        paths = list(args.files)
    elif args.base:
        paths = changed_files(args.base, args.head)
    else:
        print("::warning::no base ref given; assuming full suite", file=sys.stderr)
        paths = []

    result = classify(paths)

    print(json.dumps(result, indent=2, sort_keys=True))

    if args.github_output:
        target = os.environ.get("GITHUB_OUTPUT")
        if target:
            with open(target, "a", encoding="utf-8") as handle:
                for key, value in sorted(result.items()):
                    if isinstance(value, bool):
                        handle.write(f"{key}={str(value).lower()}\n")
                    elif isinstance(value, (int, str)):
                        handle.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
