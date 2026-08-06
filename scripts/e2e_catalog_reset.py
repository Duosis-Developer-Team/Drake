"""Deterministic E2E catalog reset (local/test only).

Integration suites leave their own catalog worlds behind on the shared
disposable database; E2E needs the canonical fixture world. This clears
catalog rows, their scope-bound grants, and non-organization scopes so the
fixture bootstrap always loads cleanly. Fail-closed outside local/test.
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    env = os.environ.get("DRAKE_ENV", "local")
    if env not in ("local", "test"):
        raise RuntimeError("e2e catalog reset is local/test only")
    engine = create_async_engine(os.environ["DRAKE_DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            for table in (
                # Sprint 4 agent/inventory tables reference clusters with
                # RESTRICT — cleared first (guarded: they may not exist yet
                # on a database migrated only part-way).
                "cluster_inventory_state",
                "inventory_change_events",
                "inventory_resources",
                "inventory_staging_resources",
                "inventory_snapshot_pages",
                "inventory_snapshots",
                "cluster_agents",
                "agent_enrollment_tokens",
                "integrations",
                "environment_services",
                "service_definitions",
                "environments",
                "project_owners",
                "projects",
                "clusters",
            ):
                exists = (
                    await connection.execute(text("SELECT to_regclass(:name)"), {"name": table})
                ).scalar_one()
                if exists is not None:
                    await connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608
            # Grants that pointed at non-org scopes (scope FKs are RESTRICT).
            await connection.execute(
                text(
                    """
                    DELETE FROM grants WHERE scope_id IN (
                        SELECT id FROM scopes WHERE scope_type != 'organization'
                    )
                    """
                )
            )
            await connection.execute(text("DELETE FROM scopes WHERE scope_type != 'organization'"))
    finally:
        await engine.dispose()
    sys.stdout.write("e2e catalog reset complete\n")


if __name__ == "__main__":
    asyncio.run(main())
