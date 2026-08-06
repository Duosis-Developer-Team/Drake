# ADR-0017 — Snapshot/watch ordering, dedupe, and atomic inventory projection

Status: accepted (Sprint 4)
Builds on: ADR-0011 (state semantics), ADR-0014 (catalog authority).

## Context

Kubernetes inventory arrives from an unreliable, restartable agent over an
unreliable network. The projection users see must never be assembled from
a torn snapshot, never regress to older data, and never present gaps as
health.

## Decision

### 1. Snapshot protocol: begin → pages → complete, atomically applied

- `begin` registers a snapshot (agent-supplied `snapshot_uid`, unique per
  cluster) and **touches nothing** in the current projection.
- Pages land in a staging table; page numbers are unique per snapshot, so
  duplicate pages are idempotent no-ops; per-page and total resource/page
  budgets are enforced fail-closed.
- `complete` verifies the staged page SET, not counters alone: page
  numbers must be exactly `1..total_pages` (no holes, no extras) and the
  DISTINCT staged resources must match the declared total, so duplicate
  UIDs across pages cannot inflate it. A replayed page is idempotent only
  when its content hash matches; the same page number with different
  content is a torn stream. A successful completion swaps the projection
  in ONE transaction: staged rows upsert the current inventory, resources
  absent from the snapshot flip to `missing` (never hard-deleted), change
  events are appended, and the snapshot is marked complete. Repeated
  `complete` for the same snapshot is idempotent.
- A per-cluster **writer/generation row** serializes every inventory
  write: one active writer (the most recently enrolled agent — a
  superseded agent can heartbeat but never write), a server-assigned
  monotonic generation per snapshot, and the applied generation. A new
  begin supersedes the pending snapshot; a superseded snapshot can never
  complete; a snapshot at or below the applied generation can never touch
  the projection; watch events bind to the applied generation only.
- Snapshots have a bounded completion window (typed settings with
  production floors): a timed-out snapshot is refused at complete and
  discarded by maintenance — the last good projection stays intact and
  its freshness makes the staleness visible. Staging of dead snapshots,
  snapshot metadata history, and change events all have enforced bounds,
  cleaned in advisory-locked batches that never touch the projection.

### 2. Watch events: idempotent, ordered, gap-aware

- The agent stamps every batch with a strictly monotonic sequence and
  persists the last ACKNOWLEDGED sequence next to its identity: restarts
  resume the chain, never re-base it blindly, and a crash between ACK and
  persist replays an idempotent message. The server records the last
  applied sequence per agent. Replays (≤ last applied) are acknowledged
  as no-ops; **gaps** on pages/completes/events are refused with a
  DURABLE `reconcile_required` (committed outside the refused request's
  rollback). A `snapshot_begin` is itself the reconcile action: it may
  jump the sequence forward, while a begin with a stale sequence is a
  no-op that can regress nothing. ResourceVersions are opaque strings
  used only for watch bookmarks, never compared numerically.
- Events normalize to add/update/missing transitions on the current
  projection; a delete marks `missing` (lifecycle), never a hard delete.
  Events belonging to a superseded snapshot generation cannot overwrite
  newer state (generation check on apply).

### 3. Agent-side correctness

The agent LISTs each resource family with bounded pages, records the list
resourceVersion, ships the snapshot, then WATCHes from that
resourceVersion — no window exists between list and watch. On disconnect
or `410 Gone` it never resumes blindly: exponential backoff with jitter,
then a bounded full reconcile (new snapshot). The outbound queue is
bounded; when the API is unreachable long enough to overflow, the agent
drops intermediate events, marks itself reconcile-required, and performs
a full snapshot on reconnect — data loss is surfaced as staleness, never
hidden. Context cancellation tears down every watch, HTTP body, and
goroutine (no orphans; asserted with `-race` tests).

### 4. Freshness is separate from health

Agent connectivity (heartbeat), inventory freshness (last completed
snapshot/event age), and workload health are independent axes. A
heartbeat alone never makes inventory fresh; a fresh inventory of broken
workloads is fresh-but-unhealthy; stale data is never presented as
healthy.

## Consequences

- The projection is always a consistent cut: either the previous complete
  snapshot plus contiguous events, or the new snapshot — never a mixture.
- Storage carries a staging copy during ingest (bounded by snapshot
  limits) — an acceptable cost for atomicity.
- Server-side idempotency makes the single-replica agent safe to restart
  at any moment (crash-only design); a second replica remains a separate
  future decision and requires no Lease write permission today.
