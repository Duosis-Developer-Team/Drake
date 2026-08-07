#!/usr/bin/env bash
# Full-chart smoke on a DISPOSABLE k3d cluster — no mocks, no real clusters:
#   1. build the agent image from apps/cluster-agent/Dockerfile
#   2. import it into a throwaway k3d cluster
#   3. run the real internal TLS listener on the host
#   4. mint a one-time enrollment token (fixture world)
#   5. create the existingSecret fixtures the chart references
#   6. apply the FULL rendered chart (RBAC + Deployment + NetworkPolicy)
#   7. prove: pod Ready, liveness subcommand exits 0, no Service/Ingress,
#      securityContext intact, and the agent REALLY syncs through the
#      fail-closed NetworkPolicy (positive path)
#   8. prove the NEGATIVE path: removing the API egress rule stops the
#      agent's heartbeats (the policy actually denies)
#   9. delete the cluster, image, and artifacts
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${DRAKE_SMOKE_CLUSTER:-drake-s4-chart-smoke}"
NAMESPACE="drake-system"
SMOKE_DIR="$REPO_ROOT/.e2e-agent-smoke"
IMAGE_TAG="drake-cluster-agent:smoke"
INTERNAL_PORT=58446
DB_URL="${DRAKE_SMOKE_DATABASE_URL:-postgresql+psycopg://drake:drake_local_only_dev@127.0.0.1:55432/drake}"
REDIS_URL="${DRAKE_SMOKE_REDIS_URL:-redis://127.0.0.1:56379/0}"

command -v k3d >/dev/null || { echo "k3d is required" >&2; exit 2; }
command -v kubectl >/dev/null || { echo "kubectl is required" >&2; exit 2; }
command -v helm >/dev/null || { echo "helm is required" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }

API_PID=""
cleanup() {
  if [ -n "$API_PID" ]; then
    pkill -P "$API_PID" >/dev/null 2>&1 || true
    kill "$API_PID" >/dev/null 2>&1 || true
  fi
  k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
  docker image rm "$IMAGE_TAG" >/dev/null 2>&1 || true
  rm -rf "$SMOKE_DIR"
}
trap cleanup EXIT

echo "[smoke] building agent image"
docker build -t "$IMAGE_TAG" "$REPO_ROOT/apps/cluster-agent" 2>&1 | tail -2

echo "[smoke] creating disposable k3d cluster ${CLUSTER_NAME}"
k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
k3d cluster create "$CLUSTER_NAME" --no-lb --wait --timeout 120s \
  --k3s-arg "--disable=traefik@server:0" >/dev/null
KUBECONFIG_FILE="$SMOKE_DIR/kubeconfig"
mkdir -p "$SMOKE_DIR"
k3d kubeconfig get "$CLUSTER_NAME" > "$KUBECONFIG_FILE"
chmod 600 "$KUBECONFIG_FILE"
export KUBECONFIG="$KUBECONFIG_FILE"

echo "[smoke] importing image"
k3d image import "$IMAGE_TAG" -c "$CLUSTER_NAME" >/dev/null

echo "[smoke] TLS material + internal listener (host)"
(cd "$REPO_ROOT" && uv run python scripts/e2e_agent_tls.py "$SMOKE_DIR") >/dev/null
# The listener binds all interfaces so the k3d network can reach it via
# host.k3d.internal — a LOCAL, disposable test only.
# exec (not a subshell chain) so $API_PID is the listener itself and the
# cleanup trap really kills it — an orphan would hold pipes open forever.
(cd "$REPO_ROOT" && exec env DRAKE_ENV=local \
  DRAKE_DATABASE_URL="$DB_URL" DRAKE_REDIS_URL="$REDIS_URL" \
  DRAKE_AGENT_CA_CERT_FILE="$SMOKE_DIR/ca/agent-ca.pem" \
  DRAKE_AGENT_CA_KEY_FILE="$SMOKE_DIR/ca/agent-ca-key.pem" \
  uv run python scripts/run_internal_agent_api.py \
    --host 0.0.0.0 --port "$INTERNAL_PORT" \
    --tls-cert "$SMOKE_DIR/internal-server.pem" \
    --tls-key "$SMOKE_DIR/internal-server-key.pem" \
    --client-ca "$SMOKE_DIR/ca/agent-ca.pem" \
    --client-cert-optional >"$SMOKE_DIR/listener.log" 2>&1) &
