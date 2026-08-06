#!/usr/bin/env bash
# Agent RBAC acceptance on a DISPOSABLE k3d cluster.
#
# Creates a throwaway cluster, applies ONLY the chart's rendered RBAC
# (ServiceAccount + ClusterRole + ClusterRoleBinding), then proves the
# permission surface with a `kubectl auth can-i` matrix: every allowlisted
# read must be YES, every forbidden operation must be NO. The cluster is
# deleted afterwards no matter what. Real/shared clusters are never touched.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${DRAKE_ACCEPTANCE_CLUSTER:-drake-s4-rbac-acceptance}"
NAMESPACE="drake-system"
SA="system:serviceaccount:${NAMESPACE}:drake-cluster-agent"

command -v k3d >/dev/null || { echo "k3d is required" >&2; exit 2; }
command -v kubectl >/dev/null || { echo "kubectl is required" >&2; exit 2; }
command -v helm >/dev/null || { echo "helm is required" >&2; exit 2; }

cleanup() {
  k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[k3d] creating disposable cluster ${CLUSTER_NAME}"
k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
k3d cluster create "$CLUSTER_NAME" --no-lb --wait --timeout 120s \
  --k3s-arg "--disable=traefik@server:0" >/dev/null

KUBECONFIG_FILE="$(mktemp)"
k3d kubeconfig get "$CLUSTER_NAME" > "$KUBECONFIG_FILE"
export KUBECONFIG="$KUBECONFIG_FILE"

kubectl create namespace "$NAMESPACE" >/dev/null

echo "[helm] rendering chart RBAC (ServiceAccount/ClusterRole/Binding only)"
helm template acceptance "$REPO_ROOT/deploy/agent" \
  --namespace "$NAMESPACE" \
  --set clusterId=00000000-0000-0000-0000-000000000000 \
  --set clusterName=acceptance \
  --set apiBaseUrl=https://drake-internal.example.test \
  --set image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  --set serverCA.existingSecret=drake-agent-server-ca \
  --set enrollmentToken.existingSecret=drake-agent-enrollment \
  --set networkPolicy.apiEndpointCIDR=203.0.113.10/32 \
  --set networkPolicy.kubernetesApiCIDR=198.51.100.0/24 \
  --show-only templates/rbac.yaml | kubectl apply -f - >/dev/null

failures=0
matrix_row() {
  local expected="$1"; shift
  local answer
  answer="$(kubectl auth can-i "$@" --as="$SA" 2>/dev/null || true)"
  local verdict="OK"
  if [ "$answer" != "$expected" ]; then
    verdict="MISMATCH"
    failures=$((failures + 1))
  fi
  printf '%-9s %-45s expected=%-3s got=%-3s %s\n' \
    "[matrix]" "$*" "$expected" "$answer" "$verdict"
}

echo "[matrix] --- allowlisted reads (must be YES) ---"
matrix_row yes get pods
matrix_row yes list pods
matrix_row yes watch pods
matrix_row yes list nodes
matrix_row yes list namespaces
matrix_row yes list services
matrix_row yes list endpointslices.discovery.k8s.io
matrix_row yes list deployments.apps
matrix_row yes watch replicasets.apps
matrix_row yes list statefulsets.apps
matrix_row yes list daemonsets.apps
matrix_row yes list jobs.batch
matrix_row yes list cronjobs.batch
matrix_row yes list persistentvolumeclaims
matrix_row yes list persistentvolumes
matrix_row yes list storageclasses.storage.k8s.io
matrix_row yes list horizontalpodautoscalers.autoscaling
matrix_row yes list poddisruptionbudgets.policy
matrix_row yes list resourcequotas
matrix_row yes list limitranges
matrix_row yes list events

echo "[matrix] --- forbidden surface (must be NO) ---"
matrix_row no get secrets
matrix_row no list secrets
matrix_row no watch secrets
matrix_row no get configmaps
matrix_row no list configmaps
matrix_row no create pods --subresource=exec
matrix_row no create pods --subresource=attach
matrix_row no create pods --subresource=portforward
matrix_row no create pods
matrix_row no delete pods
matrix_row no update deployments.apps
matrix_row no patch nodes
matrix_row no deletecollection pods
matrix_row no create tokenreviews.authentication.k8s.io
matrix_row no create subjectaccessreviews.authorization.k8s.io
matrix_row no create leases.coordination.k8s.io
matrix_row no impersonate users
matrix_row no escalate clusterroles.rbac.authorization.k8s.io
matrix_row no bind clusterroles.rbac.authorization.k8s.io

if [ "$failures" -ne 0 ]; then
  echo "[matrix] FAILED: ${failures} mismatches" >&2
  exit 1
fi
echo "[matrix] RBAC surface proven: all allowlisted reads YES, all forbidden NO"
