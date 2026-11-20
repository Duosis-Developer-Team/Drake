"""Drain the missing-resource backlog once, from inside the running API image.

    python -m drake_api.agents.prune_inventory_cli --cluster-ref duosis-prod-1 --dry-run
    python -m drake_api.agents.prune_inventory_cli --cluster-ref duosis-prod-1
    python -m drake_api.agents.prune_inventory_cli --cluster-ref duosis-prod-1 --reclaim

Why this exists: until the retention existed, nothing ever left
`inventory_resources`, and drake-prod accumulated millions of rows for
resources that were long gone. The maintenance pass removes them a bounded
number at a time (a few days for that backlog). This runs the SAME function
(`prune_missing_resources`, same retention, same binding guard, same lock,
same small transactions) until nothing past retention is left, so the
backlog goes in minutes instead of days.

Deleting rows does not give disk back: Postgres keeps the file and reuses
the space. `--reclaim` rewrites the table with `VACUUM (FULL, ANALYZE)`,
which holds an exclusive lock on `inventory_resources` for as long as the
rewrite takes. Agent ingest and inventory reads wait during it. It is
proportional to the rows that remain (tens of thousands after a drain), so
seconds, but it is still a lock, and so it is a separate, explicit flag
and refuses to run while a backlog remains. Take a backup first.

It is not a database shell: no statement argument, nothing but this table.
"""

import argparse
import asyncio
import time

from sqlalchemy import text

from drake_api.agents.maintenance import prune_missing_resources
from drake_api.db import get_engine
from drake_api.settings import get_settings

# Rows per call; each call is still made of `agent_cleanup_batch_rows`
# transactions. Only sets how often progress is printed.
CHUNK = 50_000

_QUALIFYING = """
    SELECT count(*) FROM inventory_resources c
    WHERE c.cluster_id = :cluster_id
      AND c.lifecycle = 'missing'
      AND c.last_seen_at < now() - make_interval(hours => CASE
          WHEN c.kind = 'Event' THEN :event_hours ELSE :retention_hours END)
      AND NOT EXISTS (
          SELECT 1 FROM service_workload_bindings b
          WHERE b.cluster_id = c.cluster_id AND b.resolved_resource_uid = c.uid)
"""


async def _run(cluster_ref: str, dry_run: bool, reclaim: bool) -> int:
    settings = get_settings()
    engine = get_engine(settings)
    async with engine.connect() as connection:
        cluster = (
            await connection.execute(
                text("SELECT id FROM clusters WHERE cluster_ref = :ref"), {"ref": cluster_ref}
            )
        ).first()
    if cluster is None:
        print(f"refused: no cluster with cluster_ref {cluster_ref!r}")
        return 2
    cluster_id = cluster[0]
    params = {
        "cluster_id": cluster_id,
        "event_hours": settings.agent_missing_event_retention_hours,
        "retention_hours": settings.agent_missing_retention_hours,
    }

    async def qualifying() -> int:
        async with engine.connect() as connection:
            return int((await connection.execute(text(_QUALIFYING), params)).scalar_one())

    pending = await qualifying()
    print(
        f"{cluster_ref}: {pending} missing rows past retention "
        f"(events {settings.agent_missing_event_retention_hours} h, "
        f"others {settings.agent_missing_retention_hours} h; bound workloads kept)"
    )
    if dry_run:
        return 0

    removed = 0
    started = time.monotonic()
    while True:
        batch = await prune_missing_resources(engine, settings, cluster_id, max_rows=CHUNK)
        removed += batch
        print(f"  removed {removed} ({time.monotonic() - started:.0f} s)")
        if batch < CHUNK:
            break

    if reclaim:
        left = await qualifying()
        if left:
            print(f"refused to reclaim: {left} rows past retention are still there")
            return 3
        print("reclaiming disk: VACUUM (FULL, ANALYZE) inventory_resources")
        async with engine.connect() as connection:
            autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
            before = (
                await autocommit.execute(
                    text("SELECT pg_size_pretty(pg_total_relation_size('inventory_resources'))")
                )
            ).scalar_one()
            await autocommit.execute(text("VACUUM (FULL, ANALYZE) inventory_resources"))
            after = (
                await autocommit.execute(
                    text("SELECT pg_size_pretty(pg_total_relation_size('inventory_resources'))")
                )
            ).scalar_one()
        print(f"  inventory_resources: {before} -> {after}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove inventory rows for resources that left the cluster past retention."
    )
    parser.add_argument("--cluster-ref", required=True)
    parser.add_argument("--dry-run", action="store_true", help="count only, remove nothing")
    parser.add_argument(
        "--reclaim",
        action="store_true",
        help="after draining, rewrite the table to give disk back (exclusive lock, seconds)",
    )
    args = parser.parse_args()
    if args.dry_run and args.reclaim:
        parser.error("--dry-run and --reclaim are exclusive")
    return asyncio.run(_run(args.cluster_ref, args.dry_run, args.reclaim))


if __name__ == "__main__":
    raise SystemExit(main())
