"""Environment dashboards, end to end: real PostgreSQL, Redis and RBAC."""

import uuid as uuidlib
from typing import Any

import httpx
import pytest
from harness_s1 import build_harness, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, seed_catalog_world
from test_service_health_read_api_integration import csrf, owner
from test_telemetry_api_integration import engine, migrated_db

pytestmark = pytest.mark.integration

__all__ = ["engine", "migrated_db"]


def harness_with_viewer() -> Any:
    harness = build_harness(require_it_settings())
    user_type = type(harness.provider.users["user-owner"])
    harness.provider.users.setdefault(
        "user-env", user_type("user-env", "Env", "user-env@example.test")
    )
    return harness


def layout(*keys: str, hidden: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [{"key": k, "size": "l", "hidden": k in hidden} for k in keys]


async def save(
    client: httpx.AsyncClient, env_id: Any, expected: int, **overrides: Any
) -> httpx.Response:
    body = {
        "title": "Alpha dev",
        "layout": layout("findings", "overview"),
        "expected_version": expected,
        **overrides,
    }
    return await client.put(f"/v1/dashboards/{env_id}", json=body, headers=await csrf(client))


async def test_the_default_is_version_zero_and_each_save_adds_a_version(
    engine: AsyncEngine,
) -> None:
    harness = harness_with_viewer()
    world = await seed_catalog_world(engine)
    env_id = world["alpha_dev"].id

    async with owner(harness, engine) as client:
        first = (await client.get(f"/v1/dashboards/{env_id}")).json()
        assert first["version"] == 0 and first["saved"] is False and first["can_manage"]
        assert first["title"] == "Alpha · dev"
        assert first["layout"][0]["key"] == "overview"

        one = await save(client, env_id, 0, note="findings first")
        assert one.status_code == 200, one.text
        assert one.json()["version"] == 1
        assert [w["key"] for w in one.json()["layout"][:2]] == ["findings", "overview"]

        stale = await save(client, env_id, 0)
        assert stale.status_code == 409

        two = await save(client, env_id, 1, title="Alpha dev, tidy")
        assert two.json()["version"] == 2 and two.json()["title"] == "Alpha dev, tidy"

        versions = (await client.get(f"/v1/dashboards/{env_id}/versions")).json()["items"]
        assert [v["version"] for v in versions] == [2, 1]
        assert versions[1]["note"] == "findings first"

        # Restoring is saving an old layout again: history only grows.
        back = await save(
            client, env_id, 2, title=versions[1]["title"], layout=versions[1]["layout"]
        )
        assert back.json()["version"] == 3
        listed = (await client.get("/v1/dashboards")).json()["items"]
        mine = next(i for i in listed if i["environment_id"] == str(env_id))
        assert mine["version"] == 3 and mine["saved"]

    async with engine.connect() as connection:
        kept = (
            await connection.execute(text("SELECT count(*) FROM dashboard_versions"))
        ).scalar_one()
        action = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action = 'dashboard.save' AND target_id = :id"
                ),
                {"id": back.json()["id"]},
            )
        ).scalar_one()
    assert kept == 3 and action == 3


async def test_seeing_an_environment_is_not_permission_to_change_its_dashboard(
    engine: AsyncEngine,
) -> None:
    harness = harness_with_viewer()
    world = await seed_catalog_world(engine)
    dev, prod = world["alpha_dev"].id, world["alpha_prod"].id

    async with harness.api_client() as viewer:
        await harness.login(viewer, "user-env")
        await grant(engine, harness, "user-env", "Developer", "environment", "alpha/dev")
        seen = await viewer.get(f"/v1/dashboards/{dev}")
        assert seen.status_code == 200 and seen.json()["can_manage"] is False
        assert (await save(viewer, dev, 0)).status_code == 404
        # Another environment is indistinguishable from one that does not exist.
        assert (await viewer.get(f"/v1/dashboards/{prod}")).status_code == 404
        assert (await viewer.get(f"/v1/dashboards/{uuidlib.uuid4()}")).status_code == 404
        listed = (await viewer.get("/v1/dashboards")).json()["items"]
        assert [i["environment_id"] for i in listed] == [str(dev)]


async def test_a_layout_outside_the_catalog_is_refused(engine: AsyncEngine) -> None:
    harness = harness_with_viewer()
    world = await seed_catalog_world(engine)
    env_id = world["alpha_dev"].id

    async with owner(harness, engine) as client:
        unknown = await save(client, env_id, 0, layout=[{"key": "promql", "size": "l"}])
        twice = await save(client, env_id, 0, layout=layout("overview") + layout("overview"))
        extra = await save(
            client, env_id, 0, layout=[{"key": "overview", "size": "l", "query": "up"}]
        )
        empty_title = await save(client, env_id, 0, title="  ")
    assert unknown.status_code == 422
    assert twice.status_code == 422
    assert extra.status_code == 422
    assert empty_title.status_code == 422
