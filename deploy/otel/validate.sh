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
#     the host; RBAC anywhere in the chart is read-only (get/list/watch)
#   - nothing is scheduled by the chart onto a node it did not select
#   - OBI (eBPF traces), when enabled: only its own pod gets hostPID, its
#     capabilities are a fixed allowlist without SYS_ADMIN/NET_ADMIN, and
#     its config keeps every switch that would modify an application off
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

OBI_SETS=(
  --set obi.enabled=true
  --set obi.image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000
  --set-json 'obi.nodeSelector={"kubernetes.io/hostname":"policy-check"}'
  --set-json 'obi.namespaces=["policy-check-a","policy-check-b"]'
)

echo "[policy] OBI fail-closed renders"
for unset in obi.namespaces obi.nodeSelector obi.image.digest; do
  if render -f values-drake-prod.yaml "${OBI_SETS[@]}" --set "$unset=null" >/dev/null 2>&1; then
    echo "POLICY VIOLATION: OBI rendered without $unset" >&2
    exit 1
  fi
done

OUT="$(mktemp)"
OUT_OBI="$(mktemp)"
trap 'rm -f "$OUT" "$OUT_OBI"' EXIT
render -f values-drake-prod.yaml >"$OUT"
render -f values-drake-prod.yaml "${OBI_SETS[@]}" >"$OUT_OBI"

python3 - "$OUT" "$OUT_OBI" <<'PYEOF'
import sys

import yaml

failures = []


def fail(message):
    failures.append(message)


def load(path):
    docs = [d for d in yaml.safe_load_all(open(path)) if d]
    by_kind = {}
    for d in docs:
        by_kind.setdefault(d["kind"], []).append(d)
    return docs, by_kind


OBI_NAME = "drake-otel-obi"
OBI_CAPS = {"BPF", "PERFMON", "SYS_PTRACE", "NET_RAW", "CHECKPOINT_RESTORE", "DAC_READ_SEARCH"}
OBI_HOST_PATHS = {"/sys/kernel/tracing", "/sys/fs/cgroup"}


