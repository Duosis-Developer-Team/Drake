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
# The image's CONFIG digest — what `docker image inspect .Id` returns, and
# one of the two identities a running container can be traced back to.
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

# What a caller needs to prove a RUNNING container came from this import.
#
# Two different identities are involved and they are not interchangeable:
# the image CONFIG digest (docker's image id) and the MANIFEST digest
# containerd records for the reference. Kubernetes reports one or the other
# in `status.containerStatuses[].imageID` depending on runtime and how the
# image arrived, so both are published and the caller matches against
# either — as a full digest, never a prefix.
emit_identity() {
  [ -n "${K3D_IMPORT_IDENTITY_FILE:-}" ] || return 0
  local node digests=""
  for node in $(nodes); do
    digests="$digests $(docker exec "$node" ctr -n k8s.io images ls 2>/dev/null |
      awk -v ref="$IMAGE" '$1 ~ ref { print $3 }' | grep -oE 'sha256:[0-9a-f]{64}' | sort -u | tr '\n' ' ')"
  done
  {
    echo "reference=$IMAGE"
    echo "local_config_id=$LOCAL_ID"
    echo "node_digests=$(echo "$digests" | tr -s ' ' | sed 's/^ //;s/ $//')"
  } > "$K3D_IMPORT_IDENTITY_FILE"
  echo "[import] identity recorded for the caller"
}

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  # Already there — an earlier attempt that reported failure may still have
  # worked, and re-importing would be pointless rather than harmful.
  if present_everywhere; then
    echo "[import] verified on: $(nodes | tr '\n' ' ')"
    emit_identity
    exit 0
  fi
  OUTPUT="$(k3d image import "$IMAGE" -c "$CLUSTER" 2>&1)" && STATUS=0 || STATUS=$?
  if present_everywhere; then
    echo "[import] verified on: $(nodes | tr '\n' ' ') (attempt $attempt)"
    emit_identity
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
