#!/usr/bin/env bash
# The commands each CI job runs — defined once, so a developer can run
# exactly what CI will run.
#
# CI used to spell these out as workflow steps, which meant the local
# equivalent was whatever you remembered to type. That is how CI ends up
# being the first place a change is debugged: the loop is eight minutes
# long and the local command was subtly different. Both sides now call this
# script, so "it passed locally" and "it passed in CI" mean the same thing.
#
# Usage:
#   scripts/ci_job.sh <job>
#
# Jobs: contracts | web | python | chart | go-agent | integration
#
# `integration` takes its test selection from DRAKE_INTEGRATION_SELECTION
# (space-separated paths). Empty means every integration test.
set -euo pipefail

cd "$(dirname "$0")/.."

JOB="${1:-}"

step() { printf '\n\033[1m[%s]\033[0m %s\n' "$JOB" "$*"; }
run() { step "$*"; "$@"; }

case "$JOB" in
  contracts)
    run pnpm install --frozen-lockfile
    run pnpm --filter @drake/contracts lint
    run pnpm --filter @drake/contracts typecheck
    run pnpm --filter @drake/contracts test
    ;;

  web)
    run pnpm install --frozen-lockfile
    run pnpm --filter @drake/web lint
    run pnpm --filter @drake/web typecheck
    # Includes the static browser provider-access guard test.
    run pnpm --filter @drake/web test
    run pnpm --filter @drake/web build
    ;;

  python)
    run uv sync --all-packages
    run uv run ruff format --check .
    run uv run ruff check .
    run uv run mypy apps/api/src apps/worker/src
    # The chart contract tests are deselected: they belong to the `chart`
    # job, which is selected by chart changes. Running them in both places
    # rendered the chart 149 times twice on every full suite.
    run uv run pytest -m "not integration" -q \
      --ignore=apps/api/tests/test_production_chart_unit.py \
      --ignore=apps/api/tests/test_agent_chart_unit.py
    # Does the production image contain the code the chart tells it to run?
    # It did not, once, and a production upgrade rolled back.
    run bash scripts/api_image_entrypoint_smoke.sh
    # Children die on success, failure, timeout and Ctrl+C — including
    # grandchildren, which is the case that actually leaked.
    run bash scripts/process_lifecycle_regression.sh
    ;;

  integration)
    # Integration tests skip themselves when these are unset, so a missing
    # variable does not fail the job — it produces a green run in which
    # nothing was tested. That is the one outcome worth refusing outright.
    if [ -z "${DRAKE_IT_DATABASE_URL:-}" ] || [ -z "${DRAKE_IT_REDIS_URL:-}" ]; then
      echo "DRAKE_IT_DATABASE_URL / DRAKE_IT_REDIS_URL are not set." >&2
      echo "Every integration test would skip and this job would pass having" >&2
      echo "run nothing. Start the local stack and export them:" >&2
      echo >&2
      echo "  docker compose -f deploy/local/docker-compose.yml up -d" >&2
      echo "  export DRAKE_IT_DATABASE_URL=postgresql+psycopg://drake:drake_local_only_dev@127.0.0.1:55432/drake" >&2
      echo "  export DRAKE_IT_REDIS_URL=redis://127.0.0.1:56379/0" >&2
      exit 2
    fi
    run uv sync --all-packages
    run bash scripts/start_fixture_prometheus.sh
    # Unquoted on purpose: this is a list of paths, and empty must expand to
    # nothing so pytest collects every integration test.
    local_selection="${DRAKE_INTEGRATION_SELECTION:-}"
    if [ -n "$local_selection" ]; then
      step "integration subset: $(printf '%s' "$local_selection" | wc -w | tr -d ' ') suites"
    else
      step "integration: every suite (no narrowing)"
    fi
    # shellcheck disable=SC2086
    run uv run pytest -m integration -q $local_selection
    ;;

  chart)
    run uv sync --all-packages
    run bash deploy/drake/validate.sh
    run bash deploy/agent/validate.sh
    run bash deploy/dev/observability/validate.sh
    run uv run --directory apps/api pytest \
      tests/test_production_chart_unit.py tests/test_agent_chart_unit.py -q
    ;;

  go-agent)
    step "gofmt"
    if [ -n "$(cd apps/cluster-agent && gofmt -l .)" ]; then
      echo "gofmt found unformatted files:" >&2
      (cd apps/cluster-agent && gofmt -l .) >&2
      exit 1
    fi
    (cd apps/cluster-agent && run go vet ./...)
    (cd apps/cluster-agent && run go build ./...)
    (cd apps/cluster-agent && run go test -race ./...)
    ;;

  *)
    echo "usage: $0 {contracts|web|python|integration|chart|go-agent}" >&2
    exit 2
    ;;
esac

printf '\n\033[1m[%s] OK\033[0m\n' "$JOB"
