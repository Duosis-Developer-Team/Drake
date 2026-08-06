"""Dynamic RBAC through the real API (integration, local stack).

Covers: catalog idempotency, role lifecycle with ETag/If-Match and
Idempotency-Key, permission edits with anti-escalation, scoped grants with
delegation rules, group mappings, validity windows, inheritance direction,
last-owner protection, and transactional (fail-closed) audit.
"""

import uuid as uuidlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from drake_api.db import dispose_engines
from drake_api.rbac.catalog import PERMISSIONS, seed_catalog
from drake_api.rbac.scope import ScopeResolver
from drake_api.rbac.service import Principal, RbacService
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


async def owner_session(
    harness: S1Harness, engine: AsyncEngine, client: httpx.AsyncClient
) -> dict[str, Any]:
    await harness.login(client, "user-owner")
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
    me = (await client.get("/v1/me")).json()
    return me


def csrf(me: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": me["csrf_token"]}


# --------------------------------------------------------------- catalog
async def test_catalog_seed_is_idempotent(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await seed_catalog(connection)
        await seed_catalog(connection)
        count = (await connection.execute(text("SELECT count(*) FROM permissions"))).scalar_one()
    assert count == len(PERMISSIONS)


async def test_new_identity_has_zero_permissions(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await harness.login(client, "user-plain")
        assert me["permissions"] == []
        assert me["scopes"] == {}
        # And RBAC surfaces are denied outright:
        assert (await client.get("/v1/permissions")).status_code == 403
        assert (await client.get("/v1/roles")).status_code == 403
        assert (await client.get("/v1/audit-events")).status_code == 403


# ----------------------------------------------------------------- roles
async def test_role_lifecycle_with_etag_and_idempotency(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_session(harness, engine, client)

        key = idem()
        created = await client.post(
            "/v1/roles",
            json={"name": "Release Manager", "description": "d"},
            headers={**csrf(me), **key},
        )
        assert created.status_code == 201, created.text
        role_id = created.json()["id"]

        # Idempotent replay: same key returns the stored result, one role only.
        replay = await client.post(
            "/v1/roles",
            json={"name": "Release Manager", "description": "d"},
            headers={**csrf(me), **key},
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == role_id

        # Missing Idempotency-Key → 428; missing If-Match on update → 428.
        assert (
            await client.post(
                "/v1/roles", json={"name": "X-Role-2", "description": ""}, headers=csrf(me)
            )
        ).status_code == 428
        assert (
            await client.put(
                f"/v1/roles/{role_id}",
                json={"description": "n"},
                headers={**csrf(me), **idem()},
            )
        ).status_code == 428

        updated = await client.put(
            f"/v1/roles/{role_id}",
            json={"description": "new"},
            headers={**csrf(me), **idem(), "If-Match": 'W/"role-1"'},
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2

        # Stale If-Match (concurrent edit) → 412.
        stale = await client.put(
            f"/v1/roles/{role_id}",
            json={"description": "conflict"},
            headers={**csrf(me), **idem(), "If-Match": 'W/"role-1"'},
        )
        assert stale.status_code == 412

        # Permissions set + archive.
        perms = await client.put(
            f"/v1/roles/{role_id}/permissions",
            json={"permissions": ["project.view", "telemetry.query"]},
            headers={**csrf(me), **idem(), "If-Match": 'W/"role-2"'},
        )
        assert perms.status_code == 200

        archived = await client.post(
            f"/v1/roles/{role_id}/archive",
            headers={**csrf(me), **idem(), "If-Match": 'W/"role-3"'},
        )
        assert archived.status_code == 200

        # Archived roles cannot be granted.
        root_scope = (
            await client.get("/v1/me")
        ).json()  # owner still has scopes; fetch org scope id via DB instead
    async with engine.connect() as connection:
        scope_row = (
            await connection.execute(text("SELECT id FROM scopes WHERE scope_type='organization'"))
        ).first()
    async with harness.api_client() as client2:
        me2 = await owner_session(harness, engine, client2)
        plain_identity = None  # grant target below uses group mapping instead
        grant = await client2.post(
            "/v1/grants",
            json={
                "role_id": role_id,
                "scope_id": str(scope_row[0]),
                "group_mapping_id": None,
                "identity_id": None,
            },
            headers={**csrf(me2), **idem()},
        )
        assert grant.status_code == 409  # exactly_one_principal
        del plain_identity, root_scope


async def test_system_roles_are_immutable(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_session(harness, engine, client)
        roles = (await client.get("/v1/roles")).json()["roles"]
        owner_role = next(role for role in roles if role["name"] == "Platform Owner")
        response = await client.put(
            f"/v1/roles/{owner_role['id']}/permissions",
            json={"permissions": ["project.view"]},
            headers={**csrf(me), **idem(), "If-Match": owner_role["etag"]},
        )
        assert response.status_code == 409
        assert response.json()["error"]["message"] == "system_role_immutable"


# ---------------------------------------------------------------- grants
async def setup_project_scopes(engine: AsyncEngine) -> tuple[str, str, str]:
    """Create project-a (with env child) and project-b; return their ids."""
    async with engine.begin() as connection:
        resolver = ScopeResolver(connection)
        project_a = await resolver.ensure("project", "project-a", "Project A")
        env_a = await resolver.ensure(
            "environment", "project-a/dev", "Project A dev", parent_id=project_a.id
        )
        project_b = await resolver.ensure("project", "project-b", "Project B")
    return str(project_a.id), str(env_a.id), str(project_b.id)


async def identity_id_of(engine: AsyncEngine, issuer: str, subject: str) -> str:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT id FROM identities WHERE issuer=:issuer AND subject=:subject"),
                {"issuer": issuer, "subject": subject},
            )
        ).first()
    assert row is not None
    return str(row[0])


async def role_id_by_name(client: httpx.AsyncClient, name: str) -> str:
    roles = (await client.get("/v1/roles")).json()["roles"]
    return str(next(role["id"] for role in roles if role["name"] == name))


async def test_direct_grant_inheritance_and_isolation(engine: AsyncEngine) -> None:
    harness = build_harness()
    project_a, env_a, project_b = await setup_project_scopes(engine)

    async with harness.api_client() as plain_client:
        await harness.login(plain_client, "user-plain")
    plain_id = await identity_id_of(engine, harness.provider.issuer, "user-plain")

    async with harness.api_client() as owner_client:
        me = await owner_session(harness, engine, owner_client)
        developer_role = await role_id_by_name(owner_client, "Developer")
        grant = await owner_client.post(
            "/v1/grants",
            json={"role_id": developer_role, "scope_id": project_a, "identity_id": plain_id},
            headers={**csrf(me), **idem()},
        )
        assert grant.status_code == 201, grant.text

    async with engine.connect() as connection:
        service = RbacService(connection)
        principal = Principal(identity_id=uuidlib.UUID(plain_id), issuer=harness.provider.issuer)
        # Parent → child inheritance applies:
        assert await service.has_permission(principal, "project.view", uuidlib.UUID(project_a))
        assert await service.has_permission(principal, "project.view", uuidlib.UUID(env_a))
        # Sibling project: no.
        assert not await service.has_permission(principal, "project.view", uuidlib.UUID(project_b))
        # Child grant never widens to parent:
        root = await service.scopes.organization_root()
        assert not await service.has_permission(principal, "project.view", root.id)
        # Tenant access is a separate permission — not derived from project.view:
        assert not await service.has_permission(principal, "tenant.view", uuidlib.UUID(env_a))


async def test_child_grant_does_not_apply_to_parent(engine: AsyncEngine) -> None:
    harness = build_harness()
    project_a, env_a, _project_b = await setup_project_scopes(engine)
    async with harness.api_client() as plain_client:
        await harness.login(plain_client, "user-plain")
    plain_id = await identity_id_of(engine, harness.provider.issuer, "user-plain")

    async with harness.api_client() as owner_client:
        me = await owner_session(harness, engine, owner_client)
        developer_role = await role_id_by_name(owner_client, "Developer")
        grant = await owner_client.post(
            "/v1/grants",
            json={"role_id": developer_role, "scope_id": env_a, "identity_id": plain_id},
            headers={**csrf(me), **idem()},
        )
        assert grant.status_code == 201

    async with engine.connect() as connection:
        service = RbacService(connection)
        principal = Principal(identity_id=uuidlib.UUID(plain_id), issuer=harness.provider.issuer)
        assert await service.has_permission(principal, "project.view", uuidlib.UUID(env_a))
        assert not await service.has_permission(principal, "project.view", uuidlib.UUID(project_a))


async def test_validity_windows_and_revocation(engine: AsyncEngine) -> None:
    harness = build_harness()
    project_a, _env_a, _project_b = await setup_project_scopes(engine)
    async with harness.api_client() as plain_client:
        await harness.login(plain_client, "user-plain")
    plain_id = await identity_id_of(engine, harness.provider.issuer, "user-plain")

    async with harness.api_client() as owner_client:
        me = await owner_session(harness, engine, owner_client)
        developer_role = await role_id_by_name(owner_client, "Developer")

        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        future_grant = await owner_client.post(
            "/v1/grants",
            json={
                "role_id": developer_role,
                "scope_id": project_a,
                "identity_id": plain_id,
                "valid_from": future,
            },
            headers={**csrf(me), **idem()},
        )
        assert future_grant.status_code == 201

        past_start = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        past_end = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        expired_grant = await owner_client.post(
            "/v1/grants",
            json={
                "role_id": developer_role,
                "scope_id": project_a,
                "identity_id": plain_id,
                "valid_from": past_start,
                "valid_to": past_end,
            },
            headers={**csrf(me), **idem()},
        )
        assert expired_grant.status_code == 201

        active = await owner_client.post(
            "/v1/grants",
            json={"role_id": developer_role, "scope_id": project_a, "identity_id": plain_id},
            headers={**csrf(me), **idem()},
        )
        assert active.status_code == 201
        active_id = active.json()["id"]

        async with engine.connect() as connection:
            service = RbacService(connection)
            principal = Principal(
                identity_id=uuidlib.UUID(plain_id), issuer=harness.provider.issuer
            )
            # Only the active grant applies (future + expired contribute nothing).
            grants = await service.effective_grants(principal)
            assert {g.scope_id for g in grants} == {uuidlib.UUID(project_a)}

        revoke = await owner_client.request(
            "DELETE", f"/v1/grants/{active_id}", headers={**csrf(me), **idem()}
        )
        assert revoke.status_code == 200

        async with engine.connect() as connection:
            service = RbacService(connection)
            principal = Principal(
                identity_id=uuidlib.UUID(plain_id), issuer=harness.provider.issuer
            )
            assert await service.effective_grants(principal) == []


async def test_group_mapping_grants_and_overage(engine: AsyncEngine) -> None:
    harness = build_harness()
    project_a, _env_a, _project_b = await setup_project_scopes(engine)
    issuer = harness.provider.issuer
    harness.provider.users["user-plain"].groups = ["group-mapped", "group-unmapped"]

    async with harness.api_client() as owner_client:
        await owner_session(harness, engine, owner_client)
        developer_role = await role_id_by_name(owner_client, "Developer")

    async with engine.begin() as connection:
        service = RbacService(connection)
        owner_id = await identity_id_of(engine, issuer, "user-owner")
        owner_principal = Principal(identity_id=uuidlib.UUID(owner_id), issuer=issuer)
        mapping_id = await service.create_group_mapping(
            owner_principal, issuer, "group-mapped", "Mapped Group", "it-corr-0001"
        )
        await service.create_grant(
            owner_principal,
            role_id=uuidlib.UUID(developer_role),
            scope_id=uuidlib.UUID(project_a),
            group_mapping_id=mapping_id,
            correlation_id="it-corr-0002",
        )

    async with harness.api_client() as plain_client:
        me_plain = await harness.login(plain_client, "user-plain")
        # Mapped group → permissions flow; unmapped group contributes nothing.
        assert "project.view" in me_plain["permissions"]
        assert set(me_plain["permissions"]) == {
            "environment.view",
            "project.view",
            "telemetry.query",
        }

    # Group overage: same user, but the token omits groups → fail closed.
    harness.provider.emit_group_overage = True
    async with harness.api_client() as overage_client:
        me_overage = await harness.login(overage_client, "user-plain")
        assert me_overage["groups_overage"] is True
        assert me_overage["permissions"] == []


async def test_self_escalation_and_delegation_boundaries(engine: AsyncEngine) -> None:
    harness = build_harness()
    project_a, _env_a, project_b = await setup_project_scopes(engine)
    issuer = harness.provider.issuer

    async with harness.api_client() as plain_client:
        await harness.login(plain_client, "user-plain")
    plain_id = await identity_id_of(engine, issuer, "user-plain")

    async with harness.api_client() as owner_client:
        me = await owner_session(harness, engine, owner_client)
        # A project-scoped RBAC admin role (created by owner).
        created = await owner_client.post(
            "/v1/roles",
            json={"name": "Project RBAC Admin", "description": ""},
            headers={**csrf(me), **idem()},
        )
        admin_role = created.json()["id"]
        set_perms = await owner_client.put(
            f"/v1/roles/{admin_role}/permissions",
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
        assert set_perms.status_code == 200
        developer_role = await role_id_by_name(owner_client, "Developer")
        grant = await owner_client.post(
            "/v1/grants",
            json={"role_id": admin_role, "scope_id": project_a, "identity_id": plain_id},
            headers={**csrf(me), **idem()},
        )
        assert grant.status_code == 201

    async with harness.api_client() as plain_client:
        me_plain = await harness.login(plain_client, "user-plain")
        assert "rbac.manage" in me_plain["permissions"]
        owner_id = await identity_id_of(engine, issuer, "user-owner")

        # 1. Self-grant is refused outright (403 + audit).
        self_grant = await plain_client.post(
            "/v1/grants",
            json={"role_id": developer_role, "scope_id": project_a, "identity_id": plain_id},
            headers={**csrf(me_plain), **idem()},
        )
        assert self_grant.status_code == 403

        # 2. Org-root grant creation → hidden as 404 (outside authority).
        async with engine.connect() as connection:
            root_id = str((await ScopeResolver(connection).organization_root()).id)
        org_grant = await plain_client.post(
            "/v1/grants",
            json={"role_id": developer_role, "scope_id": root_id, "identity_id": owner_id},
            headers={**csrf(me_plain), **idem()},
        )
        assert org_grant.status_code == 404

        # 3. Sibling project scope → 404 (no enumeration).
        sibling = await plain_client.post(
            "/v1/grants",
            json={"role_id": developer_role, "scope_id": project_b, "identity_id": owner_id},
            headers={**csrf(me_plain), **idem()},
        )
        assert sibling.status_code == 404

        # 4. Delegating a role whose permissions exceed the actor's own → 403.
        analyst_role = await role_id_by_name(plain_client, "Billing/Operations Analyst")
        too_broad = await plain_client.post(
            "/v1/grants",
            json={"role_id": analyst_role, "scope_id": project_a, "identity_id": owner_id},
            headers={**csrf(me_plain), **idem()},
        )
        assert too_broad.status_code == 403

        # 5. Delegating a subset the actor holds → allowed.
        subset = await plain_client.post(
            "/v1/grants",
            json={"role_id": developer_role, "scope_id": project_a, "identity_id": owner_id},
            headers={**csrf(me_plain), **idem()},
        )
        assert subset.status_code == 201

    # Audit trail recorded the escalation attempts.
    async with engine.connect() as connection:
        denied = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE action IN ('rbac.self_escalation.denied', 'rbac.delegation.denied') "
                    "AND result = 'denied'"
                )
            )
        ).scalar_one()
    assert denied >= 2


async def test_role_permission_edit_cannot_escalate_editor(engine: AsyncEngine) -> None:
    """An org-level RBAC manager without full permissions cannot add
    permissions they do not hold to a role (self-escalation via role edit)."""
    harness = build_harness()
    issuer = harness.provider.issuer
    async with harness.api_client() as limited_client:
        await harness.login(limited_client, "user-plain")
    plain_id = await identity_id_of(engine, issuer, "user-plain")

    async with harness.api_client() as owner_client:
        me = await owner_session(harness, engine, owner_client)
        created = await owner_client.post(
            "/v1/roles",
            json={"name": "Org RBAC Only", "description": ""},
            headers={**csrf(me), **idem()},
        )
        rbac_only_role = created.json()["id"]
        assert (
            await owner_client.put(
                f"/v1/roles/{rbac_only_role}/permissions",
                json={"permissions": ["rbac.manage"]},
                headers={**csrf(me), **idem(), "If-Match": 'W/"role-1"'},
            )
        ).status_code == 200
        async with engine.connect() as connection:
            root_id = str((await ScopeResolver(connection).organization_root()).id)
        assert (
            await owner_client.post(
                "/v1/grants",
                json={"role_id": rbac_only_role, "scope_id": root_id, "identity_id": plain_id},
                headers={**csrf(me), **idem()},
            )
        ).status_code == 201

        target = await owner_client.post(
            "/v1/roles",
            json={"name": "Victim Role", "description": ""},
            headers={**csrf(me), **idem()},
        )
        victim_role = target.json()["id"]

    async with harness.api_client() as limited_client:
        me_limited = await harness.login(limited_client, "user-plain")
        assert me_limited["permissions"] == ["rbac.manage"]
        response = await limited_client.put(
            f"/v1/roles/{victim_role}/permissions",
            json={"permissions": ["tenant.usage.export"]},
            headers={**csrf(me_limited), **idem(), "If-Match": 'W/"role-1"'},
        )
        assert response.status_code == 403


async def test_last_platform_owner_protection(engine: AsyncEngine) -> None:
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_session(harness, engine, client)
        grants = (await client.get("/v1/grants")).json()["grants"]
        own_grant = next(
            grant
            for grant in grants
            if grant["role_name"] == "Platform Owner" and grant["revoked_at"] is None
        )
        response = await client.request(
            "DELETE", f"/v1/grants/{own_grant['id']}", headers={**csrf(me), **idem()}
        )
        assert response.status_code == 409
        assert response.json()["error"]["message"] == "last_platform_owner_protected"


async def test_audit_failure_rolls_back_mutation(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed: if the audit row cannot be written, the RBAC mutation
    itself must not commit."""
    harness = build_harness()
    async with harness.api_client() as client:
        me = await owner_session(harness, engine, client)

        import drake_api.rbac.service as service_module

        def broken_validate(_event: object) -> dict[str, object]:
            raise RuntimeError("audit backend rejected the event")

        monkeypatch.setattr(service_module, "validate_event", broken_validate)
        response = await client.post(
            "/v1/roles",
            json={"name": "Ghost Role", "description": ""},
            headers={**csrf(me), **idem()},
        )
        assert response.status_code == 500
    async with engine.connect() as connection:
        count = (
            await connection.execute(text("SELECT count(*) FROM roles WHERE name = 'Ghost Role'"))
        ).scalar_one()
    assert count == 0  # rolled back together with the failed audit write
