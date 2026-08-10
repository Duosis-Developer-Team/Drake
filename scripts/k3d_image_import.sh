#!/usr/bin/env bash
# Import a local image into a k3d cluster, and PROVE it landed.
#
# `k3d image import` exits 0 even when it did not import anything. Both
# smokes trusted that exit code, so a failed import looked like a success
# and the next thing anyone saw was `ErrImageNeverPull` two minutes later,
# from a pod that could not pull because `pullPolicy: Never` is exactly
# what an imported image is for.
#
# So this does not trust the exit code. It asks every node whether the
# image is actually in its containerd store, and only then returns.
#
#   k3d_image_import <image:tag> <cluster>
set -euo pipefail

IMAGE="${1:?image reference required}"
CLUSTER="${2:?cluster name required}"
MAX_ATTEMPTS="${K3D_IMPORT_ATTEMPTS:-3}"
BACKOFF_SECONDS="${K3D_IMPORT_BACKOFF:-5}"

# A missing local image would otherwise look exactly like a failed import.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[import] FAIL: $IMAGE does not exist locally; nothing to import" >&2
  exit 1
fi
LOCAL_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}')"
echo "[import] $IMAGE ($LOCAL_ID) -> cluster $CLUSTER"

nodes() {
  # Agent and server nodes both run workloads; the serverlb never does.
  k3d node list --no-headers 2>/dev/null |
    awk -v c="$CLUSTER" '$2 ~ /server|agent/ && $0 ~ c { print $1 }'
}

present_everywhere() {
  local node found=0
  for node in $(nodes); do
    found=1
    if ! docker exec "$node" ctr -n k8s.io images ls -q 2>/dev/null |
        grep -qxF "docker.io/library/$IMAGE" &&
       ! docker exec "$node" ctr -n k8s.io images ls -q 2>/dev/null |
        grep -qxF "$IMAGE"; then
      return 1
    fi
  done
  # No nodes at all is not "present everywhere".
  [ "$found" -eq 1 ]
}

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  # Already there — an earlier attempt that reported failure may still have
  # worked, and re-importing would be pointless rather than harmful.
  if present_everywhere; then
    echo "[import] verified on: $(nodes | tr '\n' ' ')"
    exit 0
  fi
  OUTPUT="$(k3d image import "$IMAGE" -c "$CLUSTER" 2>&1)" && STATUS=0 || STATUS=$?
  if present_everywhere; then
    echo "[import] verified on: $(nodes | tr '\n' ' ') (attempt $attempt)"
    exit 0
  fi
  echo "[import] attempt $attempt/$MAX_ATTEMPTS did not land the image (exit $STATUS)" >&2
  echo "$OUTPUT" | tail -5 >&2
  [ "$attempt" -lt "$MAX_ATTEMPTS" ] && sleep "$BACKOFF_SECONDS"
done

echo "[import] FAIL: $IMAGE is not in the containerd store of every node" >&2
for node in $(nodes); do
  echo "--- $node images:" >&2
  docker exec "$node" ctr -n k8s.io images ls -q 2>&1 | head -10 >&2 || true
done
exit 1
