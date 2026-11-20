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
- Deletes mark `missing`, never erase at once — history survives reconciles.
  A missing row leaves after its retention (below), never sooner.
- Duplicate deliveries are no-ops; replaying a page or event batch cannot
  double-apply.

## Retention of resources that left the cluster

A resource that disappears is marked `missing` and stays visible under the
inventory screen's "missing" filter. It leaves the projection after:

| Kind | Retention | Setting |
|---|---|---|
| `Event` | 24 h (Kubernetes itself keeps events about an hour) | `DRAKE_AGENT_MISSING_EVENT_RETENTION_HOURS` |
| everything else | 168 h | `DRAKE_AGENT_MISSING_RETENTION_HOURS` |

A row a service binding resolves to is kept regardless, so a binding whose
workload was deleted keeps saying so. Active rows are never touched.

The maintenance pass that runs at every snapshot start removes at most
`DRAKE_AGENT_MISSING_PRUNE_ROWS_PER_PASS` (20,000) rows, in transactions of
`DRAKE_AGENT_CLEANUP_BATCH_ROWS`. Production refuses retentions below 24 h,
or 1 h for events.

### Draining a backlog once, and giving the disk back

Before retention existed nothing left the table (drake-prod: 4.3 M missing
rows, 3.4 GB, on 2026-11-20). The maintenance pass drains such a backlog in a
few days. To do it at once, from inside an API pod, after the release that
carries retention is rolled out:

```bash
# 0. Backup first (see STANDARD_KUBERNETES_DEPLOYMENT.md): pg_dump --format=custom + sha256.
kubectl -n drake-prod exec deploy/drake-api -- \
  python -m drake_api.agents.prune_inventory_cli --cluster-ref duosis-prod-1 --dry-run
kubectl -n drake-prod exec deploy/drake-api -- \
  python -m drake_api.agents.prune_inventory_cli --cluster-ref duosis-prod-1
kubectl -n drake-prod exec deploy/drake-api -- \
  python -m drake_api.agents.prune_inventory_cli --cluster-ref duosis-prod-1 --reclaim
```

The drain runs the maintenance function itself: same retention, same
binding guard, same lock, small transactions. Deleting rows does not shrink
the file; `--reclaim` rewrites the table with `VACUUM (FULL, ANALYZE)`. That
holds an exclusive lock on `inventory_resources` for the rewrite, which is
proportional to the rows that remain (tens of thousands: seconds). Agent
ingest and inventory reads wait meanwhile. It refuses while rows past
retention remain. After that, autovacuum keeps the table at its working
size; no further rewrite is needed.
