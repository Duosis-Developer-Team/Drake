#!/usr/bin/env bash
# Production edge-routing smoke, on the disposable local stack.
#
# What this proves is the ONE thing the production edge contract turns on:
# a single origin where `/` reaches the web app and `/v1` reaches the API
# with its path INTACT — including nested routes and query strings.
#
# It runs the two application processes behind a bounded local reverse
# proxy that implements exactly the Ingress rules the chart renders
# (longest-prefix wins, no rewrite). It installs no ingress controller and
# touches no cluster: the chart's own structural contract is covered by
# deploy/drake/validate.sh and the chart contract tests, and this script
# covers the runtime half — that an unmodified /v1 path is what the API
# actually needs.
set -euo pipefail

PROXY_PORT="${DRAKE_EDGE_PROXY_PORT:-18080}"
API_PORT="${DRAKE_EDGE_API_PORT:-18000}"
WEB_PORT="${DRAKE_EDGE_WEB_PORT:-13100}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
  for pid in "${API_PID:-}" "${WEB_PID:-}" "${PROXY_PID:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

cd "$REPO_ROOT"

echo "[edge-smoke] starting the API"
DRAKE_ENV=local \
DRAKE_DATABASE_URL="${DRAKE_IT_DATABASE_URL:-postgresql+psycopg://drake:drake_local_only_dev@127.0.0.1:55432/drake}" \
DRAKE_REDIS_URL="${DRAKE_IT_REDIS_URL:-redis://127.0.0.1:56379/0}" \
  uv run uvicorn drake_api.main:create_app --factory \
  --host 127.0.0.1 --port "$API_PORT" --log-level warning &
API_PID=$!

echo "[edge-smoke] starting the web app"
(cd apps/web && DRAKE_DEPLOYMENT_MODE=production pnpm -s start --port "$WEB_PORT" >/dev/null 2>&1) &
WEB_PID=$!

echo "[edge-smoke] starting the edge proxy (the chart's Ingress rules)"
DRAKE_EDGE_PROXY_PORT="$PROXY_PORT" \
DRAKE_EDGE_API_PORT="$API_PORT" \
DRAKE_EDGE_WEB_PORT="$WEB_PORT" \
  uv run python scripts/edge_proxy.py &
PROXY_PID=$!

echo "[edge-smoke] waiting for the edge"
for _ in $(seq 1 60); do
  # /v1/me answers 401 unauthenticated, which is enough to know the API is
  # up and reachable through the proxy.
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PROXY_PORT}/v1/me")" = "401" ]; then
    break
  fi
  sleep 1
done

FAILURES=0
check() {
  local label="$1" expected="$2" url="$3"
  local status
  status="$(curl -s -o /dev/null -w '%{http_code}' "$url")"
  if [ "$status" = "$expected" ]; then
    printf '  ok       %s (%s)\n' "$label" "$status"
  else
    printf '  FAILED   %s (expected %s, got %s)\n' "$label" "$expected" "$status"
    FAILURES=$((FAILURES + 1))
  fi
}

echo "[edge-smoke] routing"
check "GET / reaches the web app"            200 "http://127.0.0.1:${PROXY_PORT}/"
check "GET /v1/me reaches the API"           401 "http://127.0.0.1:${PROXY_PORT}/v1/me"
check "nested /v1 route reaches the API"     401 "http://127.0.0.1:${PROXY_PORT}/v1/projects"
check "nested /v1 route with a query string" 401 "http://127.0.0.1:${PROXY_PORT}/v1/projects?limit=20"

# The API's health endpoints live OUTSIDE /v1, so the public edge routes
# them to the web app rather than the API. That is the intended shape:
# Kubernetes probes address the pod directly, and liveness/readiness are
# not something the internet needs to see.
check "health is not publicly routed to the API" 404 "http://127.0.0.1:${PROXY_PORT}/health/live"

# The decisive assertion: the API is addressed with the ORIGINAL path. A
# rewrite that stripped /v1 would turn /v1/health/live into /health/live,
# which the API also serves — so a status check alone would not notice.
# The proxy records exactly what it forwarded upstream.
echo "[edge-smoke] path preservation"
FORWARDED="$(curl -s "http://127.0.0.1:${PROXY_PORT}/__forwarded" || true)"
for expected in "/v1/me" "/v1/projects" "/v1/projects?limit=20"; do
  if printf '%s\n' "$FORWARDED" | grep -qxF "$expected"; then
    printf '  ok       the API was addressed with %s\n' "$expected"
  else
    printf '  FAILED   %s was not forwarded verbatim\n' "$expected"
    FAILURES=$((FAILURES + 1))
  fi
done
if printf '%s\n' "$FORWARDED" | grep -qE '^/(me|projects|health)'; then
  printf '  FAILED   a stripped path reached the API; /v1 was rewritten\n'
  FAILURES=$((FAILURES + 1))
else
  printf '  ok       no stripped path reached the API\n'
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "EDGE ROUTING SMOKE PASSED"
  exit 0
fi
echo "EDGE ROUTING SMOKE FAILED ($FAILURES checks)"
exit 1
