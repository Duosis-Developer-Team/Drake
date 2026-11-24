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
#     the host; the node-metrics DaemonSet mounts the host root read-only,
#     runs as non-root and has no API token; RBAC anywhere in the chart is
#     read-only (get/list/watch), and never nodes/proxy
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
# An SDK workload named by half (no deployment) must not render: it would
# either exclude nothing or exclude a whole namespace.
if render -f values-drake-prod.yaml "${OBI_SETS[@]}" \
  --set-json 'obi.sdkWorkloads=[{"namespace":"policy-check-a"}]' >/dev/null 2>&1; then
  echo "POLICY VIOLATION: OBI rendered an SDK workload without its deployment" >&2
  exit 1
fi

OUT="$(mktemp)"
OUT_OBI="$(mktemp)"
OUT_METRICS="$(mktemp)"
trap 'rm -f "$OUT" "$OUT_OBI" "$OUT_METRICS"' EXIT
# Both directions are checked by forcing each state, so the invariant holds
# no matter what the committed values happen to say today: OBI renders when
# obi.enabled, and only then.
render -f values-drake-prod.yaml --set obi.enabled=false --set metrics.enabled=false >"$OUT"
render -f values-drake-prod.yaml "${OBI_SETS[@]}" --set metrics.enabled=false >"$OUT_OBI"
# ADR-0038: the OpenTelemetry metrics collector, forced on.
render -f values-drake-prod.yaml --set obi.enabled=false --set metrics.enabled=true   --set metrics.node.enabled=true --set metrics.cadvisor.enabled=true >"$OUT_METRICS"

echo "[policy] metrics fail-closed renders"
if render -f values-drake-prod.yaml --set metrics.enabled=true --set-json 'metrics.names=[]' >/dev/null 2>&1; then
  echo "POLICY VIOLATION: metrics rendered without a metric allowlist" >&2
  exit 1
fi
if render -f values-drake-prod.yaml --set metrics.enabled=true --set metrics.node.enabled=true   --set-json 'metrics.node.names=[]' >/dev/null 2>&1; then
  echo "POLICY VIOLATION: node metrics rendered without a metric allowlist" >&2
  exit 1
fi
# Node metrics and cAdvisor need the metrics collector; alone they render nothing.
if render -f values-drake-prod.yaml --set metrics.enabled=false --set metrics.node.enabled=true   --set metrics.cadvisor.enabled=true | grep -qE "drake-otel-node|nodes/metrics"; then
  echo "POLICY VIOLATION: node metrics or cAdvisor rendered with metrics off" >&2
  exit 1
fi

python3 - "$OUT" "$OUT_OBI" "$OUT_METRICS" <<'PYEOF'
import json
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
OBI_CAPS = {"BPF", "PERFMON", "SYS_PTRACE", "NET_RAW", "CHECKPOINT_RESTORE", "DAC_READ_SEARCH", "IPC_LOCK"}
OBI_HOST_PATHS = {"/sys/kernel/tracing", "/sys/fs/cgroup"}


METRICS_NAME = "drake-otel-metrics"
# What k8s_cluster, k8s_observer and kubeletstats read (ADR-0038); still
# get/list/watch only, and never nodes/proxy, secrets or configmaps.
METRICS_RESOURCES = {
    "namespaces", "namespaces/status", "nodes", "nodes/spec", "nodes/stats",
    "pods", "pods/status", "replicationcontrollers", "replicationcontrollers/status",
    "resourcequotas", "services", "daemonsets", "deployments", "replicasets",
    "statefulsets", "jobs", "cronjobs", "horizontalpodautoscalers",
    # The kubelet's metrics endpoints (cAdvisor), for CPU throttling only.
    "nodes/metrics",
}
NODE_NAME = "drake-otel-node"