API_PID=$!
echo "[smoke] waiting for the internal listener"
LISTENER_UP=""
for attempt in $(seq 1 60); do
  if curl -ks "https://127.0.0.1:${INTERNAL_PORT}/x" -o /dev/null 2>/dev/null; then
    LISTENER_UP="yes"
    break
  fi
  sleep 1
done
if [ -z "$LISTENER_UP" ]; then
  echo "FAIL: internal listener never became ready; log follows" >&2
  tail -20 "$SMOKE_DIR/listener.log" >&2 || true
  exit 1
fi

echo "[smoke] fixture world + one-time token"
(cd "$REPO_ROOT" && DRAKE_DATABASE_URL="$DB_URL" bash scripts/e2e-setup.sh) >/dev/null
TOKEN="$(cd "$REPO_ROOT" && DRAKE_SMOKE_DATABASE_URL="$DB_URL" uv run python - <<'PYEOF'
import asyncio
import hashlib
import os
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine(os.environ["DRAKE_SMOKE_DATABASE_URL"])
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO agent_enrollment_tokens
                    (cluster_id, token_hash, created_by, expires_at)
                SELECT c.id, :digest, i.id, now() + interval '10 minutes'
                FROM clusters c, identities i
                WHERE c.cluster_ref = 'cluster-a'
                ORDER BY i.created_at LIMIT 1
                """
            ),
            {"digest": digest},
        )
    await engine.dispose()
    print(token)


asyncio.run(main())
PYEOF
)"
CLUSTER_ID="$(cd "$REPO_ROOT" && DRAKE_SMOKE_DATABASE_URL="$DB_URL" uv run python - <<'PYEOF'
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine(os.environ["DRAKE_SMOKE_DATABASE_URL"])
    async with engine.connect() as connection:
        cluster_id = (
            await connection.execute(
                text("SELECT id FROM clusters WHERE cluster_ref = 'cluster-a'")
            )
        ).scalar_one()
    await engine.dispose()
    print(cluster_id)


asyncio.run(main())
PYEOF
)"

echo "[smoke] existingSecret fixtures"
kubectl create namespace "$NAMESPACE" >/dev/null
kubectl -n "$NAMESPACE" create secret generic drake-agent-server-ca \
  --from-file=ca.pem="$SMOKE_DIR/internal-server.pem" >/dev/null
kubectl -n "$NAMESPACE" create secret generic drake-agent-enrollment \
  --from-literal=token="$TOKEN" >/dev/null

# Egress target: the REAL IP the cluster resolves for host.k3d.internal
# (k3d writes it into CoreDNS NodeHosts; on Docker Desktop it is the
# host-gateway address, NOT the bridge subnet).
#
# k3d injects that entry ASYNCHRONOUSLY, shortly after the cluster reports
# ready — reading the ConfigMap once races the injection. Poll on a bounded
# budget instead: no fixed sleep, no infinite retry, no fallback IP, and
# the NetworkPolicy target stays the real /32 address either way.
NODEHOSTS_ATTEMPTS=30
NODEHOSTS_INTERVAL=1
NODEHOSTS_BUDGET_SECONDS=$((NODEHOSTS_ATTEMPTS * NODEHOSTS_INTERVAL))

read_host_alias_ip() {
  # Every failure here is "not yet", not "broken": the ConfigMap may not
  # exist, kubectl may error transiently, or NodeHosts may not carry the
  # alias so far. Each case returns EMPTY with status 0, so a first-attempt
  # miss cannot kill the script under `set -euo pipefail`.
  local node_hosts=""
  if ! node_hosts="$(kubectl -n kube-system get configmap coredns \
    -o jsonpath='{.data.NodeHosts}' 2>/dev/null)"; then
    return 0
  fi
  printf '%s\n' "$node_hosts" \
    | awk '{ for (i = 2; i <= NF; i++) if ($i == "host.k3d.internal") { print $1; exit } }'
  return 0
}

echo "[smoke] waiting for the k3d host alias in CoreDNS (<= ${NODEHOSTS_BUDGET_SECONDS}s)"
HOST_ALIAS_IP=""
for attempt in $(seq 1 "$NODEHOSTS_ATTEMPTS"); do
  HOST_ALIAS_IP="$(read_host_alias_ip)"
  if [ -n "$HOST_ALIAS_IP" ]; then
    break
  fi
  if [ "$attempt" -lt "$NODEHOSTS_ATTEMPTS" ]; then
    sleep "$NODEHOSTS_INTERVAL"
  fi
done

if [ -z "$HOST_ALIAS_IP" ]; then
  {
    echo "FAIL: host.k3d.internal never appeared in the CoreDNS NodeHosts"
    echo "      (waited ${NODEHOSTS_ATTEMPTS} attempts x ${NODEHOSTS_INTERVAL}s" \
      "= ${NODEHOSTS_BUDGET_SECONDS}s)"
    echo "--- diagnostic: CoreDNS NodeHosts (host entries only) ---"
    kubectl -n kube-system get configmap coredns \
      -o jsonpath='{.data.NodeHosts}' 2>&1 || echo "(CoreDNS ConfigMap unreadable)"
    echo
  } >&2
  exit 1
fi
echo "[smoke] host.k3d.internal resolves to ${HOST_ALIAS_IP} in-cluster"
# Egress policies match the POST-DNAT destination: the Kubernetes API rule
# must therefore target the real apiserver ENDPOINT (node ip:6443), not
# the 10.43.0.1 service VIP.
KUBE_API_IP="$(kubectl get endpoints kubernetes \
  -o jsonpath='{.subsets[0].addresses[0].ip}')"
KUBE_API_PORT="$(kubectl get endpoints kubernetes \
  -o jsonpath='{.subsets[0].ports[0].port}')"
[ -n "$KUBE_API_IP" ] || { echo "FAIL: kubernetes endpoint unresolved" >&2; exit 1; }
echo "[smoke] kubernetes API endpoint is ${KUBE_API_IP}:${KUBE_API_PORT}"

render_chart() {
  helm template smoke "$REPO_ROOT/deploy/agent" \
    --namespace "$NAMESPACE" \
    --set clusterId="$CLUSTER_ID" \
    --set clusterName=cluster-a \
    --set apiBaseUrl="https://host.k3d.internal:${INTERNAL_PORT}" \
    --set image.repository=drake-cluster-agent \
    --set image.devTag=smoke \
    --set image.pullPolicy=Never \
    --set serverCA.existingSecret=drake-agent-server-ca \
    --set enrollmentToken.existingSecret=drake-agent-enrollment \
    --set networkPolicy.apiEndpointCIDR="${HOST_ALIAS_IP}/32" \
    --set networkPolicy.apiEndpointPort="$INTERNAL_PORT" \
    --set networkPolicy.kubernetesApiCIDR="${KUBE_API_IP}/32" \
    --set networkPolicy.kubernetesApiPort="${KUBE_API_PORT}" \
    "$@"
}

echo "[smoke] applying the FULL rendered chart"
render_chart | kubectl apply -f - >/dev/null
kubectl -n "$NAMESPACE" rollout status deployment/drake-cluster-agent --timeout=120s >/dev/null
POD="$(kubectl -n "$NAMESPACE" get pods -l app.kubernetes.io/name=drake-cluster-agent \
  -o jsonpath='{.items[0].metadata.name}')"

echo "[smoke] liveness subcommand executes inside the container"
LIVE_OK=""
for attempt in $(seq 1 20); do
  if kubectl -n "$NAMESPACE" exec "$POD" -- \
      /usr/local/bin/drake-agent healthcheck 127.0.0.1:8090 >/dev/null 2>&1; then
    LIVE_OK="yes"
    break
  fi
  sleep 3
done
if [ -z "$LIVE_OK" ]; then
  echo "FAIL: liveness subcommand never succeeded; pod logs follow" >&2
  kubectl -n "$NAMESPACE" logs "$POD" --tail=40 >&2 || true
  exit 1
fi
echo "[smoke] liveness OK"

echo "[smoke] surface + securityContext assertions"
SERVICES="$(kubectl -n "$NAMESPACE" get services -o name | wc -l | tr -d ' ')"
INGRESSES="$(kubectl -n "$NAMESPACE" get ingress -o name 2>/dev/null | wc -l | tr -d ' ')"
[ "$SERVICES" = "0" ] || { echo "FAIL: agent namespace exposes a Service" >&2; exit 1; }
[ "$INGRESSES" = "0" ] || { echo "FAIL: agent namespace exposes an Ingress" >&2; exit 1; }
kubectl -n "$NAMESPACE" get pod "$POD" -o json > "$SMOKE_DIR/pod.json"
python3 - "$SMOKE_DIR/pod.json" <<'PYEOF'
import json
import sys

with open(sys.argv[1]) as handle:
    pod = json.load(handle)
spec = pod["spec"]
container = spec["containers"][0]
security = container["securityContext"]
assert spec["securityContext"]["runAsNonRoot"] is True
assert spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
assert security["allowPrivilegeEscalation"] is False
assert security["readOnlyRootFilesystem"] is True
assert security["capabilities"]["drop"] == ["ALL"]
assert not spec.get("hostNetwork") and not spec.get("hostPID") and not spec.get("hostIPC")
print("[smoke] securityContext intact")
PYEOF

echo "[smoke] POSITIVE: agent syncs through the fail-closed NetworkPolicy"
SYNCED="$(cd "$REPO_ROOT" && DRAKE_SMOKE_DATABASE_URL="$DB_URL" uv run python - <<'PYEOF'
import asyncio
import os
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine(os.environ["DRAKE_SMOKE_DATABASE_URL"])
    deadline = time.time() + 120
    state = "none"
    while time.time() < deadline:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT agents.inventory_state FROM cluster_agents agents
                        JOIN clusters c ON c.id = agents.cluster_id
                        WHERE c.cluster_ref = 'cluster-a'
                        ORDER BY agents.created_at DESC LIMIT 1
                        """
                    )
                )
            ).first()
        if row is not None:
            state = str(row[0])
            if state == "fresh":
                break
        await asyncio.sleep(2)
    await engine.dispose()
    print(state)


