#!/usr/bin/env bash
# Do the internal agent listeners actually START?
#
# Three production rollouts failed on that question, each on a different
# defect, each discovered only after `helm upgrade --atomic` had rolled the
# release back:
#
#   1. the runner module was not in the API image
#   2. the containers lacked the env `validate_runtime_security()` demands
#   3. the CA Secret was mounted 0400 root:root and the process runs as 65532
#
# Every one of them renders cleanly. `helm template` cannot see a missing
# module, an unset environment variable, or a file mode the process cannot
# read — and the tests that existed asserted arguments and env NAMES.
#
# So this starts them. Real image, real Kubernetes Secret volumes, the same
# securityContext and defaultMode the production chart renders, as uid 65532,
# on a disposable k3d cluster. Then it talks to both listeners over TLS.
#
# What it deliberately does NOT do: relax anything to pass. No root, no
# privileged container, no world-readable private key, no mocked startup, no
# sleep standing in for readiness.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${DRAKE_LISTENER_SMOKE_CLUSTER:-drake-listener-smoke}"
NAMESPACE="drake-listener-smoke"
IMAGE_TAG="drake-api:listener-smoke"
WORK_DIR="$REPO_ROOT/.listener-smoke"
ENROLL_PORT=8144
INGEST_PORT=8143
# The uid/gid the API image runs as, and the fsGroup the chart sets.
EXPECTED_UID=65532
EXPECTED_GID=65532

command -v k3d >/dev/null || { echo "k3d is required" >&2; exit 2; }
command -v kubectl >/dev/null || { echo "kubectl is required" >&2; exit 2; }
command -v helm >/dev/null || { echo "helm is required" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 2; }

# The release is published for linux/amd64 and this test asks a question
# only that architecture can answer honestly: `cryptography` aborts with
# SIGILL inside an arm64 container on some developer machines, so the
# listener would die for a reason that has nothing to do with the chart.
# The k3d node runs the host architecture, so the host has to be amd64.
HOST_ARCH="$(uname -m)"
if [ "$HOST_ARCH" != "x86_64" ] && [ "$HOST_ARCH" != "amd64" ]; then
  echo "listener runtime smoke requires an amd64 host (found $HOST_ARCH):" >&2
  echo "  the k3d node runs the host architecture, and the application" >&2
  echo "  aborts in an arm64 container. CI runs this on amd64." >&2
  exit 3
fi

PF_PID=""
FAILED=0
fail() { echo "FAIL: $*" >&2; FAILED=1; }
ok() { echo "  OK   $*"; }

diagnose() {
  echo "--- diagnostics ---------------------------------------------" >&2
  kubectl -n "$NAMESPACE" get pods -o wide 2>&1 | tail -5 >&2 || true
  kubectl -n "$NAMESPACE" describe pod -l app.kubernetes.io/component=agent-gateway 2>&1 |
    grep -A6 -iE "events|state|reason" | tail -25 >&2 || true
  for c in enroll ingest; do
    echo "--- $c log:" >&2
    kubectl -n "$NAMESPACE" logs -l app.kubernetes.io/component=agent-gateway -c "$c" \
      --tail=25 2>&1 | tail -25 >&2 || true
  done
}

cleanup() {
  [ -n "$PF_PID" ] && kill "$PF_PID" >/dev/null 2>&1 || true
  # A cleanup that fails silently leaves a cluster and a private key behind.
  if ! k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1; then
    echo "WARNING: could not delete the disposable cluster $CLUSTER_NAME" >&2
  fi
  docker image rm -f "$IMAGE_TAG" >/dev/null 2>&1 || true
  if ! rm -rf "$WORK_DIR"; then
    echo "WARNING: ephemeral PKI was not removed from $WORK_DIR" >&2
  fi
}
trap cleanup EXIT

rm -rf "$WORK_DIR"; mkdir -p "$WORK_DIR"; chmod 700 "$WORK_DIR"
umask 077

echo "[listener-smoke] building the production API image"
docker build -q -f "$REPO_ROOT/apps/api/Dockerfile" -t "$IMAGE_TAG" "$REPO_ROOT" >/dev/null

echo "[listener-smoke] disposable k3d cluster"
k3d cluster delete "$CLUSTER_NAME" >/dev/null 2>&1 || true
k3d cluster create "$CLUSTER_NAME" --no-lb --wait --timeout 180s \
  --k3s-arg "--disable=traefik@server:0" >/dev/null
kubectl config use-context "k3d-$CLUSTER_NAME" >/dev/null
bash "$REPO_ROOT/scripts/k3d_image_import.sh" "$IMAGE_TAG" "$CLUSTER_NAME"

