"""Grant-options visibility policy (integration, local stack).

Org-root managers see the directory (minus themselves); narrow managers see
only their subtree and principals already granted there; superset/archived
roles are never offered; forged requests are still rejected server-side.
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
    return {"Idempotency-Key": f"opt-{uuidlib.uuid4().hex}"}


def csrf(me: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": me["csrf_token"]}


async def setup_world(engine: AsyncEngine) -> dict[str, Any]:
    """Projects A/B; user-plain = project-A admin (rbac.manage+view+telemetry);
    a third identity granted only in project B."""
    harness = build_harness()
    harness.provider.users["user-b-only"] = type(harness.provider.users["user-owner"])(
        "user-b-only", "B Only", "b@example.test"
    )
    async with engine.begin() as connection:
        resolver = ScopeResolver(connection)
        project_a = str((await resolver.ensure("project", "project-a", "Project A")).id)
        project_b = str((await resolver.ensure("project", "project-b", "Project B")).id)

    for subject in ("user-plain", "user-b-only"):
        async with harness.api_client() as client:
            await harness.login(client, subject)

    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        me = (await owner.get("/v1/me")).json()

        roles = (await owner.get("/v1/roles")).json()["roles"]
        role_id = {role["name"]: role["id"] for role in roles}

        created = await owner.post(
            "/v1/roles",
            json={"name": "A Scoped Admin", "description": ""},
            headers={**csrf(me), **idem()},
        )
        a_admin_role = created.json()["id"]
        assert (
            await owner.put(
                f"/v1/roles/{a_admin_role}/permissions",
                json={
                    "permissions": [
                        "rbac.manage",
                        "project.view",
                        "environment.view",
                        "telemetry.query",
                    ]
                },
                headers={**csrf(me), **idem(), "If-Match": 'W/"role-1"'},
            )
        ).status_code == 200

        async with engine.connect() as connection:
            ids = {
                row[1]: str(row[0])
                for row in (
                    await connection.execute(text("SELECT id, subject FROM identities"))
                ).all()
            }

        for role, scope, identity in (
            (a_admin_role, project_a, ids["user-plain"]),
            (role_id["Developer"], project_b, ids["user-b-only"]),
        ):
            assert (
                await owner.post(
                    "/v1/grants",
                    json={"role_id": role, "scope_id": scope, "identity_id": identity},
                    headers={**csrf(me), **idem()},
                )
            ).status_code == 201

        # An archived role must never appear in options.
        archived = await owner.post(
            "/v1/roles",
            json={"name": "Archived Role", "description": ""},
            headers={**csrf(me), **idem()},
        )
        assert (
            await owner.post(
                f"/v1/roles/{archived.json()['id']}/archive",
                headers={**csrf(me), **idem(), "If-Match": 'W/"role-1"'},
            )
        ).status_code == 200

    return {
        "harness": harness,
        "project_a": project_a,
        "project_b": project_b,
        "ids": ids,
        "roles": role_id,
    }


async def test_org_manager_sees_directory_without_self(engine: AsyncEngine) -> None:
    world = await setup_world(engine)
    harness = world["harness"]
    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        options = (await owner.get("/v1/grant-options")).json()

    assert options["directory_scope"] == "organization"
    scope_refs = {scope["scope_ref"] for scope in options["scopes"]}
    assert {"root", "project-a", "project-b"} <= scope_refs
    names = {identity["display_name"] for identity in options["identities"]}
    assert "Plain User" in names and "B Only" in names
    assert "Owner One" not in names  # self excluded
    for identity in options["identities"]:
        assert set(identity.keys()) == {"id", "display_name"}  # no email/subject/issuer


async def test_narrow_manager_sees_only_subtree(engine: AsyncEngine) -> None:
    world = await setup_world(engine)
    harness = world["harness"]
    async with harness.api_client() as plain:
        await harness.login(plain, "user-plain")
        response = await plain.get("/v1/grant-options")
        assert response.status_code == 200
        options = response.json()

    assert options["directory_scope"] == "subtree"
    scope_refs = {scope["scope_ref"] for scope in options["scopes"]}
    assert scope_refs == {"project-a"}  # no org root, no sibling project
    names = {identity["display_name"] for identity in options["identities"]}
    assert "B Only" not in names  # principal known only in project B: invisible
    assert "Owner One" not in names

    role_names = {role["name"] for role in options["roles"]}
    assert "Archived Role" not in role_names

    scope_a = next(s for s in options["scopes"] if s["scope_ref"] == "project-a")
    delegable_names = {
        role["name"] for role in options["roles"] if role["id"] in scope_a["delegable_role_ids"]
    }
    # Developer ⊆ actor's perms at A → offered; Analyst has tenant.usage.export
    # (actor lacks it) → NOT offered; Platform Owner obviously not.
    assert "Developer" in delegable_names
    assert "Billing/Operations Analyst" not in delegable_names
    assert "Platform Owner" not in delegable_names


async def test_without_manage_options_is_denied(engine: AsyncEngine) -> None:
    world = await setup_world(engine)
    harness = world["harness"]
    async with harness.api_client() as b_only:
        await harness.login(b_only, "user-b-only")
        assert (await b_only.get("/v1/grant-options")).status_code == 403


async def test_forged_create_outside_options_still_rejected(engine: AsyncEngine) -> None:
    """The options list is UI shaping only — forging role/scope beyond it is
    re-checked by the create endpoint."""
    world = await setup_world(engine)
    harness = world["harness"]
    async with harness.api_client() as plain:
        me = await harness.login(plain, "user-plain")

        forged_scope = await plain.post(
            "/v1/grants",
            json={
                "role_id": world["roles"]["Developer"],
                "scope_id": world["project_b"],  # not in options → still 404
                "identity_id": world["ids"]["user-b-only"],
            },
            headers={**csrf(me), **idem()},
        )
        assert forged_scope.status_code == 404

        forged_role = await plain.post(
            "/v1/grants",
            json={
                "role_id": world["roles"]["Billing/Operations Analyst"],  # superset
                "scope_id": world["project_a"],
                "identity_id": world["ids"]["user-b-only"],
            },
            headers={**csrf(me), **idem()},
        )
        assert forged_role.status_code == 403


async def test_invalid_validity_interval_rejected(engine: AsyncEngine) -> None:
    world = await setup_world(engine)
    harness = world["harness"]
    async with harness.api_client() as owner:
        await harness.login(owner, "user-owner")
        me = (await owner.get("/v1/me")).json()
        response = await owner.post(
            "/v1/grants",
            json={
                "role_id": world["roles"]["Developer"],
                "scope_id": world["project_a"],
                "identity_id": world["ids"]["user-plain"],
                "valid_from": "2026-08-06T12:00:00Z",
                "valid_to": "2026-08-06T11:00:00Z",
            },
            headers={**csrf(me), **idem()},
        )
        assert response.status_code == 422
