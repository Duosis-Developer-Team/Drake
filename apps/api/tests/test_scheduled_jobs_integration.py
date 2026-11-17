"""An environment's scheduled jobs, end to end (catalog.scheduled_jobs).

Real PostgreSQL, real Redis, real RBAC, real catalog. The inventory rows
are written the way the agent ingest leaves them: a CronJob with its
schedule and last times, and the Jobs it owns, named
`<cronjob>-<scheduled minute>` as the CronJob controller names them.
"""

import json
import uuid
from typing import Any

import pytest
from harness_s1 import build_harness, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import seed_catalog_world
from test_service_health_read_api_integration import owner
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]

# 2026-11-16 03:00 UTC, in minutes since the epoch.
NIGHT = 29913300


async def _resource(
    engine: AsyncEngine,
    cluster_id: uuid.UUID,
    namespace: str,
    kind: str,
    name: str,
    payload: dict[str, Any],
    health: str = "healthy",
    reasons: list[str] | None = None,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO inventory_resources
                    (cluster_id, api_group, api_version, kind, namespace, name, uid,
                     resource_version, payload, health, health_reasons, last_seen_at,
                     observed_at)
                VALUES (:cluster, 'batch', 'batch/v1', :kind, :ns, :name, :uid, '1',
                        CAST(:payload AS jsonb), :health, CAST(:reasons AS jsonb),
                        now(), now())
                """
            ),
            {
                "cluster": cluster_id,
                "kind": kind,
                "ns": namespace,
                "name": name,
                "uid": f"uid-{uuid.uuid4().hex[:12]}",
                "payload": json.dumps(payload),
                "health": health,
                "reasons": json.dumps(reasons or []),
            },
        )


def _job(cronjob: str, minute: int, status: dict[str, int]) -> tuple[str, dict[str, Any]]:
    return f"{cronjob}-{minute}", {
        "owners": [{"kind": "CronJob", "name": cronjob, "uid": "u"}],
        "status_summary": status,
    }


async def _world(engine: AsyncEngine) -> dict[str, Any]:
    world = await seed_catalog_world(engine)
    cluster = world["cluster_a"].id
    await _resource(
        engine,
        cluster,
        "alpha-dev",
        "CronJob",
        "api-cleanup",
        {
            "spec_summary": {"schedule": "0 3 * * *", "suspend": False},
            "status_summary": {
                "last_schedule_time": "2026-11-16T03:00:00Z",
                "last_successful_time": "2026-08-16T03:01:19Z",
            },
        },
        health="degraded",
        reasons=["recent_runs_failed"],
    )
    for minute, status in (
        (NIGHT - 2880, {"failed": 1}),
        (NIGHT - 1440, {"failed": 1}),
        (NIGHT, {"failed": 1}),
    ):
        name, payload = _job("api-cleanup", minute, status)
        await _resource(engine, cluster, "alpha-dev", "Job", name, payload, health="unhealthy")
    # Another environment's CronJob on the same cluster: never listed here.
    await _resource(
        engine,
        cluster,
        "beta-dev",
        "CronJob",
        "beta-only",
        {"spec_summary": {"schedule": "* * * * *"}, "status_summary": {}},
    )
    return world


def _url(world: dict[str, Any], suffix: str = "") -> str:
    return (
        f"/v1/projects/{world['alpha'].id}/environments/{world['alpha_dev'].id}"
        f"/scheduled-jobs{suffix}"
    )


async def test_an_environment_lists_its_cronjobs_with_their_recent_runs(
    engine: AsyncEngine,
) -> None:
    harness = build_harness(require_it_settings())
    world = await _world(engine)

    async with owner(harness, engine) as client:
        response = await client.get(_url(world))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "ok"
    [job] = body["jobs"]
    assert job["name"] == "api-cleanup"
    assert job["health"] == "degraded"
    assert job["reasons"] == ["recent_runs_failed"]
    assert job["schedule"] == "0 3 * * *"
    assert job["last_successful_time"] == "2026-08-16T03:01:19Z"
    # Newest first, each with when it was scheduled and how it ended.
    assert [r["outcome"] for r in job["recent_runs"]] == ["failed", "failed", "failed"]
    assert job["recent_runs"][0]["name"] == f"api-cleanup-{NIGHT}"
    assert job["recent_runs"][0]["scheduled_at"] == "2026-11-16T03:00:00+00:00"
    assert "beta-only" not in response.text


async def test_someone_who_cannot_see_the_environment_sees_nothing(engine: AsyncEngine) -> None:
    harness = build_harness(require_it_settings())
    world = await _world(engine)

    async with harness.api_client() as client:
        await harness.login(client, "user-plain")
        listing = await client.get(_url(world))
        logs = await client.get(_url(world, "/api-cleanup/logs"))

    assert listing.status_code == 404
    assert logs.status_code == 404


async def test_logs_are_only_for_this_environments_own_cronjobs(engine: AsyncEngine) -> None:
    harness = build_harness(require_it_settings())
    world = await _world(engine)

    async with owner(harness, engine) as client:
        own = await client.get(_url(world, "/api-cleanup/logs"))
        foreign = await client.get(_url(world, "/beta-only/logs"))
        unknown = await client.get(_url(world, "/does-not-exist/logs"))
        malformed = await client.get(_url(world, "/Not_A_Name/logs"))

    assert own.status_code == 200, own.text
    # This harness configures no log store: an honest "not configured".
    assert own.json()["state"] == "not_configured"
    assert own.json()["cronjob"] == "api-cleanup"
    for refused in (foreign, unknown, malformed):
        assert refused.status_code == 404
