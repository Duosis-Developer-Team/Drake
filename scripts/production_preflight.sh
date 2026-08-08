#!/usr/bin/env bash
# Read-only production preflight.
#
# Validates that a prepared Drake release COULD be applied. It never
# applies anything: no kubectl apply, no namespace, no Secret, no
# migration, no DNS change, no token mint, no GitHub call. Every check is
# a read, and every value it prints is a name or a boolean — never a
# secret value.
#
# The kube context is an explicit argument on purpose. Relying on whichever
# context happens to be current is how a command meant for staging runs
# against production.
#
# Usage:
#   scripts/production_preflight.sh --context <ctx> --namespace <ns> \
#       --values <values-production.yaml> [--chart deploy/drake]
#
# Exit non-zero if any required production prerequisite is missing.
set -uo pipefail

CONTEXT=""
NAMESPACE=""
VALUES=""
CHART="deploy/drake"
FAILURES=0
CHECKS=0

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --context) CONTEXT="${2:-}"; shift 2 ;;
    --namespace) NAMESPACE="${2:-}"; shift 2 ;;
    --values) VALUES="${2:-}"; shift 2 ;;
    --chart) CHART="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$CONTEXT" ] || { echo "--context is required (never the current context implicitly)" >&2; exit 2; }
[ -n "$NAMESPACE" ] || { echo "--namespace is required" >&2; exit 2; }
[ -n "$VALUES" ] || { echo "--values is required" >&2; exit 2; }
[ -f "$VALUES" ] || { echo "values file not found: $VALUES" >&2; exit 2; }

pass() { CHECKS=$((CHECKS + 1)); printf '  ok       %s\n' "$1"; }
warn() { CHECKS=$((CHECKS + 1)); printf '  note     %s\n' "$1"; }
bad()  { CHECKS=$((CHECKS + 1)); FAILURES=$((FAILURES + 1)); printf '  MISSING  %s\n' "$1"; }

# Read a value out of the values file without printing the file.
value_at() {
  python3 - "$VALUES" "$1" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1])) or {}
node = doc
for key in sys.argv[2].split("."):
    if not isinstance(node, dict) or key not in node:
        print(""); raise SystemExit
    node = node[key]
print("" if node is None else node)
PY
}

# kubectl, read-only, against the named context only.
kget() { kubectl --context "$CONTEXT" "$@" 2>/dev/null; }

echo "Drake production preflight"
echo "  context:   $CONTEXT"
echo "  namespace: $NAMESPACE"
echo "  values:    $VALUES"
echo

echo "[tooling]"
for tool in helm kubectl python3; do
  if command -v "$tool" >/dev/null 2>&1; then
    pass "$tool present ($("$tool" version --short 2>/dev/null | head -1 || "$tool" --version 2>&1 | head -1))"
  else
    bad "$tool is not installed"
  fi
done

echo
echo "[cluster reachability]"
if kget cluster-info >/dev/null; then
  pass "context '$CONTEXT' is reachable"
else
  bad "context '$CONTEXT' is not reachable (or does not exist)"
fi
if kget get namespace "$NAMESPACE" -o name >/dev/null; then
  pass "namespace '$NAMESPACE' exists"
else
  bad "namespace '$NAMESPACE' does not exist (create it out of band; this script will not)"
fi

echo
echo "[public edge]"
MODE="$(value_at deploymentMode)"
[ "$MODE" = "production" ] && pass "deploymentMode is production" || bad "deploymentMode must be 'production' (found '${MODE:-unset}')"

ORIGIN="$(value_at publicOrigin)"
HOST="$(value_at ingress.host)"
CLASS="$(value_at ingress.className)"
TLS_ENABLED="$(value_at ingress.tls.enabled)"
TLS_SECRET="$(value_at ingress.tls.secretName)"

case "$ORIGIN" in
  https://*) pass "publicOrigin uses https" ;;
  "") bad "publicOrigin is not set" ;;
  *) bad "publicOrigin must use https" ;;
esac
if [ -n "$HOST" ] && [ "https://$HOST" = "$ORIGIN" ]; then
  pass "publicOrigin and ingress.host agree ($HOST)"
else
  bad "publicOrigin and ingress.host must describe the same origin"
fi
case "$HOST" in
  *REPLACE_ME*|*"<"*|*"*"*|"") bad "ingress.host is a placeholder or wildcard" ;;
  *) pass "ingress.host is an exact hostname" ;;
esac

if [ -n "$CLASS" ]; then
  if kget get ingressclass "$CLASS" -o name >/dev/null; then
    pass "ingress class '$CLASS' exists in the cluster"
  else
    bad "ingress class '$CLASS' is not installed in the cluster"
  fi
else
  bad "ingress.className is not set"
fi

[ "$TLS_ENABLED" = "True" ] || [ "$TLS_ENABLED" = "true" ] && pass "TLS is enabled" || bad "ingress.tls.enabled must be true"
if [ -n "$TLS_SECRET" ]; then
  if kget get secret "$TLS_SECRET" -n "$NAMESPACE" -o name >/dev/null; then
    pass "TLS secret '$TLS_SECRET' exists"
  else
    bad "TLS secret '$TLS_SECRET' is not present in '$NAMESPACE' (provision the certificate first)"
  fi
