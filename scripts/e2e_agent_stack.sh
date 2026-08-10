#!/usr/bin/env bash
# Full-chain E2E prerequisites for the cluster-agent scenario:
#   up:   disposable k3d cluster + kubeconfig + TLS material + agent binary
#   down: delete the disposable cluster and material
#
# Everything lands in .e2e-agent/ (gitignored). Real/shared clusters are
# never touched; the k3d cluster is disposable by name and deleted on
# `down` (and by CI runner teardown).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_DIR="$REPO_ROOT/.e2e-agent"
CLUSTER_NAME="${DRAKE_E2E_K3D_CLUSTER:-drake-s4-e2e}"

UP_COMPLETED=""

command_up() {
  command -v k3d >/dev/null || { echo "k3d is required" >&2; exit 2; }
  command -v kubectl >/dev/null || { echo "kubectl is required" >&2; exit 2; }
  command -v go >/dev/null || { echo "go is required" >&2; exit 2; }

  # `up` leaves a cluster running on purpose — the E2E scenario uses it and
  # `down` removes it. So cleanup must fire only when up did NOT finish:
  # before this, a failure in the TLS step or the go build (or a Ctrl+C)
  # left a full k3d cluster running with nothing left to delete it.
  . "$REPO_ROOT/scripts/lib/process_lifecycle.sh"
  lifecycle_on_cleanup '[ -n "$UP_COMPLETED" ] || {
      echo "[e2e-agent] up did not complete; removing the partial stack" >&2
      k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
      rm -rf "$STACK_DIR"
    }'
  lifecycle_install_traps

  mkdir -p "$STACK_DIR"

  echo "[e2e-agent] creating disposable k3d cluster ${CLUSTER_NAME}"
  k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
  k3d cluster create "$CLUSTER_NAME" --no-lb --wait --timeout 120s \
    --k3s-arg "--disable=traefik@server:0" >/dev/null
  k3d kubeconfig get "$CLUSTER_NAME" > "$STACK_DIR/kubeconfig"
  chmod 600 "$STACK_DIR/kubeconfig"

  echo "[e2e-agent] generating ephemeral TLS material"
  (cd "$REPO_ROOT" && uv run python scripts/e2e_agent_tls.py "$STACK_DIR")

  echo "[e2e-agent] building agent binary"
  (cd "$REPO_ROOT/apps/cluster-agent" && go build -o "$STACK_DIR/agent" ./cmd/agent)

  # Seed workload so the first snapshot has real, assertable content.
  KUBECONFIG="$STACK_DIR/kubeconfig" kubectl create namespace e2e-workloads \
    --dry-run=client -o yaml | KUBECONFIG="$STACK_DIR/kubeconfig" kubectl apply -f - >/dev/null
  KUBECONFIG="$STACK_DIR/kubeconfig" kubectl -n e2e-workloads create deployment e2e-web \
    --image=rancher/mirrored-pause:3.6 --replicas=1 --dry-run=client -o yaml \
    | KUBECONFIG="$STACK_DIR/kubeconfig" kubectl apply -f - >/dev/null
  # A Secret that must NEVER surface anywhere in Drake (leak canary).
  KUBECONFIG="$STACK_DIR/kubeconfig" kubectl -n e2e-workloads create secret generic e2e-canary-secret \
    --from-literal=canary="drake-e2e-secret-canary-value" \
    --dry-run=client -o yaml | KUBECONFIG="$STACK_DIR/kubeconfig" kubectl apply -f - >/dev/null

  UP_COMPLETED=1
  echo "[e2e-agent] stack ready: $STACK_DIR"
}

command_down() {
  k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
  rm -rf "$STACK_DIR"
  echo "[e2e-agent] stack removed"
}

case "${1:-}" in
  up) command_up ;;
  down) command_down ;;
  *) echo "usage: $0 up|down" >&2; exit 2 ;;
esac