def check(docs, by_kind, label, expect_obi):
    [sc] = by_kind["StorageClass"]
    if sc.get("reclaimPolicy") != "Retain":
        fail(f"{label}: StorageClass must be Retain")
    if sc["metadata"].get("annotations", {}).get("helm.sh/resource-policy") != "keep":
        fail(f"{label}: StorageClass must be kept on uninstall")

    [sts] = by_kind["StatefulSet"]
    retention = sts["spec"].get("persistentVolumeClaimRetentionPolicy", {})
    if retention != {"whenDeleted": "Retain", "whenScaled": "Retain"}:
        fail(f"{label}: StatefulSet claims must be retained on delete and scale")

    workloads = by_kind["StatefulSet"] + by_kind["Deployment"] + by_kind["DaemonSet"]
    names = {w["metadata"]["name"] for w in workloads}
    if (OBI_NAME in names) != expect_obi:
        fail(f"{label}: OBI must render exactly when obi.enabled")

    for w in workloads:
        name = w["metadata"]["name"]
        pod = w["spec"]["template"]
        spec = pod["spec"]
        if pod["metadata"]["labels"].get("admission.datadoghq.com/enabled") != "false":
            fail(f"{label}: {name}: pod must opt out of Datadog injection")
        for c in spec.get("initContainers", []) + spec["containers"]:
            resources = c.get("resources", {})
            for part in ("requests", "limits"):
                for key in ("cpu", "memory"):
                    if key not in resources.get(part, {}):
                        fail(f"{label}: {name}/{c['name']}: missing {part}.{key}")
            sc_ = c.get("securityContext", {})
            if sc_.get("allowPrivilegeEscalation") is not False:
                fail(f"{label}: {name}/{c['name']}: privilege escalation must be off")
            if sc_.get("privileged"):
                fail(f"{label}: {name}/{c['name']}: privileged containers are not allowed")
            caps = sc_.get("capabilities", {})
            if caps.get("drop") != ["ALL"]:
                fail(f"{label}: {name}/{c['name']}: all capabilities must be dropped")
            added = set(caps.get("add", []))
            allowed = OBI_CAPS if name == OBI_NAME else set()
            if not added <= allowed:
                fail(f"{label}: {name}/{c['name']}: capabilities {sorted(added - allowed)} are not allowed")
            if sc_.get("readOnlyRootFilesystem") is not True:
                fail(f"{label}: {name}/{c['name']}: root filesystem must be read-only")
            for p in c.get("ports", []):
                if "hostPort" in p:
                    fail(f"{label}: {name}: host ports are not allowed")
        for flag in ("hostNetwork", "hostIPC"):
            if spec.get(flag):
                fail(f"{label}: {name}: {flag} is not allowed")
        if spec.get("hostPID") and name != OBI_NAME:
            fail(f"{label}: {name}: hostPID is reserved for OBI")

        host_paths = {v["name"]: v["hostPath"]["path"] for v in spec.get("volumes", []) if "hostPath" in v}
        if name == "drake-otel-agent":
            permitted = {"/var/log/pods"}
        elif name == OBI_NAME:
            permitted = OBI_HOST_PATHS
        else:
            permitted = set()
        if not set(host_paths.values()) <= permitted:
            fail(f"{label}: {name}: host paths {sorted(set(host_paths.values()) - permitted)} are not allowed")
        for c in spec["containers"]:
            for m in c.get("volumeMounts", []):
                if m["name"] in host_paths and m.get("readOnly") is not True:
                    fail(f"{label}: {name}: host mount {host_paths[m['name']]} must be read-only")

    for w in by_kind["StatefulSet"] + by_kind["Deployment"]:
        if not w["spec"]["template"]["spec"].get("nodeSelector"):
            fail(f"{label}: {w['metadata']['name']}: must be pinned with a nodeSelector")
    for w in by_kind["DaemonSet"]:
        if w["metadata"]["name"] == OBI_NAME and not w["spec"]["template"]["spec"].get("nodeSelector"):
            fail(f"{label}: OBI must be pinned with a nodeSelector")

    allowed_resources = {"pods", "namespaces", "replicasets", "services", "nodes"}
    for role in by_kind.get("ClusterRole", []):
        for rule in role["rules"]:
            if not set(rule["verbs"]) <= {"get", "list", "watch"}:
                fail(f"{label}: {role['metadata']['name']}: only get/list/watch are allowed")
            for resource in rule["resources"]:
                if resource not in allowed_resources:
                    fail(f"{label}: {role['metadata']['name']}: resource {resource} is not allowed")

    if "Secret" in by_kind:
        fail(f"{label}: the chart must not create Secrets")

    if expect_obi:
        [cm] = [c for c in by_kind["ConfigMap"] if c["metadata"]["name"] == OBI_NAME]
        cfg = yaml.safe_load(cm["data"]["obi.yaml"])
        ebpf = cfg.get("ebpf", {})
        if ebpf.get("context_propagation") != "disabled":
            fail(f"{label}: OBI context propagation must be disabled")
        if ebpf.get("track_request_headers") is not False:
            fail(f"{label}: OBI must not track request headers")
        if ebpf.get("log_enricher", {}).get("services") != []:
            fail(f"{label}: OBI log enrichment must be off")
        if cfg.get("nodejs", {}).get("enabled") is not False:
            fail(f"{label}: OBI Node.js injector must be disabled")
        if cfg.get("javaagent", {}).get("enabled") is not False:
            fail(f"{label}: OBI Java agent must be disabled")
        for key in ("otel_metrics_export", "prometheus_export"):
            if key in cfg:
                fail(f"{label}: OBI must export traces only ({key} set)")
        instrument = cfg.get("discovery", {}).get("instrument", [])
        if not instrument or any(
            set(rule) != {"k8s_namespace"} or "*" in str(rule["k8s_namespace"]) for rule in instrument
        ):
            fail(f"{label}: OBI discovery must be an explicit namespace allowlist")

    return len(docs)


off_docs, off_kinds = load(sys.argv[1])
on_docs, on_kinds = load(sys.argv[2])
n_off = check(off_docs, off_kinds, "default", expect_obi=False)
n_on = check(on_docs, on_kinds, "obi", expect_obi=True)

if failures:
    for f in failures:
        print("POLICY VIOLATION:", f, file=sys.stderr)
    sys.exit(1)
print(f"[policy] {n_off} rendered objects pass; with OBI enabled, {n_on} pass")
PYEOF