echo "[listener-smoke] ephemeral test PKI"
# Generated per run, inside a 0700 directory, deleted by the trap. Never
# committed, never uploaded, never printed.
SVC="drake-agent-gateway"
cat > "$WORK_DIR/ca.cnf" <<EOF
[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=Listener Smoke CA
[v3]
basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
EOF
openssl ecparam -name prime256v1 -genkey -noout -out "$WORK_DIR/server-ca.key" 2>/dev/null
openssl req -x509 -new -key "$WORK_DIR/server-ca.key" -sha256 -days 2 \
  -config "$WORK_DIR/ca.cnf" -out "$WORK_DIR/server-ca.crt" 2>/dev/null
openssl ecparam -name prime256v1 -genkey -noout -out "$WORK_DIR/agent-ca.key" 2>/dev/null
openssl req -x509 -new -key "$WORK_DIR/agent-ca.key" -sha256 -days 2 \
  -config "$WORK_DIR/ca.cnf" -out "$WORK_DIR/agent-ca.crt" 2>/dev/null

cat > "$WORK_DIR/server.cnf" <<EOF
[req]
distinguished_name=dn
prompt=no
[dn]
CN=$SVC.$NAMESPACE.svc.cluster.local
[v3]
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@san
[san]
DNS.1=$SVC
DNS.2=$SVC.$NAMESPACE
DNS.3=$SVC.$NAMESPACE.svc
DNS.4=$SVC.$NAMESPACE.svc.cluster.local
DNS.5=localhost
IP.1=127.0.0.1
EOF
openssl ecparam -name prime256v1 -genkey -noout -out "$WORK_DIR/server.key" 2>/dev/null
openssl req -new -key "$WORK_DIR/server.key" -config "$WORK_DIR/server.cnf" \
  -out "$WORK_DIR/server.csr" 2>/dev/null
openssl x509 -req -in "$WORK_DIR/server.csr" -CA "$WORK_DIR/server-ca.crt" \
  -CAkey "$WORK_DIR/server-ca.key" -CAcreateserial -days 2 -sha256 \
  -extfile "$WORK_DIR/server.cnf" -extensions v3 -out "$WORK_DIR/server.crt" 2>/dev/null

# A client identity signed by the Agent CA the ingest listener trusts, and
# one signed by a CA it does not.
for who in good rogue; do
  openssl ecparam -name prime256v1 -genkey -noout -out "$WORK_DIR/$who.key" 2>/dev/null
  openssl req -new -key "$WORK_DIR/$who.key" -subj "/CN=$who-agent" -out "$WORK_DIR/$who.csr" 2>/dev/null
done
openssl x509 -req -in "$WORK_DIR/good.csr" -CA "$WORK_DIR/agent-ca.crt" \
  -CAkey "$WORK_DIR/agent-ca.key" -CAcreateserial -days 2 -sha256 -out "$WORK_DIR/good.crt" 2>/dev/null
openssl x509 -req -in "$WORK_DIR/rogue.csr" -CA "$WORK_DIR/server-ca.crt" \
  -CAkey "$WORK_DIR/server-ca.key" -CAcreateserial -days 2 -sha256 -out "$WORK_DIR/rogue.crt" 2>/dev/null

echo "[listener-smoke] namespace and Secrets"
kubectl create namespace "$NAMESPACE" >/dev/null
kubectl -n "$NAMESPACE" create secret tls drake-agent-tls \
  --cert="$WORK_DIR/server.crt" --key="$WORK_DIR/server.key" >/dev/null
kubectl -n "$NAMESPACE" create secret generic drake-agent-ca \
  --from-file=ca.crt="$WORK_DIR/agent-ca.crt" --from-file=ca.key="$WORK_DIR/agent-ca.key" >/dev/null
# The application config Secret the chart references. Startup does not open
# a connection — the engine is created per request — so unreachable DSNs are
# enough to prove the listeners come up.
kubectl -n "$NAMESPACE" create secret generic drake-api-config \
  --from-literal=DRAKE_DATABASE_URL="postgresql+psycopg://smoke:smoke@127.0.0.1:5432/smoke" \
  --from-literal=DRAKE_REDIS_URL="redis://127.0.0.1:6379/0" >/dev/null

echo "[listener-smoke] applying the gateway exactly as production renders it"
# The real chart, the real production values, only the workload under test.
helm template drake "$REPO_ROOT/deploy/drake" \
  -f "$REPO_ROOT/deploy/drake/values-drake-prod.yaml" \
  --namespace "$NAMESPACE" \
  --set internalAgentApi.enabled=true \
  --set internalAgentApi.tlsSecret=drake-agent-tls \
  --set internalAgentApi.caSecret=drake-agent-ca \
  --set imagePullSecrets=null \
  | python3 -c '
import sys, yaml
keep = []
for doc in yaml.safe_load_all(sys.stdin):
    if not doc:
        continue
    name = doc["metadata"]["name"]
    if name == "drake-agent-gateway" and doc["kind"] in ("Deployment", "Service"):
        # imagePullPolicy Never: the image was imported, never pulled.
        if doc["kind"] == "Deployment":
            for c in doc["spec"]["template"]["spec"]["containers"]:
                # The ONLY thing changed from the production render: a
                # locally built image has no registry digest. Everything
                # else — securityContext, volumes, defaultMode, command,
                # args, env — is exactly what production installs.
                c["image"] = "drake-api:listener-smoke"
                c["imagePullPolicy"] = "Never"
        keep.append(doc)
print(yaml.safe_dump_all(keep))
' | kubectl -n "$NAMESPACE" apply -f - >/dev/null

if ! kubectl -n "$NAMESPACE" rollout status deployment/drake-agent-gateway --timeout=180s >/dev/null; then
  fail "the listeners never became Ready"
  diagnose
  exit 1
fi
POD="$(kubectl -n "$NAMESPACE" get pods -l app.kubernetes.io/component=agent-gateway \
  -o jsonpath='{.items[0].metadata.name}')"
ok "both listeners reached Ready"

echo "[listener-smoke] runtime identity and mounted file modes"
for c in enroll ingest; do
  IDS="$(kubectl -n "$NAMESPACE" exec "$POD" -c "$c" -- id -u 2>/dev/null):$(kubectl -n "$NAMESPACE" exec "$POD" -c "$c" -- id -g 2>/dev/null)"
  [ "$IDS" = "$EXPECTED_UID:$EXPECTED_GID" ] && ok "$c runs as $IDS" || fail "$c runs as $IDS, expected $EXPECTED_UID:$EXPECTED_GID"
  for f in /etc/drake/agent-ca/ca.crt /etc/drake/agent-ca/ca.key /etc/drake/agent-tls/tls.key; do
    MODE="$(kubectl -n "$NAMESPACE" exec "$POD" -c "$c" -- stat -L -c '%a' "$f" 2>/dev/null || echo MISSING)"
    case "$MODE" in
      440) ok "$c $f mode $MODE (group-readable, not world-readable)" ;;
      *4|*5|*6|*7) fail "$c $f mode $MODE is world-readable" ;;
      *) fail "$c $f mode $MODE" ;;
    esac
  done
  # Readable by the process, which is the whole point.
  if kubectl -n "$NAMESPACE" exec "$POD" -c "$c" -- head -c 1 /etc/drake/agent-ca/ca.key >/dev/null 2>&1; then
    ok "$c can read the CA material it needs"
  else
    fail "$c cannot read /etc/drake/agent-ca/ca.key"
  fi
