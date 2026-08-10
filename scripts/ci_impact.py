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
