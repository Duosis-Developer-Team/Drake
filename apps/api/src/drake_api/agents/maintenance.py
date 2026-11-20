"""Bounded inventory maintenance (ADR-0017's bounded completion window).

Enforces, in small batches under a per-cluster advisory lock:
- pending snapshots time out (TTL) and are discarded, never applied;
- pending snapshots beyond the per-cluster cap are discarded oldest-first;
- staging rows of non-pending snapshots are removed;
- completed/discarded snapshot METADATA keeps a bounded history
  (the applied snapshot is always retained);
- change events respect both an age and a per-cluster row bound;
- resources that left the cluster (`lifecycle = 'missing'`) leave the
  projection after a retention window, a bounded number per pass.

Active rows of the projection (`inventory_resources`) are never touched
here; only rows already marked missing are, and only once they are older
than the retention. Cleanup failures degrade housekeeping only, never
what users see. Every
step commits separately and is idempotent, so overlapping runs and
retries are safe; the advisory lock keeps concurrent runs from racing.
"""

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.settings import Settings

logger = logging.getLogger(__name__)


async def run_inventory_maintenance(
    engine: AsyncEngine, settings: Settings, cluster_id: uuid.UUID
) -> dict[str, int]:
    """One bounded maintenance pass for one cluster. Returns counters for
    tests/observability; failures are logged, isolated per step, and never
    propagate into the ingest path that scheduled the pass."""
    results = {
        "expired_snapshots": 0,
        "capped_snapshots": 0,
        "staging_rows_removed": 0,
        "snapshots_pruned": 0,
        "change_events_removed": 0,
        "missing_pruned": 0,
    }
    steps = (
        ("expired_snapshots", _discard_expired_pending),
        ("capped_snapshots", _cap_pending_snapshots),
        ("staging_rows_removed", _drain_dead_staging),
        ("snapshots_pruned", _prune_snapshot_history),
        ("change_events_removed", _bound_change_events),
        ("missing_pruned", _prune_missing_resources),
    )
    for key, step in steps:
        try:
            results[key] = await step(engine, settings, cluster_id)
        except Exception:
            logger.exception("inventory maintenance step failed", extra={"step": key})
    return results


async def _locked(connection: Any, cluster_id: uuid.UUID) -> None:
    # Transaction-scoped advisory lock: concurrent maintenance for the
    # same cluster serializes; other clusters are unaffected.
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('inv-maint:' || :cluster))"),
        {"cluster": str(cluster_id)},
    )


async def _discard_expired_pending(
    engine: AsyncEngine, settings: Settings, cluster_id: uuid.UUID
) -> int:
    async with engine.begin() as connection:
        await _locked(connection, cluster_id)
        expired = (
            await connection.execute(
                text(
                    """
                    UPDATE inventory_snapshots SET status = 'discarded'
                    WHERE cluster_id = :cluster_id AND status = 'pending'
                      AND started_at < now() - make_interval(secs => :ttl)
                    RETURNING id
                    """
                ),
                {"cluster_id": cluster_id, "ttl": settings.agent_snapshot_ttl_seconds},
            )
        ).all()
        if expired:
            await connection.execute(
                text(
                    """
                    UPDATE cluster_inventory_state SET pending_snapshot_id = NULL
                    WHERE cluster_id = :cluster_id
                      AND pending_snapshot_id = ANY(:ids)
                    """
                ),
                {"cluster_id": cluster_id, "ids": [row[0] for row in expired]},
            )
        return len(expired)


async def _cap_pending_snapshots(
    engine: AsyncEngine, settings: Settings, cluster_id: uuid.UUID
) -> int:
    async with engine.begin() as connection:
        await _locked(connection, cluster_id)
        capped = (
            await connection.execute(
                text(
                    """
                    UPDATE inventory_snapshots SET status = 'discarded'
                    WHERE id IN (
                        SELECT id FROM inventory_snapshots
                        WHERE cluster_id = :cluster_id AND status = 'pending'
                        ORDER BY started_at DESC
                        OFFSET :cap
                    )
                    RETURNING id
                    """
                ),
                {"cluster_id": cluster_id, "cap": settings.agent_max_pending_snapshots},
            )
        ).all()
        return len(capped)


async def _drain_dead_staging(
    engine: AsyncEngine, settings: Settings, cluster_id: uuid.UUID
) -> int:
    removed = 0
    while True:
        async with engine.begin() as connection:
            await _locked(connection, cluster_id)
            batch = (
                await connection.execute(
                    text(
                        """
                        DELETE FROM inventory_staging_resources
                        WHERE id IN (
                            SELECT staging.id
                            FROM inventory_staging_resources staging
                            JOIN inventory_snapshots snapshot
                              ON snapshot.id = staging.snapshot_id
                            WHERE snapshot.cluster_id = :cluster_id
                              AND snapshot.status != 'pending'
                            LIMIT :batch
                        )
                        RETURNING id
                        """
                    ),
                    {"cluster_id": cluster_id, "batch": settings.agent_cleanup_batch_rows},
                )
            ).all()
        removed += len(batch)
        if len(batch) < settings.agent_cleanup_batch_rows:
            return removed


async def _prune_snapshot_history(
    engine: AsyncEngine, settings: Settings, cluster_id: uuid.UUID
) -> int:
    async with engine.begin() as connection:
        await _locked(connection, cluster_id)
        pruned = (
            await connection.execute(
                text(
                    """
                    DELETE FROM inventory_snapshots
                    WHERE id IN (
                        SELECT snapshot.id FROM inventory_snapshots snapshot
                        WHERE snapshot.cluster_id = :cluster_id
                          AND snapshot.status != 'pending'
                          AND snapshot.id NOT IN (
                              SELECT applied_snapshot_id FROM cluster_inventory_state
                              WHERE cluster_id = :cluster_id
                                AND applied_snapshot_id IS NOT NULL
                          )
                        ORDER BY snapshot.started_at DESC
                        OFFSET :keep
                    )
                    RETURNING id
                    """
                ),
                {"cluster_id": cluster_id, "keep": settings.agent_snapshot_history_limit},
            )
        ).all()
        return len(pruned)


