#!/usr/bin/env bash
# Drake secret scanning — single source of truth for local and CI runs.
#
# Modes:
#   history  gitleaks over the full git history
#   tree     gitleaks over the tracked/staged working tree content, copied to
#            a clean temp dir so gitignored build output can neither mask
#            findings nor add unrelated noise (scan scope = what is or would
#            be committed)
#   canary   regression test: plant a temporary, non-committed,
#            high-confidence FAKE credential inside the negative-fixture
#            directory and require the scanner to detect it. Proves fixture
#            directories are NOT allowlisted. The value is never printed.
#   all      history + tree + canary (default)
#
# The scanner image is pinned by digest. No allowlists are passed beyond the
# repository's .gitleaks.toml (which contains none).

set -euo pipefail

GITLEAKS_IMAGE="ghcr.io/gitleaks/gitleaks@sha256:e1b35e12a8c6fa8901f060459cfb6b2fc4c484d3afbe3b029733a3bbfab07055"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-all}"

TREE_COPY_DIR="$REPO_ROOT/.secret-scan-tree"
CANARY_FILE="$REPO_ROOT/packages/contracts/fixtures/invalid/.secret-scan-canary.tmp.yaml"
# Runtime canaries proving the .gitleaksignore registry fingerprints do NOT
# blind the scanner to credential-shaped values in the very same JSON field
# shapes ("key": ..., "queryTemplateKey": ...).
CANARY_JSON_KEY="$REPO_ROOT/packages/contracts/fixtures/invalid/.secret-scan-canary-key.tmp.json"
CANARY_JSON_REGISTRY="$REPO_ROOT/packages/contracts/fixtures/invalid/.secret-scan-canary-registry.tmp.json"
CANARY_REPORT="$REPO_ROOT/.secret-scan-canary-report.json"

cleanup() {
  rm -rf "$TREE_COPY_DIR"
  rm -f "$CANARY_FILE" "$CANARY_JSON_KEY" "$CANARY_JSON_REGISTRY" "$CANARY_REPORT"
}
trap cleanup EXIT

run_gitleaks() {
  docker run --rm -v "$REPO_ROOT:/repo" "$GITLEAKS_IMAGE" "$@"
}

history_scan() {
  echo "[secret-scan] full git history"
  run_gitleaks git /repo --config /repo/.gitleaks.toml --redact --exit-code 1 --no-banner
}

tree_scan() {
  echo "[secret-scan] tracked/staged working tree"
  rm -rf "$TREE_COPY_DIR"
  mkdir -p "$TREE_COPY_DIR"
  # Copy exactly the tracked + staged file set.
  (cd "$REPO_ROOT" && git ls-files -z | xargs -0 tar cf "$TREE_COPY_DIR/.tree.tar" --)
  (cd "$TREE_COPY_DIR" && tar xf .tree.tar && rm .tree.tar)
  run_gitleaks dir /repo/.secret-scan-tree --config /repo/.gitleaks.toml --redact --exit-code 1 --no-banner
  rm -rf "$TREE_COPY_DIR"
}

canary_scan() {
  echo "[secret-scan] canary regression (fixtures directory must be scanned)"
  # Deliberately fake credential shapes, generated at runtime, never real,
  # never printed, never committed.
  # 1) classic cloud-key format in YAML (the original canary),
  # 2) credential-shaped value in a generic JSON "key" field,
  # 3) credential-shaped value in a registry-like "queryTemplateKey" field.
  # 2+3 prove the exact-fingerprint exemptions for registry identifiers do
  # NOT blind the scanner to real secrets in the same field shapes.
  # No pipes here: unbounded-source pipelines SIGPIPE under pipefail.
  local suffix entropy1 entropy2
  suffix="$(printf '%04X%04X%04X%04X' "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM")"
  entropy1="$(printf '%04x%04x%04x%04x%04x%04x%04x%04x' \
    "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM")"
  entropy2="$(printf '%04x%04x%04x%04x%04x%04x%04x%04x' \
    "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM" "$RANDOM")"
  printf 'canary: "%s%s"\n' "AKIA" "$suffix" > "$CANARY_FILE"
  printf '{"key": "%s%s"}\n' "AKIA" "$suffix" > "$CANARY_JSON_KEY"
  printf '{"queryTemplateKey": "sk%s%s"}\n' "$entropy1" "$entropy2" > "$CANARY_JSON_REGISTRY"

  rm -f "$CANARY_REPORT"
  if run_gitleaks dir /repo/packages/contracts/fixtures/invalid \
    --config /repo/.gitleaks.toml --redact --exit-code 1 --no-banner \
    --report-format json --report-path /repo/.secret-scan-canary-report.json \
    >/dev/null 2>&1; then
    echo "[secret-scan] CANARY FAILED: no planted credential was detected" >&2
    exit 1
  fi
  local plant
  for plant in .secret-scan-canary.tmp.yaml .secret-scan-canary-key.tmp.json \
    .secret-scan-canary-registry.tmp.json; do
    if ! grep -q "$plant" "$CANARY_REPORT"; then
      echo "[secret-scan] CANARY FAILED: $plant was NOT detected" >&2
      exit 1
    fi
  done
  rm -f "$CANARY_FILE" "$CANARY_JSON_KEY" "$CANARY_JSON_REGISTRY" "$CANARY_REPORT"
  echo "[secret-scan] canary OK: all three planted credentials were detected"

  echo "[secret-scan] canary follow-up: fixtures directory is clean without the plants"
  run_gitleaks dir /repo/packages/contracts/fixtures \
    --config /repo/.gitleaks.toml --redact --exit-code 1 --no-banner
}

case "$MODE" in
  history) history_scan ;;
  tree) tree_scan ;;
  canary) canary_scan ;;
  all)
    history_scan
    tree_scan
    canary_scan
    echo "[secret-scan] all modes passed"
    ;;
  *)
    echo "usage: $0 [history|tree|canary|all]" >&2
    exit 2
    ;;
esac
