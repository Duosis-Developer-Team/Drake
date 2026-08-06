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

echo "[e2e-setup] migrations + bootstrap complete"
