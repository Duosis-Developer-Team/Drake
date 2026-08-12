#!/usr/bin/env bash
# The production rollout, made safe to run when you are not the only one
# running it.
#
#   bash deploy/drake/rollout.sh                 # roll out to what values say
#   DRY_RUN=1 bash deploy/drake/rollout.sh       # say what would change
#
# Two sessions have been working this repository in parallel, and the
# collisions were not hypothetical: `helm upgrade` refused with "another
# operation (install/upgrade/rollback) is in progress" while the other
# session's migration hook was mid-flight. `helm list` returned nothing at
# all in that window, which reads exactly like "the release is gone".
#
# The dangerous move at that moment is the obvious one: --force, or deleting
# the pending release secret, while a migration is running against the
# production database. So this script refuses instead, and says who it is
# waiting for.
#
# It is also IDEMPOTENT on purpose. A second session that rolls out the same
# digests should be told there is nothing to do, not race to apply an
# identical release and burn a revision.
set -euo pipefail

cd "$(dirname "$0")/../.."

NAMESPACE="${DRAKE_NAMESPACE:-drake-prod}"
RELEASE="${DRAKE_RELEASE:-drake}"
VALUES="${DRAKE_VALUES:-deploy/drake/values-drake-prod.yaml}"
TIMEOUT="${DRAKE_ROLLOUT_TIMEOUT:-10m}"

fail() { echo "[rollout] $*" >&2; exit 1; }

[ -f "$VALUES" ] || fail "values file not found: $VALUES"

# --- 1. is someone else mid-rollout? ---------------------------------------
#
# `helm status` reports the pending-* states Helm sets for the duration of an
# operation. Reading them is how this avoids interrupting a migration hook
# that is already talking to the database.
status="$(helm status "$RELEASE" -n "$NAMESPACE" -o json 2>/dev/null \
  | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin)["info"]["status"])
except Exception:
    print("absent")' || echo "absent")"

case "$status" in
  pending-install|pending-upgrade|pending-rollback|uninstalling)
    echo "[rollout] release '$RELEASE' is $status — another operation is in flight." >&2
    echo "[rollout] Recent revisions:" >&2
    helm history "$RELEASE" -n "$NAMESPACE" 2>/dev/null | tail -3 >&2 || true
    echo "[rollout] Pods currently rolling:" >&2
    kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null \
      | grep -vE "Running|Completed" | awk '{print "  " $1, $3}' >&2 || true
    fail "refusing to run concurrently. Wait for it to finish, then re-run.
       Do NOT use --force or delete the release secret while a migration hook
       is running: that is how a half-applied schema happens."
    ;;
  failed)
    echo "[rollout] release '$RELEASE' is in a FAILED state." >&2
    helm history "$RELEASE" -n "$NAMESPACE" 2>/dev/null | tail -3 >&2 || true
    fail "a human decides whether to roll forward or back; this script will not guess."
    ;;
esac

# --- 2. is there anything to do? -------------------------------------------
#
# Compare what the values file asks for against what the cluster is running,
# per workload. Rendering is cheap and honest: it asks the chart, rather than
# re-implementing which value maps to which image.
RENDERED_FILE="$(mktemp)"
trap 'rm -f "$RENDERED_FILE"' EXIT
helm template "$RELEASE" deploy/drake -n "$NAMESPACE" -f "$VALUES" > "$RENDERED_FILE"
changes="$(python3 - "$NAMESPACE" "$RENDERED_FILE" <<'PYEOF'
import json, subprocess, sys

import yaml

namespace, rendered_path = sys.argv[1], sys.argv[2]
wanted = {}
# From a FILE, not stdin: this program itself arrives on stdin, so anything
# pushed there is read as source code and never reaches this line.
for doc in yaml.safe_load_all(open(rendered_path).read()):
    if not doc or doc.get("kind") != "Deployment":
        continue
    containers = doc["spec"]["template"]["spec"]["containers"]
    wanted[doc["metadata"]["name"]] = containers[0]["image"]

live = {}
result = subprocess.run(
    ["kubectl", "get", "deploy", "-n", namespace, "-o", "json"],
    capture_output=True, text=True,
)
if result.returncode == 0:
    for item in json.loads(result.stdout).get("items", []):
        live[item["metadata"]["name"]] = (
            item["spec"]["template"]["spec"]["containers"][0]["image"]
        )

for name, image in sorted(wanted.items()):
    current = live.get(name)
    if current != image:
        short = image.rsplit("@", 1)[-1][:19]
        was = (current or "absent").rsplit("@", 1)[-1][:19]
        print(f"  {name}: {was} -> {short}")
PYEOF
)"

if [ -z "$changes" ]; then
  echo "[rollout] every workload already runs the pinned image; nothing to apply."
  echo "[rollout] (chart-only changes still need a run — pass FORCE_APPLY=1 to do that.)"
  [ "${FORCE_APPLY:-0}" = "1" ] || exit 0
else
  echo "[rollout] image changes:"
  echo "$changes"
fi

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "[rollout] dry run only"
  exit 0
fi

# --- 3. apply -------------------------------------------------------------
helm upgrade --install "$RELEASE" deploy/drake \
  --namespace "$NAMESPACE" -f "$VALUES" \
  --atomic --wait --timeout "$TIMEOUT"

helm history "$RELEASE" -n "$NAMESPACE" | tail -2
kubectl get pods -n "$NAMESPACE" --no-headers | awk '{print "  " $1, $2, $3, "restarts=" $4}'
