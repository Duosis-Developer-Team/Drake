#!/usr/bin/env bash
# Start the local fixture Prometheus, tolerating a flaky registry.
#
# `docker compose up -d --wait prometheus` pulls two digest-pinned images.
# When the registry answers
#
#     Error response from daemon: received unexpected HTTP status: 500
#
# the whole e2e job goes red for a reason that has nothing to do with the
# commit. That is not a test result, and re-running the job by hand is not
# a fix — it just moves the retry to a human.
#
# So: bounded retries with backoff, and a hard failure when they are
# exhausted. What this deliberately does NOT do is swallow the error. A
# fixture Prometheus that never starts must fail the job, because the
# metrics scenarios would otherwise run against nothing and pass.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="deploy/local/docker-compose.yml"
SERVICE="prometheus"
ATTEMPTS="${DRAKE_FIXTURE_PROMETHEUS_ATTEMPTS:-3}"
BACKOFF="${DRAKE_FIXTURE_PROMETHEUS_BACKOFF:-5}"

for attempt in $(seq 1 "$ATTEMPTS"); do
  if docker compose -f "$COMPOSE_FILE" up -d --wait "$SERVICE"; then
    echo "[fixture-prometheus] up (attempt $attempt)"
    exit 0
  fi
  if [ "$attempt" -lt "$ATTEMPTS" ]; then
    echo "[fixture-prometheus] attempt $attempt failed; retrying in ${BACKOFF}s" >&2
    # Leave nothing half-started behind, or the next attempt inherits a
    # container in a state `--wait` will never call healthy.
    docker compose -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
    sleep "$BACKOFF"
  fi
done

echo "[fixture-prometheus] FAILED after ${ATTEMPTS} attempts" >&2
docker compose -f "$COMPOSE_FILE" ps >&2 || true
exit 1
