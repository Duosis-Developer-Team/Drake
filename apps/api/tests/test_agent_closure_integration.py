"""Sprint 4 CTO-closure proofs (real PG + Redis).

Every durability assertion here re-reads the database on a FRESH
connection AFTER the HTTP response closed — response text alone proves
nothing about what a rolled-back transaction left behind.

Covered: durable sequence-gap refusals on page/complete/events; refused
payloads leaving zero staging/projection residue; recovery only via a
successful full snapshot; non-contiguous page sets; duplicate-UID totals;
old-snapshot/new-snapshot races; two-agent writer races; superseded
agents; snapshot TTL; bounded staging/history/change-event cleanup.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from drake_api.agents.maintenance import run_inventory_maintenance
from drake_api.db import dispose_engines
from harness_s1 import require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from test_agent_inventory_integration import (
    UID_POD,
    AgentDriver,
    ca_settings,
    enroll_agent,
    healthy_world,
    internal_client,
    resource,
)
from test_catalog_api_integration import seed_catalog_world
from test_catalog_persistence_integration import reset_catalog

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def migrated_db() -> None:
    settings = require_it_settings()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


@pytest.fixture
async def engine() -> Any:
    settings = require_it_settings()
    eng = create_async_engine(settings.database_url)
    await reset_catalog(eng)
    yield eng
    await eng.dispose()
    await dispose_engines()


async def _fresh_agent_state(engine: AsyncEngine, agent_id: str) -> str:
    """The durable inventory_state, read on a NEW connection."""
    fresh = create_async_engine(require_it_settings().database_url)
    try:
        async with fresh.connect() as connection:
            return str(
                (
                    await connection.execute(
                        text("SELECT inventory_state FROM cluster_agents WHERE id = :id"),
                        {"id": agent_id},
                    )
                ).scalar_one()
            )
    finally:
        await fresh.dispose()


async def _counts(engine: AsyncEngine, cluster_id: str) -> dict[str, int]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM inventory_resources
                         WHERE cluster_id = :cid),
                        (SELECT count(*) FROM inventory_staging_resources staging
                         JOIN inventory_snapshots snap ON snap.id = staging.snapshot_id
                         WHERE snap.cluster_id = :cid),
                        (SELECT count(*) FROM inventory_change_events
                         WHERE cluster_id = :cid)
                    """
                ),
                {"cid": cluster_id},
            )
        ).one()
    return {"projection": int(rows[0]), "staging": int(rows[1]), "events": int(rows[2])}


async def _seed_fresh_projection(
    driver: AgentDriver, resources: list[dict[str, Any]] | None = None
) -> str:
    snapshot_uid = str(uuidlib.uuid4())
    world = resources if resources is not None else healthy_world()
    assert (await driver.begin(snapshot_uid)).status_code == 200
    assert (await driver.page(snapshot_uid, 1, world)).status_code == 200
    done = await driver.complete(snapshot_uid, 1, len(world))
    assert done.json()["result"] == "applied", done.text
    return snapshot_uid


