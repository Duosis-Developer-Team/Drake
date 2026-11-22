#!/usr/bin/env bash
# Connect this machine's Claude Code to Tracesis (Agent Visibility →
# Development): cost, tokens, sessions and what was done, per person and
# project. Run once per developer machine.
#
#   scripts/connect-claude-code.sh [github-login]
#
# It asks for the ingest token without echoing it (get it from a Tracesis
# administrator), backs up ~/.claude/settings.json, and adds the telemetry
# variables to its `env` block. Nothing else in the file changes.
#
# What reaches Tracesis: cost, token counts, tool names, durations, the
# repository's name. What does not: prompts, answers, tool inputs, file
# contents, your e-mail (removed at Tracesis's door).
set -euo pipefail

ENDPOINT="${TRACESIS_OTLP_ENDPOINT:-https://tracesis-84-247-180-173.sslip.io:30773/otlp}"
SETTINGS="${HOME}/.claude/settings.json"

# A Python that actually runs. On Windows `python3` is often only the
# Microsoft Store placeholder, which exists on PATH but prints "Python was
# not found" and fails; the real interpreter is `python` (or `py -3`).
# Found before anything is asked or written, so a missing Python changes
# nothing on disk.
PYTHON=""
for candidate in python3 python "py -3"; do
  if $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Python 3.8 or later is needed (tried python3, python, py -3)." >&2
  exit 2
fi

login="${1:-}"
if [ -z "$login" ]; then
  guess="$(gh api user -q .login 2>/dev/null || true)"
  read -r -p "Your GitHub login${guess:+ [$guess]}: " login
  login="${login:-$guess}"
fi
case "$login" in
  "" | *[!A-Za-z0-9-]*) echo "A GitHub login is letters, digits and dashes." >&2; exit 2 ;;
esac

read -r -s -p "Tracesis ingest token (input hidden): " token
echo
case "$token" in
  "" | *[!A-Za-z0-9]*) echo "That does not look like the ingest token." >&2; exit 2 ;;
esac

if command -v claude >/dev/null 2>&1; then
  version="$(claude --version 2>/dev/null | awk '{print $1}')"
  echo "Claude Code ${version:-unknown}; repository attribution needs 2.1.269 or later (claude update)."
fi

mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' >"$SETTINGS"
backup="${SETTINGS}.bak-$(date +%Y%m%d%H%M%S)"
cp "$SETTINGS" "$backup"
chmod 600 "$backup"  # a re-run's backup holds the token

TRACESIS_TOKEN="$token" TRACESIS_LOGIN="$login" TRACESIS_ENDPOINT="$ENDPOINT" \
  $PYTHON - "$SETTINGS" <<'PY'
import json, os, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
env = data.setdefault("env", {})
env.update({
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": os.environ["TRACESIS_ENDPOINT"],
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer " + os.environ["TRACESIS_TOKEN"],
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
    "OTEL_METRICS_INCLUDE_REPOSITORY": "true",
    "OTEL_RESOURCE_ATTRIBUTES": "tracesis.person=" + os.environ["TRACESIS_LOGIN"],
})
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.chmod(path, 0o600)
PY

echo "Done. New Claude Code sessions report to Tracesis as ${login}; the map updates within 15 minutes."
echo "Your earlier work: python3 scripts/export-claude-code-usage.py --login ${login} > claude-code-usage.json"
echo "(counts only; see docs/runbooks/CLAUDE_CODE_USAGE.md)"