else
  bad "ingress.tls.secretName is not set"
fi

echo
echo "[secret references]"
API_SECRET="$(value_at api.existingSecret)"
if [ -n "$API_SECRET" ]; then
  if kget get secret "$API_SECRET" -n "$NAMESPACE" -o name >/dev/null; then
    # Names only. Values are never read, printed or logged.
    pass "application secret '$API_SECRET' exists"
  else
    bad "application secret '$API_SECRET' is not present in '$NAMESPACE'"
  fi
else
  bad "api.existingSecret is not set"
fi

GITHUB_ENABLED="$(value_at github.enabled)"
GITHUB_SECRET="$(value_at github.existingSecret)"
if [ "$GITHUB_ENABLED" = "True" ] || [ "$GITHUB_ENABLED" = "true" ]; then
  if [ -n "$GITHUB_SECRET" ] && kget get secret "$GITHUB_SECRET" -n "$NAMESPACE" -o name >/dev/null; then
    pass "GitHub integration enabled with secret '$GITHUB_SECRET'"
  else
    bad "GitHub integration is enabled but its secret reference is missing"
  fi
else
  warn "GitHub integration is disabled — Drake starts without it, and no App is required yet"
fi

echo
echo "[images]"
for component in api web migration; do
  digest="$(value_at "$component.image.digest")"
  tag="$(value_at "$component.image.tag")"
  if [ -n "$tag" ]; then
    bad "$component sets image.tag; production deploys digests only"
  elif [ -z "$digest" ]; then
    bad "$component image is not digest-pinned"
  elif [ "${digest#sha256:}" = "$digest" ]; then
    bad "$component image.digest is not a sha256 digest"
  else
    pass "$component image is digest-pinned"
  fi
done

echo
echo "[chart]"
if helm lint "$CHART" -f "$VALUES" >/dev/null 2>&1; then
  pass "helm lint"
else
  bad "helm lint failed"
fi
RENDER="$(mktemp)"
trap 'rm -f "$RENDER"' EXIT
if helm template drake "$CHART" -f "$VALUES" --namespace "$NAMESPACE" > "$RENDER" 2>/dev/null; then
  pass "helm template renders"
  if grep -qE "rewrite-target|configuration-snippet|server-snippet" "$RENDER"; then
    bad "rendered ingress rewrites the path; the API owns /v1 unchanged"
  else
    pass "no path rewrite or configuration snippet is rendered"
  fi
  if grep -qE "type: (NodePort|LoadBalancer)" "$RENDER"; then
    bad "a public Service type is rendered; the Ingress is the only front door"
  else
    pass "web and API Services stay ClusterIP"
  fi
  if grep -qE "BEGIN [A-Z ]*PRIVATE KEY|ghs_[A-Za-z0-9]{20,}" "$RENDER"; then
    bad "rendered manifests contain credential-shaped material"
  else
    pass "rendered manifests carry no credential material"
  fi
else
  bad "helm template failed (a required production value is missing)"
fi

echo
echo "[network policy prerequisites]"
NS_SELECTOR="$(value_at networkPolicy.ingressControllerNamespaceSelector)"
[ -n "$NS_SELECTOR" ] && pass "ingress-controller namespace selector is configured" \
  || bad "networkPolicy.ingressControllerNamespaceSelector is required (default-deny would block the route)"
for cidr_key in databaseCIDR redisCIDR; do
  cidr="$(value_at "networkPolicy.$cidr_key")"
  case "$cidr" in
    ""|0.0.0.0/0|::/0) bad "networkPolicy.$cidr_key must be a specific CIDR" ;;
    *) pass "networkPolicy.$cidr_key is specific" ;;
  esac
done

echo
echo "[external dependencies]"
# Presence of the reference only. No connection is opened and no
# credential is read.
if kget get secret "$API_SECRET" -n "$NAMESPACE" -o jsonpath='{.data}' >/dev/null 2>&1; then
  for key in DRAKE_DATABASE_URL DRAKE_REDIS_URL DRAKE_OIDC_ISSUER; do
    if kget get secret "$API_SECRET" -n "$NAMESPACE" -o jsonpath="{.data.$key}" | grep -q .; then
      pass "$key is present in the application secret (value not read)"
    else
      bad "$key is missing from the application secret"
    fi
  done
else
  warn "application secret keys not verified (secret unreadable from this context)"
fi

echo
echo "-------------------------------------------------------------"
if [ "$FAILURES" -eq 0 ]; then
  echo "PREFLIGHT PASSED — $CHECKS checks, 0 missing prerequisites"
  echo "Nothing was applied. This says the release COULD be installed,"
  echo "not that it has been."
  exit 0
fi
echo "PREFLIGHT FAILED — $FAILURES of $CHECKS checks are missing prerequisites"
echo "Nothing was applied."
exit 1