async def test_sequence_gaps_are_durable_on_every_path(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    settings = ca_settings(tmp_path)
    cluster_a = str(world["cluster_a"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)
        await _seed_fresh_projection(driver)
        baseline = await _counts(engine, cluster_a)

        # 1) PAGE gap: 409 AND durably reconcile_required after response.
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        gap_page = await driver.page(
            snapshot_uid, 1, [healthy_world()[0]], sequence=driver.sequence + 7
        )
        assert gap_page.status_code == 409
        assert await _fresh_agent_state(engine, agent_id) == "reconcile_required"
        # The refused payload left NOTHING behind (staging unchanged).
        after_gap = await _counts(engine, cluster_a)
        assert after_gap["projection"] == baseline["projection"]
        assert after_gap["staging"] == baseline["staging"]

        # Recover with a full snapshot; then 2) COMPLETE gap.
        await _seed_fresh_projection(driver)
        assert await _fresh_agent_state(engine, agent_id) == "fresh"
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        world_page = healthy_world()
        assert (await driver.page(snapshot_uid, 1, world_page)).status_code == 200
        gap_complete = await driver.post(
            "/internal/v1/agent/inventory/snapshot/complete",
            driver.base("snapshot_complete", sequence=driver.sequence + 9)
            | {
                "snapshot_uid": snapshot_uid,
                "total_pages": 1,
                "total_resources": len(world_page),
            },
        )
        assert gap_complete.status_code == 409
        assert await _fresh_agent_state(engine, agent_id) == "reconcile_required"

        # 3) While reconcile_required, watch events are refused.
        refused_events = await driver.events(
            [
                {
                    "event_id": str(uuidlib.uuid4()),
                    "change_type": "updated",
                    "resource": healthy_world()[0],
                }
            ]
        )
        assert refused_events.status_code == 409

        # Recover again; then WATCH gap.
        await _seed_fresh_projection(driver)
        gap_events = await driver.events(
            [
                {
                    "event_id": str(uuidlib.uuid4()),
                    "change_type": "updated",
                    "resource": healthy_world()[0],
                }
            ],
            sequence=driver.sequence + 11,
        )
        assert gap_events.status_code == 409
        assert await _fresh_agent_state(engine, agent_id) == "reconcile_required"

        # 4) ONLY a successful new full snapshot returns the state to fresh.
        heartbeat = await driver.heartbeat("fresh")
        assert heartbeat.status_code == 200
        assert await _fresh_agent_state(engine, agent_id) == "reconcile_required", (
            "a heartbeat must never clear reconcile_required"
        )
        await _seed_fresh_projection(driver)
        assert await _fresh_agent_state(engine, agent_id) == "fresh"


async def test_page_continuity_is_verified_not_counted(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    settings = ca_settings(tmp_path)
    cluster_a = str(world["cluster_a"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)

        # pages {2,3} with total_pages=2: counters match, the SET does not.
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        one = [resource("Pod", "p-a", "cccc1111-0000-0000-0000-000000000001")]
        two = [resource("Pod", "p-b", "cccc1111-0000-0000-0000-000000000002")]
        assert (await driver.page(snapshot_uid, 2, one)).status_code == 200
        assert (await driver.page(snapshot_uid, 3, two)).status_code == 200
        refused = await driver.complete(snapshot_uid, 2, 2)
        assert refused.status_code == 409, refused.text
        assert await _fresh_agent_state(engine, agent_id) == "reconcile_required"

        # pages {1,3} with total_pages=2 (hole in the middle): refused.
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        assert (await driver.page(snapshot_uid, 1, one)).status_code == 200
        assert (await driver.page(snapshot_uid, 3, two)).status_code == 200
        assert (await driver.complete(snapshot_uid, 2, 2)).status_code == 409

        # Duplicate UID across two pages: staging collapses it, so the
        # declared total of 2 is a lie → refused; projection untouched.
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        same = [resource("Pod", "p-dup", "cccc1111-0000-0000-0000-000000000003")]
        assert (await driver.page(snapshot_uid, 1, same)).status_code == 200
        assert (await driver.page(snapshot_uid, 2, same)).status_code == 200
        assert (await driver.complete(snapshot_uid, 2, 2)).status_code == 409
        counts = await _counts(engine, cluster_a)
        assert counts["projection"] == 0, "no torn snapshot may reach the projection"

        # Same page number with DIFFERENT content is a torn stream.
        snapshot_uid = str(uuidlib.uuid4())
        assert (await driver.begin(snapshot_uid)).status_code == 200
        assert (await driver.page(snapshot_uid, 1, one)).status_code == 200
        conflicting = await driver.page(snapshot_uid, 1, two, sequence=driver.sequence)
        assert conflicting.status_code == 409


async def test_snapshot_generations_and_writer_races(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    settings = ca_settings(tmp_path)
    cluster_a = str(world["cluster_a"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)

        # OLD snapshot begun; NEW snapshot begins (supersedes) + completes;
        # the OLD snapshot can never complete afterwards.
        old_uid = str(uuidlib.uuid4())
        assert (await driver.begin(old_uid)).status_code == 200
        old_world = healthy_world()
        assert (await driver.page(old_uid, 1, old_world)).status_code == 200

        new_uid = str(uuidlib.uuid4())
        assert (await driver.begin(new_uid)).status_code == 200
        assert (await driver.page(new_uid, 1, healthy_world())).status_code == 200
        assert (await driver.complete(new_uid, 1, 3)).json()["result"] == "applied"

        old_complete = await driver.complete(old_uid, 1, len(old_world))
        assert old_complete.status_code == 409, "a superseded snapshot must never complete"

        async with engine.connect() as connection:
            applied = (
                await connection.execute(
                    text(
                        "SELECT applied_generation, applied_snapshot_id "
                        "FROM cluster_inventory_state WHERE cluster_id = :cid"
                    ),
                    {"cid": cluster_a},
                )
            ).one()
        applied_generation = int(applied[0])
        assert applied_generation >= 2

        # A DELAYED old begin (stale sequence) after the newer projection:
        # nothing regresses.
        stale = AgentDriver(client, key, agent_id, cluster_a)
        stale.sequence = 0
        delayed = await stale.begin(str(uuidlib.uuid4()))
        assert delayed.json()["result"] == "stale"
        async with engine.connect() as connection:
            still = (
                await connection.execute(
                    text(
                        "SELECT applied_generation FROM cluster_inventory_state "
                        "WHERE cluster_id = :cid"
                    ),
                    {"cid": cluster_a},
                )
            ).scalar_one()
        assert int(still) == applied_generation, "a delayed begin must not regress state"

        # TWO agents race for one cluster: the newest enrollment holds the
        # writer seat; the superseded agent cannot write inventory but its
        # heartbeat still lands.
        agent_b, key_b = await enroll_agent(engine, cluster_a)
        driver_b = AgentDriver(client, key_b, agent_b, cluster_a)
        assert (await driver_b.begin(str(uuidlib.uuid4()))).status_code == 200

        superseded_begin = await driver.begin(str(uuidlib.uuid4()))
        assert superseded_begin.status_code == 403
        assert "agent authentication failed" in superseded_begin.text
        superseded_events = await driver.events(
            [
                {
                    "event_id": str(uuidlib.uuid4()),
                    "change_type": "updated",
                    "resource": healthy_world()[0],
                }
            ]
        )
        assert superseded_events.status_code == 403, (
            "an old-generation watch event must never overwrite newer state"
        )
        assert (await driver.heartbeat("fresh")).status_code == 200

        # The active writer B finishes its snapshot normally.
        b_uid = str(uuidlib.uuid4())
        b_world = healthy_world()
        stale_b = await driver_b.begin(b_uid)  # begin #2 for B
        assert stale_b.status_code == 200
        assert (await driver_b.page(b_uid, 1, b_world)).status_code == 200
        assert (await driver_b.complete(b_uid, 1, len(b_world))).json()["result"] == "applied"


async def test_snapshot_ttl_and_bounded_cleanup(engine: AsyncEngine, tmp_path: Path) -> None:
    world = await seed_catalog_world(engine)
    settings = ca_settings(tmp_path).model_copy(
        update={
            "agent_snapshot_ttl_seconds": 60,
            "agent_snapshot_history_limit": 2,
            "agent_change_event_row_limit": 5,
            "agent_change_event_retention_days": 1,
            "agent_cleanup_batch_rows": 2,
            "agent_max_pending_snapshots": 1,
        }
    )
    cluster_a = str(world["cluster_a"].id)
    cluster_b = str(world["cluster_b"].id)
    agent_id, key = await enroll_agent(engine, cluster_a)
    agent_b, key_b = await enroll_agent(engine, cluster_b)

    async with internal_client(settings) as client:
        driver = AgentDriver(client, key, agent_id, cluster_a)
        await _seed_fresh_projection(driver)
        baseline = await _counts(engine, cluster_a)
        assert baseline["projection"] == 3

        # A pending snapshot that exceeds the completion TTL cannot apply;
        # the last-good projection stays and the state turns honest.
        expired_uid = str(uuidlib.uuid4())
        assert (await driver.begin(expired_uid)).status_code == 200
        stale_world = [
            resource(
                "Pod", "late", "dddd1111-0000-0000-0000-000000000001", status={"phase": "Running"}
            )
        ]
        assert (await driver.page(expired_uid, 1, stale_world)).status_code == 200
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE inventory_snapshots SET started_at = now() - interval '2 hours' "
                    "WHERE cluster_id = :cid AND status = 'pending'"
                ),
                {"cid": cluster_a},
            )
        timed_out = await driver.complete(expired_uid, 1, 1)
        assert timed_out.status_code == 409
        after = await _counts(engine, cluster_a)
        assert after["projection"] == baseline["projection"], "TTL keeps the last-good projection"
        assert await _fresh_agent_state(engine, agent_id) == "reconcile_required"

        # Maintenance: expired pendings discarded, dead staging drained in
        # bounded batches, history pruned, change events bounded — and the
        # OTHER cluster untouched.
        driver_b = AgentDriver(client, key_b, agent_b, cluster_b)
        await _seed_fresh_projection(driver_b)
        b_counts = await _counts(engine, cluster_b)

        for _ in range(4):  # repeated abandonments cannot grow the DB
            abandoned = str(uuidlib.uuid4())
            assert (await driver.begin(abandoned)).status_code == 200
            assert (
                await driver.page(
                    abandoned,
                    1,
                    [
                        resource(
                            "Pod", "junk", f"eeee1111-0000-0000-0000-{uuidlib.uuid4().hex[:12]}"
                        )
                    ],
                )
            ).status_code == 200

        # NOTE: snapshot_begin schedules a background maintenance pass, so
        # some housekeeping may already have happened; the manual runs
        # below prove convergence and idempotence regardless.
        await run_inventory_maintenance(engine, settings, uuidlib.UUID(cluster_a))
        again = await run_inventory_maintenance(engine, settings, uuidlib.UUID(cluster_a))

        async with engine.connect() as connection:
            snapshot_rows = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM inventory_snapshots WHERE cluster_id = :cid "
                        "AND status != 'pending'"
                    ),
                    {"cid": cluster_a},
                )
            ).scalar_one()
            pending_rows = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM inventory_snapshots WHERE cluster_id = :cid "
                        "AND status = 'pending'"
                    ),
                    {"cid": cluster_a},
                )
            ).scalar_one()
            dead_staging = (
                await connection.execute(
                    text(
                        """
                        SELECT count(*) FROM inventory_staging_resources staging
                        JOIN inventory_snapshots snap ON snap.id = staging.snapshot_id
                        WHERE snap.cluster_id = :cid AND snap.status != 'pending'
                        """
                    ),
                    {"cid": cluster_a},
                )
            ).scalar_one()
        assert int(dead_staging) == 0, "discarded staging must drain"
        assert int(snapshot_rows) <= 2 + 1, "history beyond the limit must prune"
        assert int(pending_rows) <= 1, "pending snapshots respect the cap"
        # Idempotence: a second full pass finds nothing left to do.
        assert all(count == 0 for count in again.values()), again

        # The active pending snapshot of the OTHER cluster survives; its
        # counts are untouched.
        assert await _counts(engine, cluster_b) == b_counts

        # Change events: exceed the row bound, then maintenance trims to it.
        await _seed_fresh_projection(driver)  # recover cluster A
        for index in range(8):
            update = [
                resource(
                    "Pod",
                    "api-1",
                    UID_POD,
                    status={"phase": "Running", "restarts": index},
                )
            ]
            response = await driver.events(
                [
                    {
                        "event_id": str(uuidlib.uuid4()),
                        "change_type": "updated",
                        "resource": update[0],
                    }
                ]
            )
            assert response.status_code == 200
        await run_inventory_maintenance(engine, settings, uuidlib.UUID(cluster_a))
        async with engine.connect() as connection:
            event_rows = (
                await connection.execute(
                    text("SELECT count(*) FROM inventory_change_events WHERE cluster_id = :cid"),
                    {"cid": cluster_a},
                )
            ).scalar_one()
        assert int(event_rows) <= 5, "change events respect the per-cluster row bound"
        # The projection itself is NEVER touched by cleanup.
        final = await _counts(engine, cluster_a)
        assert final["projection"] >= 3
