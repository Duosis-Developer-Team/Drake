"""IDOR negatives and audit query behavior (integration, local stack).

Proves: Project A authority yields nothing about Project B (list absence,
guessed-UUID 404s, mutation denials), audit visibility is scope-filtered
with cursor pagination, and error bodies leak no hidden-resource data.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from drake_api.db import dispose_engines
from drake_api.rbac.catalog import seed_catalog
from drake_api.rbac.scope import ScopeResolver
from harness_s1 import (
    S1Harness,
    build_harness,
    grant_platform_owner,
    require_it_settings,
    reset_rbac_state,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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
    await reset_rbac_state(eng)
    async with eng.begin() as connection:
        await seed_catalog(connection)
    yield eng
    await eng.dispose()
    await dispose_engines()


def idem() -> dict[str, str]:
    return {"Idempotency-Key": f"it-{uuidlib.uuid4().hex}"}


def csrf(me: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": me["csrf_token"]}


async def prepare_two_projects(
    harness: S1Harness, engine: AsyncEngine
) -> tuple[str, str, str, str]:
    """Owner sets up projects A and B; user-plain gets a project-scoped role
    with rbac.manage + audit.view at A ONLY. Returns (a, b, plain_id, grant_b)."""
    async with engine.begin() as connection:
        resolver = ScopeResolver(connection)
        project_a = str((await resolver.ensure("project", "project-a", "Project A")).id)
        project_b = str((await resolver.ensure("project", "project-b", "Project B")).id)

    async with harness.api_client() as plain_client:
        await harness.login(plain_client, "user-plain")
    async with engine.connect() as connection:
        plain_row = (
            await connection.execute(text("SELECT id FROM identities WHERE subject = 'user-plain'"))
        ).first()
    plain_id = str(plain_row[0])

    async with harness.api_client() as owner_client:
        await harness.login(owner_client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        me = (await owner_client.get("/v1/me")).json()

        created = await owner_client.post(
            "/v1/roles",
            json={"name": "Project A Admin", "description": ""},
            headers={**csrf(me), **idem()},
        )
        role_id = created.json()["id"]
        assert (
            await owner_client.put(
                f"/v1/roles/{role_id}/permissions",
                json={"permissions": ["rbac.manage", "audit.view", "project.view"]},
                headers={**csrf(me), **idem(), "If-Match": 'W/"role-1"'},
            )
        ).status_code == 200
        assert (
            await owner_client.post(
                "/v1/grants",
                json={"role_id": role_id, "scope_id": project_a, "identity_id": plain_id},
                headers={**csrf(me), **idem()},
            )
        ).status_code == 201

        # A grant that lives in project B (the IDOR target).
        developer = next(
            role["id"]
            for role in (await owner_client.get("/v1/roles")).json()["roles"]
            if role["name"] == "Developer"
        )
        owner_row_id = (await owner_client.get("/v1/me")).json()
        del owner_row_id
        grant_b = await owner_client.post(
            "/v1/grants",
            json={"role_id": developer, "scope_id": project_b, "identity_id": plain_id},
            headers={**csrf(me), **idem()},
        )
        assert grant_b.status_code == 201
    return project_a, project_b, plain_id, grant_b.json()["id"]


async def test_project_a_authority_reveals_nothing_about_project_b(
    engine: AsyncEngine,
) -> None:
    harness = build_harness()
    _project_a, project_b, _plain_id, grant_in_b = await prepare_two_projects(harness, engine)

    async with harness.api_client() as client:
        me = await harness.login(client, "user-plain")

        # 1. Grant list contains only subtree-of-A entries.
        grants = (await client.get("/v1/grants")).json()["grants"]
        assert grants, "expected at least the project-A admin grant to be visible"
        assert all(grant["scope_ref"] != "project-b" for grant in grants)

        # 2. Guessed/leaked UUID of a project-B grant → consistent 404.
        response = await client.request(
            "DELETE", f"/v1/grants/{grant_in_b}", headers={**csrf(me), **idem()}
        )
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["message"] == "not found"
        assert "project-b" not in response.text

        # 3. Random non-existent UUID → identical 404 shape (no oracle).
        ghost = await client.request(
            "DELETE", f"/v1/grants/{uuidlib.uuid4()}", headers={**csrf(me), **idem()}
        )
        assert ghost.status_code == 404
        assert ghost.json()["error"]["message"] == body["error"]["message"]

        # 4. Creating a grant INTO project B → 404 as well.
        developer_visible = await client.get("/v1/roles")
        assert developer_visible.status_code == 200  # rbac.manage@A allows role read
        developer = next(
            role["id"] for role in developer_visible.json()["roles"] if role["name"] == "Developer"
        )
        into_b = await client.post(
            "/v1/grants",
            json={"role_id": developer, "scope_id": project_b, "identity_id": _plain_id},
            headers={**csrf(me), **idem()},
        )
        assert into_b.status_code == 404


async def test_audit_scope_filtering_and_pagination(engine: AsyncEngine) -> None:
    harness = build_harness()
    project_a, _project_b, _plain_id, _grant_b = await prepare_two_projects(harness, engine)

    # Generate scoped audit events in both projects via owner mutations.
    async with harness.api_client() as owner_client:
        await harness.login(owner_client, "user-owner")
        me = (await owner_client.get("/v1/me")).json()
        developer = next(
            role["id"]
            for role in (await owner_client.get("/v1/roles")).json()["roles"]
            if role["name"] == "Viewer"
        )
        for _ in range(3):
            grant = await owner_client.post(
                "/v1/grants",
                json={
                    "role_id": developer,
                    "scope_id": project_a,
                    "group_mapping_id": None,
                    "identity_id": _plain_id,
                },
                headers={**csrf(me), **idem()},
            )
            assert grant.status_code == 201

    async with harness.api_client() as client:
        await harness.login(client, "user-plain")

        # Scope-filtered view: only project-a events are visible.
        page = (await client.get("/v1/audit-events?limit=2")).json()
        assert len(page["events"]) == 2
        assert all(event["scope_ref"] in (None, "project-a") for event in page["events"])
        assert all(event["scope_ref"] == "project-a" for event in page["events"])

        # Cursor pagination: next page has different events, no overlap.
        second = (await client.get(f"/v1/audit-events?limit=2&cursor={page['next_cursor']}")).json()
        first_ids = {event["id"] for event in page["events"]}
        assert all(event["id"] not in first_ids for event in second["events"])

        # Filtering for project-b explicitly → 404 (not 403: no existence oracle).
        assert (
            await client.get("/v1/audit-events?scope_type=project&scope_ref=project-b")
        ).status_code == 404

        # Invalid cursor → 422 typed error.
        assert (await client.get("/v1/audit-events?cursor=garbage")).status_code == 422


async def test_owner_sees_unscoped_platform_events_plain_does_not(
    engine: AsyncEngine,
) -> None:
    harness = build_harness()
    await prepare_two_projects(harness, engine)

    async with harness.api_client() as owner_client:
        await harness.login(owner_client, "user-owner")
        events = (await owner_client.get("/v1/audit-events?limit=50")).json()["events"]
        actions = {event["action"] for event in events}
        assert "auth.login" in actions  # unscoped platform events visible at root

    async with harness.api_client() as plain_client:
        await harness.login(plain_client, "user-plain")
        events = (await plain_client.get("/v1/audit-events?limit=50")).json()["events"]
        assert all(event["scope_ref"] == "project-a" for event in events)
        assert all(event["action"] != "auth.login" for event in events)
