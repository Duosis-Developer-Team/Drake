#!/usr/bin/env bash
# Prepare the disposable local stack for E2E: migrations + catalog seed +
# first Platform Owner for the fake OIDC test user. Local-only.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Local Compose default; CI overrides with its service-container URL.
: "${DRAKE_DATABASE_URL:=postgresql+psycopg://drake:drake_local_only_dev@127.0.0.1:55432/drake}"
export DRAKE_DATABASE_URL

cd "$REPO_ROOT/apps/api"
uv run alembic upgrade head

cd "$REPO_ROOT"
uv run python -m drake_api.rbac.bootstrap \
  --issuer "http://127.0.0.1:9556" \
  --subject "user-owner" \
  --display-name "Owner One"

# Deterministic fixture world: reset leftover catalog state, then load
# fixtures (local/test only, fail-closed elsewhere) and E2E grants.
uv run python scripts/e2e_catalog_reset.py
uv run python -m drake_api.catalog.bootstrap
uv run python scripts/e2e_grants.py
uv run python scripts/e2e_telemetry_config.py
# Throwaway GitHub App material for the fake provider (never committed).
DRAKE_E2E_GITHUB_WEBHOOK_SECRET="${DRAKE_E2E_GITHUB_WEBHOOK_SECRET:-e2e-local-webhook-secret}" \
  uv run python scripts/e2e_github_config.py

# The catalog reset truncates cluster_agents, so the server forgets every
# enrolled agent. The agent's local identity is the other half of that same
# reset: leaving it behind makes the agent present a certificate the server
# has never seen, and it will (correctly) refuse to re-enroll on its own.
# Only the disposable, gitignored stack directory is touched.
AGENT_STATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.e2e-agent/state"
if [ -d "$AGENT_STATE_DIR" ]; then
  echo "[e2e-setup] clearing disposable agent identity in .e2e-agent/state"
  rm -rf "${AGENT_STATE_DIR:?}"/bundles "${AGENT_STATE_DIR:?}"/current \
    "${AGENT_STATE_DIR:?}"/sequence "${AGENT_STATE_DIR:?}"/enrollment-token
fi

echo "[e2e-setup] migrations + bootstrap complete"
