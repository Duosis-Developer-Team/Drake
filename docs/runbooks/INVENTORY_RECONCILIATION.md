# Runbook — Inventory Reconciliation

What `reconcile_required` means, why it appears, and what to do.

## The model (ADR-0017)

The agent LISTs everything (paged), captures resourceVersions, streams an
atomic snapshot (begin/pages/complete), then WATCHes from those versions —
no gap exists between list and watch. The server applies a completed
snapshot in ONE transaction; watch events extend it under a strictly
gapless sequence. Anything that breaks continuity — a sequence gap, a torn
snapshot, a watch `410 Gone`, an agent-side queue overflow — converges on
one state: `reconcile_required`, answered by a fresh full snapshot.

## Normal, self-healing occurrences

- Agent restart: the next `snapshot_begin` re-bases the sequence and runs
  a full snapshot. Expected, crash-only design.
- Watch disconnect / `410 Gone`: bounded backoff with jitter, then a full
  reconcile. Expected under API-server churn.
- Queue overflow during control-plane unreachability: intermediate events
  are dropped and honestly replaced by a full snapshot on reconnect. The
  UI shows `reconcile required` / `reconciling` during the window.

No action is needed unless the state persists.

## If `reconcile_required` persists

1. Cluster detail → is the agent `connected`? If not, follow
   AGENT_DISCONNECT first.
2. Agent logs: look for `sync cycle ended; scheduling full reconcile` with
   the underlying error (schema refusal, 413, 409 loops).
3. A repeated 422 refusal means the agent is shipping something the server
   refuses (e.g., after a version skew). Fix the version skew; the server
   is fail-closed on purpose — do not loosen ingest validation.
4. Verify snapshot progress in `inventory_snapshots` (status `pending` →
   `complete`; repeated `discarded` rows indicate torn streams — check
   body-size limits and network stability).

## Guarantees to rely on

- The visible projection is never a torn mixture; a failed snapshot leaves
  the previous good state intact and visibly stale.
- Deletes mark `missing`, never erase — history survives reconciles.
- Duplicate deliveries are no-ops; replaying a page or event batch cannot
  double-apply.
