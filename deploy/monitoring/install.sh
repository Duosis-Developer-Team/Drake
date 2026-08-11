#!/usr/bin/env bash
# Install or upgrade Drake's metrics backend.
#
#   bash deploy/monitoring/install.sh            # install/upgrade
#   DRY_RUN=1 bash deploy/monitoring/install.sh  # render only
#
# Idempotent: re-running it upgrades in place and never touches another
# release. It creates nothing outside the `drake-monitoring` namespace.
set -euo pipefail

cd "$(dirname "$0")/../.."

NAMESPACE="${DRAKE_MONITORING_NAMESPACE:-drake-monitoring}"
RELEASE="${DRAKE_MONITORING_RELEASE:-drake-metrics}"
CHART_VERSION="${DRAKE_PROMETHEUS_CHART_VERSION:-27.44.0}"

# The narrowed ClusterRole first: the chart's binding names it, and a
# binding to a role that does not exist grants nothing quietly.
kubectl apply -f deploy/monitoring/rbac.yaml

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts \
  --force-update >/dev/null
helm repo update prometheus-community >/dev/null

# The recording rules live in their own file so they can be reviewed as
# YAML. Helm needs them inside `serverFiles`, so they are grafted on here
# rather than duplicated into values.yaml by hand — one copy, no drift.
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
python3 - "$RENDERED" <<'PYEOF'
import os
import sys

import yaml

values = yaml.safe_load(open("deploy/monitoring/values.yaml"))
server_files = values.setdefault("serverFiles", {})

# Scrape ownership, expressed as a DELTA on the chart's own defaults.
#
# Copying all seven jobs into our values would make this repository the
# source of truth for scrape config it did not write, and it would drift
# silently the first time the chart changed one. So the defaults are read
# from the chart and two things are added to the endpoints job:
#
#   1. the foreign kube-state-metrics is not scraped. Another team's Zabbix
#      stack publishes one, Prometheus discovers it by annotation, and it
#      exports Secret and ConfigMap NAMES. Their deployment is not touched;
#      we simply stop collecting it.
#   2. secret/configmap metadata is dropped whatever exports it, so a new
#      exporter appearing later cannot quietly reintroduce the same series.
import subprocess

defaults = yaml.safe_load(
    subprocess.run(
        ["helm", "show", "values", "prometheus-community/prometheus",
         "--version", os.environ.get("DRAKE_PROMETHEUS_CHART_VERSION", "27.44.0")],
        capture_output=True, text=True, check=True,
    ).stdout
)
scrape_configs = defaults["serverFiles"]["prometheus.yml"]["scrape_configs"]
for job in scrape_configs:
    if job["job_name"] != "kubernetes-service-endpoints":
        continue
    job["relabel_configs"].insert(0, {
        "source_labels": ["__meta_kubernetes_service_name"],
        "action": "drop",
        "regex": "zabbix-kube-state-metrics",
    })
    job.setdefault("metric_relabel_configs", []).append({
        "source_labels": ["__name__"],
        "action": "drop",
        "regex": "kube_(secret|configmap)_.*",
    })
server_files.setdefault("prometheus.yml", {})["scrape_configs"] = scrape_configs
server_files["recording_rules.yml"] = yaml.safe_load(
    open("deploy/monitoring/recording-rules.yaml")
)
server_files["alerting_rules.yml"] = yaml.safe_load(
    open("deploy/monitoring/alert-rules.yaml")
)
yaml.safe_dump(values, open(sys.argv[1], "w"), sort_keys=False)
PYEOF

ARGS=(
  "$RELEASE" prometheus-community/prometheus
  --namespace "$NAMESPACE" --create-namespace
  --version "$CHART_VERSION"
  -f "$RENDERED"
)

if [ "${DRY_RUN:-0}" = "1" ]; then
  helm template "${ARGS[@]}" | head -40
  echo "[monitoring] dry run only"
  exit 0
fi

helm upgrade --install "${ARGS[@]}" --wait --timeout 10m

echo "[monitoring] pods:"
kubectl get pods -n "$NAMESPACE" --no-headers | awk '{print "  " $1, $2, $3}'

# Printed from the cluster, not composed from the release name: the chart
# names the Service `<release>-prometheus-server`, and a guessed address in
# a success message is how the wrong one ends up in a values file.
echo "[monitoring] Drake reaches this at the pinned address:"
kubectl get svc -n "$NAMESPACE" -l app.kubernetes.io/name=prometheus \
  -o jsonpath='  http://{.items[0].spec.clusterIP}  ({.items[0].metadata.name}){"\n"}'
