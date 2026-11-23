#!/usr/bin/env bash
# Render + policy-check the tracesis-agent chart (ADR-0037). CI-safe: no
# cluster access, no install. This is the executable form of what the one
# customer-facing install promises:
#   - nothing in it can read a Secret or a ConfigMap (every ClusterRole/Role)
#   - every image is pinned by digest
#   - the cluster is named once: a hand edit that disagrees refuses to render
#   - a first install without a token refuses to render; an upgrade does not
#   - every Prometheus job stamps this cluster's cluster_ref, and the
#     recording rules are exactly the ones duosis-prod-1 runs
#   - the agent relays to the in-cluster stores and nowhere else
set -euo pipefail

cd "$(dirname "$0")"

if ! helm repo list 2>/dev/null | grep -q '^prometheus-community'; then
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
fi
helm dependency build . >/dev/null

ID=11111111-2222-3333-4444-555555555555
SETS=(
  --set cluster.id=$ID --set cluster.name=policy-check
  --set clusterAgent.clusterId=$ID --set clusterAgent.clusterName=policy-check
  --set telemetry.clusterName=policy-check
  --set clusterAgent.apiBaseUrl=https://tracesis.example.test:30843
  --set clusterAgent.enrollmentBaseUrl=https://tracesis.example.test:30844
  --set-string tracesis.serverCA="-----BEGIN CERTIFICATE-----"
  --set enrollment.token=policy-check-token
  --set clusterAgent.image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000
  --set telemetry.images.collector.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000
)

echo "[helm] lint"
helm lint . "${SETS[@]}" >/dev/null

refuse() {
  local why="$1"
  shift
  if helm template tracesis-agent . --namespace tracesis "${SETS[@]}" "$@" >/dev/null 2>&1; then
    echo "POLICY VIOLATION: render succeeded $why" >&2
    exit 1
  fi
}

echo "[policy] fail-closed renders"
refuse "without a cluster id" --set cluster.id=""
refuse "with the agent naming another cluster" --set clusterAgent.clusterName=other
refuse "with the collector naming another cluster" --set telemetry.clusterName=other
refuse "without the listener CA" --set-string tracesis.serverCA=""
refuse "on first install without an enrollment token" --set enrollment.token=""
refuse "without a pinned agent image" --set clusterAgent.image.digest=""
refuse "without a pinned collector image" --set telemetry.images.collector.digest=""

# An upgrade carries no token: the agent's identity lives on its own volume.
helm template tracesis-agent . --namespace tracesis "${SETS[@]}" \
  --set enrollment.token="" --is-upgrade >/dev/null

echo "[contract] the install file Tracesis generates renders as is"
GENERATED="$(mktemp)"
trap 'rm -f "$GENERATED"' EXIT
(cd ../.. && uv run --quiet python - "$GENERATED" <<'PYEOF'
import sys

import yaml
from drake_api.agents.router_install import install_values
from drake_api.testing import make_settings

settings = make_settings(
    env="test",
    agent_public_enrollment_url="https://tracesis.example.test:30844",
    agent_public_ingest_url="https://tracesis.example.test:30843",
)
values = install_values(
    settings,
    cluster_id="11111111-2222-3333-4444-555555555555",
    cluster_ref="generated-check",
    server_ca="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
    token="generated-token",
)
yaml.safe_dump(values, open(sys.argv[1], "w"))
PYEOF
)
helm template tracesis-agent . --namespace tracesis -f "$GENERATED" \
  --set clusterAgent.image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  --set telemetry.images.collector.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  >/dev/null

RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED" "$GENERATED"' EXIT
helm template tracesis-agent . --namespace tracesis "${SETS[@]}" >"$RENDERED"

echo "[policy] rendered manifest"
python3 - "$RENDERED" ../monitoring/recording-rules.yaml <<'PYEOF'
import sys

import yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
expected_rules = yaml.safe_load(open(sys.argv[2]))
errors = []

for doc in docs:
    kind, name = doc["kind"], doc["metadata"]["name"]
    if kind in ("ClusterRole", "Role"):
        for rule in doc.get("rules") or []:
            resources = set(rule.get("resources") or [])
            if resources & {"secrets", "configmaps"}:
                errors.append(f"{kind} {name} grants {sorted(resources & {'secrets', 'configmaps'})}")
            if "*" in resources or "*" in (rule.get("verbs") or []):
                errors.append(f"{kind} {name} uses a wildcard")
            writes = set(rule.get("verbs") or []) - {"get", "list", "watch"}
            if writes:
                errors.append(f"{kind} {name} grants {sorted(writes)}")
    spec = (doc.get("spec") or {}).get("template", {}).get("spec")
    if spec:
        for container in spec.get("containers", []) + spec.get("initContainers", []):
            image = container["image"]
            if "@sha256:" not in image:
                errors.append(f"{kind} {name}: {container['name']} image not pinned by digest: {image}")

config = next(
    d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("tracesis-prometheus-config")
)
prometheus = yaml.safe_load(config["data"]["prometheus.yml"])
for job in prometheus["scrape_configs"]:
    stamps = [
        r for r in job.get("relabel_configs", [])
        if r.get("target_label") == "cluster_ref" and r.get("replacement") == "policy-check"
    ]
    if len(stamps) != 1:
        errors.append(f"prometheus job {job['job_name']} does not stamp cluster_ref exactly once")
if yaml.safe_load(config["data"]["recording_rules.yml"]) != expected_rules:
    errors.append("recording rules differ from deploy/monitoring/recording-rules.yaml")

agent = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "drake-cluster-agent")
env = {e["name"]: e.get("value") for e in agent["spec"]["template"]["spec"]["containers"][0]["env"]}
services = {d["metadata"]["name"] for d in docs if d["kind"] == "Service"}
for key in ("DRAKE_AGENT_RELAY_CLICKHOUSE_URL", "DRAKE_AGENT_RELAY_PROMETHEUS_URL"):
    host = (env.get(key) or "").split("://", 1)[-1].split(":", 1)[0].split("/", 1)[0]
    if host not in services:
        errors.append(f"{key} points at {host!r}, which is not a Service of this release")

if errors:
    print("POLICY VIOLATIONS:", *errors, sep="\n  ", file=sys.stderr)
    sys.exit(1)
print(f"[policy] tracesis-agent OK ({len(docs)} objects checked)")
PYEOF