def check(docs, by_kind, label, expect_obi, expect_metrics=False):
    [sc] = by_kind["StorageClass"]
    if sc.get("reclaimPolicy") != "Retain":
        fail(f"{label}: StorageClass must be Retain")
    if sc["metadata"].get("annotations", {}).get("helm.sh/resource-policy") != "keep":
        fail(f"{label}: StorageClass must be kept on uninstall")

    [sts] = by_kind["StatefulSet"]
    retention = sts["spec"].get("persistentVolumeClaimRetentionPolicy", {})
    if retention != {"whenDeleted": "Retain", "whenScaled": "Retain"}:
        fail(f"{label}: StatefulSet claims must be retained on delete and scale")

    # Agent content (prompts, answers, tool arguments) never reaches the
    # store: every gateway traces pipeline normalises GenAI telemetry and
    # removes content, and the logs pipeline drops GenAI content events.
    [gw_cm] = [cm for cm in by_kind["ConfigMap"] if cm["metadata"]["name"] == "drake-otel-gateway"]
    gw = yaml.safe_load(gw_cm["data"]["collector.yaml"])
    for pname, pipe in gw["service"]["pipelines"].items():
        procs = pipe.get("processors", [])
        if pname.startswith("traces"):
            for required in ("gen_ai_normalizer", "transform/genai-content", "filter/genai-content"):
                if required not in procs:
                    fail(f"{label}: gateway {pname} must run {required}")
        if pname.startswith("logs") and "filter/genai-content" not in procs:
            fail(f"{label}: gateway {pname} must drop GenAI content events")
    # Who called a tool (`user.id`, `user.name`) is not content: Agent
    # Visibility reads it to say who used an MCP server. No content rule may
    # delete it (the normaliser leaves it alone; checked against 0.162.0).
    content_rules = json.dumps(gw["processors"].get("transform/genai-content", {}))
    for key in ("user.id", "user.name", "user\\\\."):
        if key in content_rules:
            fail(f"{label}: gateway content removal must keep user.id and user.name")
    # Coding agents' telemetry (Claude Code) through the external door: only
    # Claude Code is accepted, and the person's email, prompt and answer
    # text and tool parameters are removed before anything is stored.
    # Checked against a real Claude Code 2.1.260 payload on 0.162.0.
    for pname in ("logs/external", "metrics/external"):
        pipe = gw["service"]["pipelines"].get(pname)
        if pipe is None:
            continue
        for required in ("filter/claude-code", "transform/claude-code"):
            if required not in pipe.get("processors", []):
                fail(f"{label}: gateway {pname} must run {required}")
    if any(p in gw["service"]["pipelines"] for p in ("logs/external", "metrics/external")):
        cc = gw["processors"].get("transform/claude-code", {})

        def deletes(statements, context):
            return {
                stmt.split('"')[1]
                for group in statements
                if group.get("context") == context
                for stmt in group.get("statements", [])
                if stmt.startswith("delete_key(attributes, ")
            }

        logs_cc = cc.get("log_statements", [])
        metrics_cc = cc.get("metric_statements", [])
        # The email is on the resource AND on every record: both levels.
        for context in ("resource", "log"):
            if "user.email" not in deletes(logs_cc, context):
                fail(f"{label}: gateway must delete user.email from Claude Code events ({context})")
        for context in ("resource", "datapoint"):
            if "user.email" not in deletes(metrics_cc, context):
                fail(f"{label}: gateway must delete user.email from Claude Code metrics ({context})")
        for key in ("prompt", "prompt_text", "response", "tool_parameters", "tool_input"):
            if key not in deletes(logs_cc, "log"):
                fail(f"{label}: gateway must delete {key} from Claude Code events")
        dropped = json.dumps(gw["processors"].get("filter/claude-code", {}))
        for event in ("assistant_response", "api_request_body", "api_response_body"):
            if event not in dropped:
                fail(f"{label}: gateway must drop Claude Code {event} events")
    external = [r for r in gw["receivers"] if r.startswith("otlp/external")]
    for r in external:
        if not gw["receivers"][r]["protocols"]["http"].get("auth"):
            fail(f"{label}: gateway {r} must require authentication")

    workloads = by_kind["StatefulSet"] + by_kind["Deployment"] + by_kind["DaemonSet"]
    names = {w["metadata"]["name"] for w in workloads}
    if (OBI_NAME in names) != expect_obi:
        fail(f"{label}: OBI must render exactly when obi.enabled")
    if (METRICS_NAME in names) != expect_metrics:
        fail(f"{label}: the metrics collector must render exactly when metrics.enabled")
    if (NODE_NAME in names) != expect_metrics:
        fail(f"{label}: node metrics must render exactly when metrics.node.enabled is forced on")
    for w in by_kind["Deployment"]:
        if w["metadata"]["name"] == METRICS_NAME:
            # Two would report every cluster-level value twice.
            if w["spec"].get("replicas") != 1 or w["spec"].get("strategy", {}).get("type") != "Recreate":
                fail(f"{label}: the metrics collector must be exactly one replica, recreated on upgrade")

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
        writable = set()
        if name == "drake-otel-agent":
            # Pod logs, read-only; and the read-offset store, the one path
            # the agent may write, under /var/lib/drake-otel only.
            checkpoint = host_paths.get("checkpoints", "")
            if not checkpoint.startswith("/var/lib/drake-otel/"):
                fail(f"{label}: {name}: read offsets must live under /var/lib/drake-otel/")
            permitted = {"/var/log/pods", checkpoint}
            writable = {"checkpoints"}
        elif name == OBI_NAME:
            permitted = OBI_HOST_PATHS
        elif name == NODE_NAME:
            # The host root, read-only (checked below), for /proc, /sys and
            # the root filesystem; as a non-root user, with no API token.
            permitted = {"/"}
            if spec.get("automountServiceAccountToken") is not False:
                fail(f"{label}: {name}: must not mount an API token")
            if spec.get("securityContext", {}).get("runAsNonRoot") is not True:
                fail(f"{label}: {name}: must run as non-root")
        else:
            permitted = set()
        if not set(host_paths.values()) <= permitted:
            fail(f"{label}: {name}: host paths {sorted(set(host_paths.values()) - permitted)} are not allowed")
        for c in spec["containers"]:
            for m in c.get("volumeMounts", []):
                if (
                    m["name"] in host_paths
                    and m["name"] not in writable
                    and m.get("readOnly") is not True
                ):
                    fail(f"{label}: {name}: host mount {host_paths[m['name']]} must be read-only")

    for w in by_kind["StatefulSet"] + by_kind["Deployment"]:
        if not w["spec"]["template"]["spec"].get("nodeSelector"):
            fail(f"{label}: {w['metadata']['name']}: must be pinned with a nodeSelector")
    for w in by_kind["DaemonSet"]:
        if w["metadata"]["name"] == OBI_NAME and not w["spec"]["template"]["spec"].get("nodeSelector"):
            fail(f"{label}: OBI must be pinned with a nodeSelector")

    allowed_resources = {"pods", "namespaces", "replicasets", "jobs", "services", "nodes"}
    for role in by_kind.get("ClusterRole", []):
        permitted_resources = (
            METRICS_RESOURCES if role["metadata"]["name"].endswith("drake-otel-metrics-read") else allowed_resources
        )
        for rule in role["rules"]:
            if not set(rule["verbs"]) <= {"get", "list", "watch"}:
                fail(f"{label}: {role['metadata']['name']}: only get/list/watch are allowed")
            for resource in rule["resources"]:
                if resource not in permitted_resources:
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
        discovery = cfg.get("discovery", {})
        if discovery.get("exclude_otel_instrumented_services") is not True:
            fail(f"{label}: OBI must skip services that already export OTLP traces")
        if discovery.get("skip_go_specific_tracers") is not True:
            fail(f"{label}: OBI must trace Go programs once (generic tracer only)")
        rules = discovery.get("exclude_instrument", []) or []
        if {"k8s_cronjob_name": "?*"} not in rules:
            fail(f"{label}: OBI must leave CronJob runs alone")
        for rule in rules:
            if rule == {"k8s_cronjob_name": "?*"}:
                continue  # the one reviewed pattern: pods a CronJob owns
            # Exact names only: a glob here could silently stop tracing
            # workloads that have no SDK.
            if set(rule) != {"k8s_namespace", "k8s_deployment_name"} or any(
                ch in str(value) for value in rule.values() for ch in "*?["
            ):
                fail(f"{label}: an OBI exclusion must name one namespace and one deployment")

    return len(docs)


off_docs, off_kinds = load(sys.argv[1])
on_docs, on_kinds = load(sys.argv[2])
n_off = check(off_docs, off_kinds, "obi off", expect_obi=False)
n_on = check(on_docs, on_kinds, "obi on", expect_obi=True)
metrics_docs, metrics_kinds = load(sys.argv[3])
n_metrics = check(metrics_docs, metrics_kinds, "metrics on", expect_obi=False, expect_metrics=True)

if failures:
    for f in failures:
        print("POLICY VIOLATION:", f, file=sys.stderr)
    sys.exit(1)
print(f"[policy] {n_off} rendered objects pass; with OBI enabled, {n_on} pass; with metrics enabled, {n_metrics} pass")
PYEOF