done

echo "[listener-smoke] no startup defect of the three known classes"
LOGS="$(kubectl -n "$NAMESPACE" logs "$POD" -c enroll --tail=200 2>&1)$(kubectl -n "$NAMESPACE" logs "$POD" -c ingest --tail=200 2>&1)"
for defect in ModuleNotFoundError PermissionError trusted_proxy_count allowed_web_origins Traceback; do
  grep -q "$defect" <<<"$LOGS" && fail "startup log contains $defect" || ok "no $defect"
done
for leak in "BEGIN EC PRIVATE KEY" "BEGIN PRIVATE KEY" "BEGIN CERTIFICATE" "smoke@127.0.0.1"; do
  grep -qF "$leak" <<<"$LOGS" && fail "logs leak $leak" || true
done
ok "no key, certificate body or DSN in the logs"

echo "[listener-smoke] stability window"
sleep 20
RESTARTS="$(kubectl -n "$NAMESPACE" get pod "$POD" -o jsonpath='{range .status.containerStatuses[*]}{.restartCount} {end}')"
READY="$(kubectl -n "$NAMESPACE" get pod "$POD" -o jsonpath='{range .status.containerStatuses[*]}{.ready} {end}')"
[ "$(tr -d ' 0' <<<"$RESTARTS")" = "" ] && ok "restarts: $RESTARTS" || fail "containers restarted: $RESTARTS"
[ "$(tr -d ' ' <<<"$READY")" = "truetrue" ] && ok "still Ready after the window" || fail "readiness: $READY"

echo "[listener-smoke] exposure"
SVC_TYPE="$(kubectl -n "$NAMESPACE" get svc drake-agent-gateway -o jsonpath='{.spec.type}')"
[ "$SVC_TYPE" = "ClusterIP" ] && ok "Service is ClusterIP" || fail "Service is $SVC_TYPE"
NODEPORTS="$(kubectl -n "$NAMESPACE" get svc drake-agent-gateway -o jsonpath='{.spec.ports[*].nodePort}')"
[ -z "$NODEPORTS" ] && ok "no node ports" || fail "node ports allocated: $NODEPORTS"
EXTERNAL="$(kubectl -n "$NAMESPACE" get svc drake-agent-gateway -o jsonpath='{.spec.externalIPs}')"
[ -z "$EXTERNAL" ] && ok "no external IPs" || fail "external IPs: $EXTERNAL"
[ "$(kubectl -n "$NAMESPACE" get ingress -o name 2>/dev/null | wc -l | tr -d ' ')" = "0" ] && ok "no Ingress" || fail "an Ingress exists"

