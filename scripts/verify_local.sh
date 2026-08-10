#!/usr/bin/env bash
# Run locally what CI would run for the current diff — before pushing.
#
# The point is parity. Both this and the workflow call scripts/ci_job.sh,
# and both ask scripts/ci_impact.py what is relevant, so "it passed
# locally" and "it will pass in CI" stop being two different claims.
#
# Usage:
#   scripts/verify_local.sh              # vs origin/main
#   scripts/verify_local.sh <base-ref>
#   scripts/verify_local.sh --list       # show the plan, run nothing
#
# What it cannot run: the e2e and k3d-runtime jobs. Those need a disposable
# cluster, service containers and a browser, and pretending otherwise would
# be worse than saying so — they are listed as unverified when selected.
set -euo pipefail

cd "$(dirname "$0")/.."

LIST_ONLY=""
BASE="origin/main"
for arg in "$@"; do
  case "$arg" in
    --list|-n) LIST_ONLY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) BASE="$arg" ;;
  esac
done

if ! git rev-parse --verify "$BASE" >/dev/null 2>&1; then
  echo "base ref '$BASE' not found; try: git fetch origin main" >&2
  exit 2
fi

# Committed diff UNION the working tree. Running this before committing is
# the whole point, and a plan computed only from committed history would
# quietly ignore the change you are about to push.
CHANGED="$(
  {
    git diff --name-only "$BASE...HEAD"
    git status --porcelain | sed 's/^...//' | sed 's/.* -> //'
  } | sed '/^$/d' | sort -u
)"

if [ -z "$CHANGED" ]; then
  # No diff is not "nothing to check" — it is "I cannot tell", and the
  # classifier treats an empty list as unknown and returns the full suite.
  IMPACT="$(python3 scripts/ci_impact.py --files)"
else
  # shellcheck disable=SC2086
  IMPACT="$(python3 scripts/ci_impact.py --files $CHANGED)"
fi
echo "$IMPACT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
skip = ("full_suite", "integration_is_narrow")
cats = [k for k, v in d.items()
        if isinstance(v, bool) and v and not k.startswith("run_") and k not in skip]
print("changed files:", d["changed_count"])
print("categories:   ", ", ".join(sorted(cats)) or "none")
print("full suite:   ", d["full_suite"])
if d["run_integration"]:
    n = len(str(d["integration_selection"]).split())
    groups = d["integration_groups"]
    print("integration:  ", "%d suites (%s)" % (n, groups) if n else "ALL suites")
if d["unmatched_paths"]:
    print("unrecognised: ", ", ".join(d["unmatched_paths"][:5]), "-> full suite")
'

get() { echo "$IMPACT" | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }

SELECTED=""
for job in contracts web python integration chart go; do
  key="run_$job"
  [ "$job" = "go" ] && key="run_go"
  if [ "$(get "$key")" = "True" ]; then
    name="$job"; [ "$job" = "go" ] && name="go-agent"
    SELECTED="$SELECTED $name"
  fi
done

UNVERIFIABLE=""
[ "$(get run_e2e)" = "True" ] && UNVERIFIABLE="$UNVERIFIABLE e2e"
[ "$(get run_k3d_runtime)" = "True" ] && UNVERIFIABLE="$UNVERIFIABLE k3d-runtime"

echo
echo "locally runnable:$([ -n "$SELECTED" ] && echo "$SELECTED" || echo " none")"
[ -n "$UNVERIFIABLE" ] && echo "CI only:         $UNVERIFIABLE (needs a cluster/browser; not run here)"
echo "always in CI:     secret scan, dependency scan"

if [ -n "$LIST_ONLY" ]; then
  echo
  echo "(--list: nothing was run)"
  exit 0
fi

DRAKE_INTEGRATION_SELECTION="$(get integration_selection)"
export DRAKE_INTEGRATION_SELECTION

FAILED=""
for job in $SELECTED; do
  if ! bash scripts/ci_job.sh "$job"; then
    FAILED="$FAILED $job"
    # Keep going: one failing job should not hide the state of the others,
    # and finding all of them now is the difference between one push and
    # three.
  fi
done

echo
if [ -n "$FAILED" ]; then
  echo "FAILED:$FAILED"
  exit 1
fi
echo "all locally runnable checks passed:${SELECTED:- none}"
[ -n "$UNVERIFIABLE" ] && echo "still unverified locally:$UNVERIFIABLE"
exit 0
