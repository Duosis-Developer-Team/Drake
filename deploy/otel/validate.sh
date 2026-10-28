#!/usr/bin/env bash
# Render + policy-check the Drake OpenTelemetry chart. CI-safe: no cluster
# access, no install. Exits non-zero on any violation. This is the
# executable form of ADR-0029's safety contract:
#   - every required decision (cluster name, namespaces, node, secret,
#     digests) refuses to render when missing
#   - the store's volume is Retain, and its class is kept on uninstall
#   - every pod opts out of Datadog injection, on Drake's own pods only
#   - every container has requests AND limits
#   - the agent mounts the node's pod logs read-only and nothing else from
#     the host, and its RBAC is exactly get/list/watch on pods, namespaces
#     and replicasets
#   - nothing is scheduled by the chart onto a node it did not select
set -euo pipefail

cd "$(dirname "$0")"

render() { helm template drake-otel . --namespace drake-otel "$@"; }

echo "[helm] lint"
helm lint . -f values-drake-prod.yaml >/dev/null

echo "[policy] fail-closed renders"
for unset in clusterName agent.namespaces clickhouse.nodeSelector clickhouse.existingSecret \
  images.collector.digest images.clickhouse.digest; do
  if render -f values-drake-prod.yaml --set "$unset=null" >/dev/null 2>&1; then
    echo "POLICY VIOLATION: render succeeded without $unset" >&2
    exit 1
  fi
done

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT
render -f values-drake-prod.yaml >"$OUT"

python3 - "$OUT" <<'PYEOF'
import sys

import yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
by_kind = {}
for d in docs:
    by_kind.setdefault(d["kind"], []).append(d)

failures = []


def fail(message):
    failures.append(message)


[sc] = by_kind["StorageClass"]
if sc.get("reclaimPolicy") != "Retain":
    fail("StorageClass must be Retain")
if sc["metadata"].get("annotations", {}).get("helm.sh/resource-policy") != "keep":
    fail("StorageClass must be kept on uninstall")

[sts] = by_kind["StatefulSet"]
retention = sts["spec"].get("persistentVolumeClaimRetentionPolicy", {})
if retention != {"whenDeleted": "Retain", "whenScaled": "Retain"}:
    fail("StatefulSet claims must be retained on delete and scale")

workloads = by_kind["StatefulSet"] + by_kind["Deployment"] + by_kind["DaemonSet"]
for w in workloads:
    name = w["metadata"]["name"]
    pod = w["spec"]["template"]
    if pod["metadata"]["labels"].get("admission.datadoghq.com/enabled") != "false":
        fail(f"{name}: pod must opt out of Datadog injection")
    for c in pod["spec"]["containers"]:
        resources = c.get("resources", {})
        for part in ("requests", "limits"):
            for key in ("cpu", "memory"):
                if key not in resources.get(part, {}):
                    fail(f"{name}/{c['name']}: missing {part}.{key}")
        sc_ = c.get("securityContext", {})
        if sc_.get("allowPrivilegeEscalation") is not False:
            fail(f"{name}/{c['name']}: privilege escalation must be off")
        if sc_.get("capabilities", {}).get("drop") != ["ALL"]:
            fail(f"{name}/{c['name']}: all capabilities must be dropped")
        if sc_.get("readOnlyRootFilesystem") is not True:
            fail(f"{name}/{c['name']}: root filesystem must be read-only")
    spec = pod["spec"]
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(flag):
            fail(f"{name}: {flag} is not allowed")

for w in by_kind["StatefulSet"] + by_kind["Deployment"]:
    if not w["spec"]["template"]["spec"].get("nodeSelector"):
        fail(f"{w['metadata']['name']}: must be pinned with a nodeSelector")

[ds] = by_kind["DaemonSet"]
host_paths = [v for v in ds["spec"]["template"]["spec"]["volumes"] if "hostPath" in v]
if [v["hostPath"]["path"] for v in host_paths] != ["/var/log/pods"]:
    fail("agent may mount only /var/log/pods from the host")
for c in ds["spec"]["template"]["spec"]["containers"]:
    for m in c.get("volumeMounts", []):
        if m["name"] in {v["name"] for v in host_paths} and m.get("readOnly") is not True:
            fail("agent host mount must be read-only")
    if any("hostPort" in p for p in c.get("ports", [])):
        fail("agent must not publish a host port in the logs phase")

for w in workloads:
    for c in w["spec"]["template"]["spec"]["containers"]:
        for p in c.get("ports", []):
            if "hostPort" in p:
                fail(f"{w['metadata']['name']}: host ports are not allowed")

for role in by_kind.get("ClusterRole", []):
    for rule in role["rules"]:
        if sorted(rule["verbs"]) != ["get", "list", "watch"]:
            fail(f"{role['metadata']['name']}: verbs must be exactly get/list/watch")
        for resource in rule["resources"]:
            if resource not in {"pods", "namespaces", "replicasets"}:
                fail(f"{role['metadata']['name']}: resource {resource} is not allowed")

if "Secret" in by_kind:
    fail("the chart must not create Secrets")

if failures:
    for f in failures:
        print("POLICY VIOLATION:", f, file=sys.stderr)
    sys.exit(1)
print(f"[policy] {len(docs)} rendered objects pass")
PYEOF