asyncio.run(main())
PYEOF
)"
if [ "$SYNCED" != "fresh" ]; then
  echo "FAIL: agent never reached fresh through the NetworkPolicy (state=$SYNCED)" >&2
  echo "--- pod logs ---" >&2
  kubectl -n "$NAMESPACE" logs -l app.kubernetes.io/name=drake-cluster-agent \
    --tail=40 >&2 || true
  echo "--- pod describe (events) ---" >&2
  kubectl -n "$NAMESPACE" describe pod -l app.kubernetes.io/name=drake-cluster-agent \
    2>/dev/null | tail -20 >&2 || true
  exit 1
fi
echo "[smoke] positive path OK (inventory fresh through restricted egress)"

echo "[smoke] NEGATIVE: removing the API egress rule stops the sync"
# Re-render with an unroutable API CIDR: DNS + kube-API stay allowed, the
# Drake endpoint does not — heartbeats must stop.
render_chart --set networkPolicy.apiEndpointCIDR="192.0.2.1/32" \
  | kubectl apply -f - >/dev/null
kubectl -n "$NAMESPACE" delete pod "$POD" --wait=true >/dev/null
sleep 20
STALLED="$(cd "$REPO_ROOT" && DRAKE_SMOKE_DATABASE_URL="$DB_URL" uv run python - <<'PYEOF'
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine(os.environ["DRAKE_SMOKE_DATABASE_URL"])
    async with engine.connect() as connection:
        age = (
            await connection.execute(
                text(
                    """
                    SELECT EXTRACT(EPOCH FROM (now() - max(agents.last_heartbeat_at)))
                    FROM cluster_agents agents
                    JOIN clusters c ON c.id = agents.cluster_id
                    WHERE c.cluster_ref = 'cluster-a'
                    """
                )
            )
        ).scalar_one()
    await engine.dispose()
    print("stalled" if age is None or float(age) > 15 else "still-syncing")


asyncio.run(main())
PYEOF
)"
[ "$STALLED" = "stalled" ] || { echo "FAIL: NetworkPolicy did not deny the API egress" >&2; exit 1; }
echo "[smoke] negative path OK (denied egress stops heartbeats)"

echo "[smoke] chart smoke PASSED (cluster/image/artifacts cleaned by trap)"
