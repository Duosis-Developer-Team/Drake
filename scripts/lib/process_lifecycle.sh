#!/usr/bin/env bash
# Shared child-process lifecycle for Drake's smoke and stack scripts.
#
# Why this exists, precisely: `edge_routing_smoke.sh` started the web app as
#
#     (cd apps/web && pnpm -s start --port "$WEB_PORT") &
#     WEB_PID=$!
#
# and cleaned up with `kill "$WEB_PID"`. `$!` is the SUBSHELL, not `pnpm`,
# and killing `pnpm` does not kill the `next-server` it spawns. So the
# cleanup reported success while a Next production server kept port 13100
# for two days, re-parented to init. Killing a pid is not killing a process.
#
# Everything here therefore works on process TREES, and every kill is
# TERM -> bounded wait -> KILL. The registry is append-only and cleanup is
# idempotent, so running it twice (EXIT after INT, say) is a no-op the
# second time rather than an error that masks the real test result.

# Guard against double-sourcing: re-sourcing must not wipe a live registry.
if [ -n "${__DRAKE_LIFECYCLE_LOADED:-}" ]; then
  return 0 2>/dev/null || true
fi
__DRAKE_LIFECYCLE_LOADED=1

__DRAKE_TRACKED_PIDS=""
__DRAKE_TRACKED_LABELS=""
# An ARRAY, not a newline-joined string. The string version split multi-line
# hooks on newlines and eval'd each fragment separately, so a hook written as
#
#     [ -n "$UP_COMPLETED" ] || {
#       k3d cluster delete "$CLUSTER_NAME"
#       rm -rf "$STACK_DIR"
#     }
#
# lost its guard to a syntax error and then ran the delete and the rm
# UNCONDITIONALLY. It destroyed the thing it was meant to preserve, and
# reported the eval failure as an ignorable warning while doing it.
__DRAKE_CLEANUP_HOOKS=()
__DRAKE_CLEANUP_DONE=""
# Seconds to wait for a TERM to be honoured before escalating. Bounded on
# purpose: an unbounded wait in a cleanup path turns a failing test into a
# hanging one, which is strictly worse.
DRAKE_LIFECYCLE_GRACE="${DRAKE_LIFECYCLE_GRACE:-5}"

# Record a child so cleanup can find it. Label is for diagnostics only.
lifecycle_track() {
  local pid="$1" label="${2:-child}"
  [ -n "$pid" ] || return 0
  __DRAKE_TRACKED_PIDS="$__DRAKE_TRACKED_PIDS $pid"
  __DRAKE_TRACKED_LABELS="$__DRAKE_TRACKED_LABELS $pid=$label"
}

# Register arbitrary extra cleanup (a k3d cluster, a compose stack, a temp
# dir). Runs after the tracked processes are gone, in registration order.
lifecycle_on_cleanup() {
  [ -n "${1:-}" ] || return 0
  __DRAKE_CLEANUP_HOOKS+=("$1")
}

__lifecycle_descendants() {
  # Depth-first, children before parents, so a parent cannot respawn or
  # re-parent a child we are about to signal.
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    __lifecycle_descendants "$child"
  done
  printf '%s\n' "$pid"
}

# TERM the whole tree, wait a bounded time, then KILL whatever is left.
lifecycle_kill_tree() {
  local root="$1"
  [ -n "$root" ] || return 0
  kill -0 "$root" 2>/dev/null || return 0

  local pids
  pids="$(__lifecycle_descendants "$root")"

  local pid
  for pid in $pids; do kill -TERM "$pid" 2>/dev/null || true; done

  local waited=0
  while [ "$waited" -lt "$DRAKE_LIFECYCLE_GRACE" ]; do
    local alive=0
    for pid in $pids; do kill -0 "$pid" 2>/dev/null && alive=1; done
    [ "$alive" -eq 0 ] && return 0
    sleep 1
    waited=$((waited + 1))
  done

  # Re-enumerate: the grace period may have produced new grandchildren.
  for pid in $(__lifecycle_descendants "$root"); do
    kill -KILL "$pid" 2>/dev/null || true
  done
  return 0
}

# Idempotent. Safe to call from EXIT after it already ran from INT.
lifecycle_cleanup() {
  if [ -n "$__DRAKE_CLEANUP_DONE" ]; then
    return 0
  fi
  __DRAKE_CLEANUP_DONE=1

  local pid
  for pid in $__DRAKE_TRACKED_PIDS; do
    lifecycle_kill_tree "$pid"
  done

  # Reap, so the script does not exit leaving zombies behind. `wait` on an
  # already-reaped pid is an error we do not care about.
  for pid in $__DRAKE_TRACKED_PIDS; do
    wait "$pid" 2>/dev/null || true
  done

  # Hook failures must never overwrite the test's own exit status: a
  # cleanup that cannot delete a cluster is a warning, not a test result.
  local hook
  # `${a[@]+...}` so an empty array is not an unbound-variable error under
  # `set -u`, which every caller uses.
  for hook in ${__DRAKE_CLEANUP_HOOKS[@]+"${__DRAKE_CLEANUP_HOOKS[@]}"}; do
    [ -n "$hook" ] || continue
    # Each hook is eval'd WHOLE, so a multi-line hook keeps its control flow.
    eval "$hook" || echo "[lifecycle] cleanup hook failed (ignored): ${hook%%$'\n'*}" >&2
  done
  return 0
}

# Install on EXIT *and* on the signals a developer actually sends.
#
# The handlers `exit` with the conventional 128+signal rather than
# re-raising. Re-raising looks more correct and is not: `trap - INT`
# restores the INHERITED disposition, and bash starts background jobs with
# SIGINT already ignored, so `kill -INT $$` from a backgrounded script is a
# no-op and the run reports a silent 0. An interrupted test claiming
# success is the one outcome this file exists to prevent.
#
# EXIT still fires afterwards; lifecycle_cleanup is idempotent, so the
# second call does nothing.
lifecycle_install_traps() {
  trap 'lifecycle_cleanup' EXIT
  trap 'lifecycle_cleanup; exit 130' INT
  trap 'lifecycle_cleanup; exit 143' TERM
}

# Start a command in the background, tracked, in one step.
lifecycle_spawn() {
  local label="$1"; shift
  "$@" &
  local pid=$!
  lifecycle_track "$pid" "$label"
  printf '%s' "$pid"
}