async def _bound_change_events(
    engine: AsyncEngine, settings: Settings, cluster_id: uuid.UUID
) -> int:
    removed = 0
    # Age bound first, then the per-cluster row bound (newest kept).
    while True:
        async with engine.begin() as connection:
            await _locked(connection, cluster_id)
            aged = (
                await connection.execute(
                    text(
                        """
                        DELETE FROM inventory_change_events
                        WHERE id IN (
                            SELECT id FROM inventory_change_events
                            WHERE cluster_id = :cluster_id
                              AND occurred_at < now() - make_interval(
                                  days => :retention_days)
                            LIMIT :batch
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "cluster_id": cluster_id,
                        "retention_days": settings.agent_change_event_retention_days,
                        "batch": settings.agent_cleanup_batch_rows,
                    },
                )
            ).all()
        removed += len(aged)
        if len(aged) < settings.agent_cleanup_batch_rows:
            break
    while True:
        async with engine.begin() as connection:
            await _locked(connection, cluster_id)
            over = (
                await connection.execute(
                    text(
                        """
                        DELETE FROM inventory_change_events
                        WHERE id IN (
                            SELECT id FROM inventory_change_events
                            WHERE cluster_id = :cluster_id
                            ORDER BY occurred_at DESC, id
                            OFFSET :keep LIMIT :batch
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "cluster_id": cluster_id,
                        "keep": settings.agent_change_event_row_limit,
                        "batch": settings.agent_cleanup_batch_rows,
                    },
                )
            ).all()
        removed += len(over)
        if len(over) < settings.agent_cleanup_batch_rows:
            return removed


async def _prune_missing_resources(
    engine: AsyncEngine, settings: Settings, cluster_id: uuid.UUID
) -> int:
    """The maintenance step: one bounded pass of `prune_missing_resources`."""
    return await prune_missing_resources(
        engine, settings, cluster_id, max_rows=settings.agent_missing_prune_rows_per_pass
    )


async def prune_missing_resources(
    engine: AsyncEngine,
    settings: Settings,
    cluster_id: uuid.UUID,
    *,
    max_rows: int,
    pause_seconds: float = 0.0,
) -> int:
    """Remove projection rows that left the cluster longer ago than the
    retention: events after a day, everything else after a week. Returns how
    many rows went, at most `max_rows`.

    What is never removed:
    - an active row: only rows already marked missing qualify;
    - a row a service binding resolves to. A bound workload that was
      deleted stays visible as missing for as long as the binding exists,
      so the binding keeps saying "your workload is gone" instead of
      falling back to "not reported yet".

    The outer WHERE repeats the qualifying conditions. A row the agent
    reports again between the sub-select and the delete is re-checked on
    its new version (Postgres re-evaluates the outer predicate after
    waiting on the row lock) and survives.

    Each batch is its own short transaction under the maintenance lock, so
    a long drain never holds a lock or a snapshot open for long. Batches
    take the oldest rows first through `ix_inventory_missing_since`
    (migration 0030), so a batch costs its own size, not a table scan.

    `pause_seconds` sleeps between batches. The maintenance pass does not
    need it (20 k rows at most); a drain of millions of rows uses it to
    leave the disk to the applications sharing the node."""
    removed = 0
    while removed < max_rows:
        batch_size = min(settings.agent_cleanup_batch_rows, max_rows - removed)
        async with engine.begin() as connection:
            await _locked(connection, cluster_id)
            batch = (
                await connection.execute(
                    text(
                        """
                        DELETE FROM inventory_resources r
                        WHERE r.id IN (
                            SELECT c.id FROM inventory_resources c
                            WHERE c.cluster_id = :cluster_id
                              AND c.lifecycle = 'missing'
                              -- The bound ix_inventory_missing_since can
                              -- range-scan; the CASE below then applies the
                              -- per-kind retention to what it returns.
                              AND c.last_seen_at < now() - make_interval(hours => :floor_hours)
                              AND c.last_seen_at < now() - make_interval(hours => CASE
                                  WHEN c.kind = 'Event' THEN :event_hours
                                  ELSE :retention_hours END)
                              AND NOT EXISTS (
                                  SELECT 1 FROM service_workload_bindings b
                                  WHERE b.cluster_id = c.cluster_id
                                    AND b.resolved_resource_uid = c.uid
                              )
                            ORDER BY c.last_seen_at
                            LIMIT :batch
                        )
                          AND r.lifecycle = 'missing'
                          AND r.last_seen_at < now() - make_interval(hours => CASE
                              WHEN r.kind = 'Event' THEN :event_hours
                              ELSE :retention_hours END)
                        RETURNING r.id
                        """
                    ),
                    {
                        "cluster_id": cluster_id,
                        "event_hours": settings.agent_missing_event_retention_hours,
                        "retention_hours": settings.agent_missing_retention_hours,
                        "floor_hours": min(
                            settings.agent_missing_event_retention_hours,
                            settings.agent_missing_retention_hours,
                        ),
                        "batch": batch_size,
                    },
                )
            ).all()
        removed += len(batch)
        if len(batch) < batch_size:
            break
        if pause_seconds > 0:
            await asyncio.sleep(pause_seconds)
    return removed
