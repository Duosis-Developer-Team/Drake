#!/usr/bin/env bash
# Does the production API image actually contain the code the chart tells it
# to run?
#
# This exists because it did not. The chart started the internal agent
# listener with `python -m scripts.run_internal_agent_api`, and the API image
# copies `apps/api` and `packages` — not the repository root. Both listener
# containers exited immediately, readiness never passed, and a production
# `helm upgrade --atomic` rolled the release back.
#
# Every check that could have caught it was looking at the wrong thing: the
# chart contract tests asserted the ARGUMENTS, and the TLS integration test
# ran the runner from the working tree. Nothing ever asked the image.
#
# So this builds the real image from the real Dockerfile and runs the real
# command inside it, with NO bind mount and no source injected. If the module
# leaves the package, drops out of the build context, or the chart's command
# and the image's contents drift apart again, this fails.
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="drake-api:entrypoint-smoke"
# The platform the release is published for. Pinned because the image has to
# be asked the question on the architecture it will actually run on — and
# because `cryptography` aborts with SIGILL in an arm64 container on some
# developer machines, which would make this fail for a reason that has
# nothing to do with what it is testing.
PLATFORM="linux/amd64"
# The module the chart's internal listener containers run. Kept in one place
# so a future rename has to change it here too — and then be justified.
MODULE="drake_api.agents.run_internal_listener"

echo "[image-smoke] the chart's command, as rendered"
RENDERED="$(helm template drake deploy/drake \
  -f deploy/drake/values-drake-prod.yaml \
  --set internalAgentApi.enabled=true \
  --set internalAgentApi.tlsSecret=smoke-tls \
  --set internalAgentApi.caSecret=smoke-ca \
  --namespace drake-prod)"
if ! grep -q "$MODULE" <<<"$RENDERED"; then
  echo "FAIL: the rendered listener does not run $MODULE" >&2
  exit 1
fi
if grep -q "scripts.run_internal_agent_api" <<<"$RENDERED"; then
  echo "FAIL: the rendered listener still runs a repository-root script" >&2
  echo "      that script is not in the API image; the containers cannot start" >&2
  exit 1
fi

echo "[image-smoke] building the production API image"
docker build -q --platform "$PLATFORM" -f apps/api/Dockerfile -t "$IMAGE" . >/dev/null

echo "[image-smoke] running the chart's command inside the image"
# No volume, no bind mount, no PYTHONPATH from the host: whatever answers
# here came out of the image.
if ! OUTPUT="$(docker run --rm --network none --platform "$PLATFORM" --entrypoint python "$IMAGE" \
    -m "$MODULE" --help 2>&1)"; then
  echo "FAIL: the image cannot run $MODULE" >&2
  echo "$OUTPUT" | tail -20 >&2
  exit 1
fi

for expected in -- --surface --tls-cert --tls-key --client-ca; do
  if ! grep -q -- "$expected" <<<"$OUTPUT"; then
    echo "FAIL: $MODULE ran but does not accept $expected" >&2
    exit 1
  fi
done

# The public API's own entrypoint must still work: this change moved a
# listener, and moving one entrypoint must not break the other.
if ! docker run --rm --network none --platform "$PLATFORM" --entrypoint python "$IMAGE" \
    -c "import drake_api.main" >/dev/null 2>&1; then
  echo "FAIL: the image can no longer import the public API application" >&2
  exit 1
fi

docker image rm -f "$IMAGE" >/dev/null 2>&1 || true
echo "[image-smoke] OK: the image runs the command the chart gives it"
