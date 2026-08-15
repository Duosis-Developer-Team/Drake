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
# `pkill -P` reaches one generation. A listener started through a wrapper
# keeps its grandchildren, and those hold the port the next run needs.
. "$REPO_ROOT/scripts/lib/process_lifecycle.sh"
lifecycle_on_cleanup 'if ! k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1; then echo "WARNING: could not delete the disposable cluster $CLUSTER_NAME" >&2; fi'
lifecycle_on_cleanup 'docker image rm "$IMAGE_TAG" >/dev/null 2>&1 || true'
lifecycle_on_cleanup 'rm -rf "$SMOKE_DIR"'
lifecycle_install_traps

echo "[smoke] building agent image"
# A failed build prints the compiler's complaint above buildkit's summary, so
# `| tail -2` reported only that something failed. Keep the whole log and show
# the end of it when — and only when — the build fails.
mkdir -p "$SMOKE_DIR"
if ! docker build -t "$IMAGE_TAG" "$REPO_ROOT/apps/cluster-agent" \
    >"$SMOKE_DIR/build.log" 2>&1; then
  tail -40 "$SMOKE_DIR/build.log" >&2
  exit 1
fi

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
bash "$REPO_ROOT/scripts/k3d_image_import.sh" "$IMAGE_TAG" "$CLUSTER_NAME"

# The address the cluster reaches the host on.
#
# The agent used to be pointed at `host.k3d.internal` and resolve it through
# CoreDNS. k3d injects that alias into the CoreDNS `NodeHosts` ConfigMap,
# and k3s re-applies its own coredns manifest — so the entry can be present
# when the script checks and gone when the pod looks it up. That is the
# `no such host` failure, and no amount of waiting fixes a value that
# disappears after the wait.
#
# Docker knows the gateway without asking Kubernetes anything, so the test
# uses the address directly and the DNS race stops existing.
HOST_GATEWAY_IP="$(docker network inspect "k3d-${CLUSTER_NAME}" \
  --format '{{ (index .IPAM.Config 0).Gateway }}' 2>/dev/null || true)"
if [ -z "$HOST_GATEWAY_IP" ]; then
  echo "FAIL: could not resolve the k3d network gateway for ${CLUSTER_NAME}" >&2
  exit 1
fi
echo "[smoke] cluster reaches the host at ${HOST_GATEWAY_IP}"

echo "[smoke] TLS material + internal listener (host)"
(cd "$REPO_ROOT" && uv run python scripts/e2e_agent_tls.py "$SMOKE_DIR" "$HOST_GATEWAY_IP") >/dev/null
# The listener binds all interfaces so the k3d network can reach it at the
# gateway address — a LOCAL, disposable test only.
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
lifecycle_track "$API_PID" "chart-smoke-listener"
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

# The agent reaches the host listener by ADDRESS, resolved from Docker
# above. The CoreDNS `host.k3d.internal` alias this used to wait for is
# injected asynchronously and can be dropped again when k3s re-applies its
# coredns manifest — so waiting for it proved nothing about whether the pod
# would still resolve it a minute later. It no longer participates.

# Egress policies match the POST-DNAT destination: the Kubernetes API rule
# must therefore target the real apiserver ENDPOINT (node ip:6443), not
# the 10.43.0.1 service VIP.
KUBE_API_IP="$(kubectl get endpoints kubernetes \
  -o jsonpath='{.subsets[0].addresses[0].ip}')"
KUBE_API_PORT="$(kubectl get endpoints kubernetes \
  -o jsonpath='{.subsets[0].ports[0].port}')"
[ -n "$KUBE_API_IP" ] || { echo "FAIL: kubernetes endpoint unresolved" >&2; exit 1; }
echo "[smoke] kubernetes API endpoint is ${KUBE_API_IP}:${KUBE_API_PORT}"

# `persistence.enabled=false`: this is a disposable cluster that exists for
# one run, so there is no agent identity here worth surviving anything — the
# chart's default claim would only add a volume for this smoke to wait on,
# and waiting on storage is not what this test was written to prove.
#
# Persistence is proved where it means something: the chart's own default,
# the production render contract, the agent validator's claim check, and
# TestIdentitySurvivesProcessRestart.
render_chart() {
  helm template smoke "$REPO_ROOT/deploy/agent" \
    --namespace "$NAMESPACE" \
    --set clusterId="$CLUSTER_ID" \
    --set clusterName=cluster-a \
    --set persistence.enabled=false \
    --set apiBaseUrl="https://${HOST_GATEWAY_IP}:${INTERNAL_PORT}" \
    --set image.repository=drake-cluster-agent \
    --set image.devTag=smoke \
    --set image.pullPolicy=Never \
    --set serverCA.existingSecret=drake-agent-server-ca \
    --set enrollmentToken.existingSecret=drake-agent-enrollment \
    --set networkPolicy.apiEndpointCIDR="${HOST_GATEWAY_IP}/32" \
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
