#!/usr/bin/env bash
# Render + policy-check the dev observability package. CI-safe: no cluster
# access, no install. Exit non-zero on any policy violation.
set -euo pipefail

cd "$(dirname "$0")"

echo "[helm] dependency build (pinned via Chart.lock)"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm dependency build . >/dev/null

echo "[helm] lint"
helm lint . --values values.yaml >/dev/null

echo "[helm] template"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
helm template drake-dev-observability . --values values.yaml --namespace drake-observability > "$RENDERED"

fail() {
  echo "POLICY VIOLATION: $1" >&2
  exit 1
}

echo "[policy] no public exposure"
grep -q "type: LoadBalancer" "$RENDERED" && fail "LoadBalancer service in rendered output"
grep -q "type: NodePort" "$RENDERED" && fail "NodePort service in rendered output"
grep -q "^kind: Ingress" "$RENDERED" && fail "Ingress in rendered output"

echo "[policy] no credential-shaped secret content"
# The chart legitimately renders config Secrets (e.g. Alertmanager's route
# config). What must never appear is credential material.
for pattern in "BEGIN PRIVATE KEY" "BEGIN RSA" "password:" "bearer_token:" "api_key" "access_key" "client_secret"; do
  if grep -qi "$pattern" "$RENDERED"; then
    fail "credential-shaped content '$pattern' in rendered output"
  fi
done

echo "[policy] no wildcard-everything cluster permission"
python3 - "$RENDERED" <<'PYEOF'
import sys

import yaml

with open(sys.argv[1]) as fh:
    docs = [d for d in yaml.safe_load_all(fh) if d]
for doc in docs:
    if doc.get("kind") not in ("ClusterRole", "Role"):
        continue
    for rule in doc.get("rules") or []:
        if (
            "*" in (rule.get("apiGroups") or [])
        and "*" in (rule.get("resources") or [])
        and "*" in (rule.get("verbs") or [])
        ):
            raise SystemExit(
                f"POLICY VIOLATION: wildcard-everything rule in {doc['metadata']['name']}"
            )
print(f"[policy] {len(docs)} rendered documents checked")
PYEOF

echo "[policy] grafana stays disabled"
grep -qi "kind: Deployment" "$RENDERED" || fail "nothing rendered?"
if grep -q "app.kubernetes.io/name: grafana" "$RENDERED"; then
  fail "grafana resources rendered despite grafana.enabled=false"
fi

echo "helm validation passed"