echo "[listener-smoke] TLS behaviour, over the wire"
kubectl -n "$NAMESPACE" port-forward "pod/$POD" "$ENROLL_PORT:$ENROLL_PORT" "$INGEST_PORT:$INGEST_PORT" >/dev/null 2>&1 &
PF_PID=$!
for _ in $(seq 1 40); do
  openssl s_client -connect "127.0.0.1:$ENROLL_PORT" -CAfile "$WORK_DIR/server-ca.crt" \
    </dev/null >/dev/null 2>&1 && break
  sleep 1
done

# Enrollment: server-authenticated TLS, no client certificate asked for.
if openssl s_client -connect "127.0.0.1:$ENROLL_PORT" -CAfile "$WORK_DIR/server-ca.crt" \
    -verify_return_error </dev/null 2>&1 | grep -q "Verify return code: 0"; then
  ok "enrollment 8144: TLS handshake with no client certificate, server verified"
else
  fail "enrollment 8144 did not complete a verified handshake"
fi

# Ingest without a usable client certificate must not answer.
#
# Measured with a REQUEST, not a handshake. Under TLS 1.3 the client
# certificate exchange happens after the client already considers the
# handshake finished, so `openssl s_client` reports success and the
# server's refusal only surfaces when data is actually exchanged. Asking
# "did a request get a response" is the question that matters anyway.
NO_CERT="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$WORK_DIR/server-ca.crt" \
  -X POST "https://127.0.0.1:$INGEST_PORT/internal/v1/agent/heartbeat" -d '{}' \
  -H 'content-type: application/json' --max-time 15 || echo 000)"
[ "$NO_CERT" = "000" ] && ok "ingest 8143: no client certificate gets no response" \
  || fail "ingest 8143 answered $NO_CERT without a client certificate"

ROGUE="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$WORK_DIR/server-ca.crt" \
  --cert "$WORK_DIR/rogue.crt" --key "$WORK_DIR/rogue.key" \
  -X POST "https://127.0.0.1:$INGEST_PORT/internal/v1/agent/heartbeat" -d '{}' \
  -H 'content-type: application/json' --max-time 15 || echo 000)"
[ "$ROGUE" = "000" ] && ok "ingest 8143: a certificate from an untrusted CA gets no response" \
  || fail "ingest 8143 answered $ROGUE for a certificate from an untrusted CA"

# And the Agent CA's own client certificate does get through the transport
# — the application then applies proof-of-possession, which is a different
# layer and not what this test is about.
GOOD="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$WORK_DIR/server-ca.crt" \
  --cert "$WORK_DIR/good.crt" --key "$WORK_DIR/good.key" \
  -X POST "https://127.0.0.1:$INGEST_PORT/internal/v1/agent/heartbeat" -d '{}' \
  -H 'content-type: application/json' --max-time 15 || echo 000)"
[ "$GOOD" != "000" ] && ok "ingest 8143: the Agent CA's certificate reaches the application ($GOOD)" \
  || fail "ingest 8143 refused a certificate its own CA issued"

echo "[listener-smoke] surface isolation, over the wire"
# /enroll must not exist on the ingest listener, and the enrolled-agent
# routes must not exist on the enrolment listener.
ENROLL_STRAY="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$WORK_DIR/server-ca.crt" \
  -X POST "https://127.0.0.1:$ENROLL_PORT/internal/v1/agent/heartbeat" -d '{}' \
  -H 'content-type: application/json' --max-time 15 || echo 000)"
[ "$ENROLL_STRAY" = "404" ] && ok "enrollment listener: /heartbeat is 404" || fail "enrollment listener answered $ENROLL_STRAY on /heartbeat"

INGEST_STRAY="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$WORK_DIR/server-ca.crt" \
  --cert "$WORK_DIR/good.crt" --key "$WORK_DIR/good.key" \
  -X POST "https://127.0.0.1:$INGEST_PORT/internal/v1/agent/enroll" -d '{}' \
  -H 'content-type: application/json' --max-time 15 || echo 000)"
[ "$INGEST_STRAY" = "404" ] && ok "ingest listener: /enroll is 404" || fail "ingest listener answered $INGEST_STRAY on /enroll"

if [ "$FAILED" -ne 0 ]; then
  diagnose
  echo "[listener-smoke] FAILED" >&2
  exit 1
fi
echo "[listener-smoke] OK: both listeners start, serve TLS, and stay up"
