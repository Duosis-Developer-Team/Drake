"""Workload bindings against the real database: validation, scope, audit."""

import uuid

import pytest
from drake_api.service_health.bindings import (
    BindingError,
    BindingTarget,
    create_binding,
    get_binding,
    list_bindings,
    refresh_resolution,
    resolve_workload,
    set_lifecycle,
    validate_target,
)
from drake_api.service_health.presets import DEFAULT_PRESET_KEY, preset_keys
from harness_s1 import grant_platform_owner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

PRESETS = preset_keys()


def target(**overrides) -> BindingTarget:
    base = dict(
        environment_service_id=uuid.uuid4(),
        cluster_id=uuid.uuid4(),
        namespace="hermes-dev",
        workload_kind="Deployment",
        workload_name="hermes-frontend",
        preset_key=DEFAULT_PRESET_KEY,
    )
    base.update(overrides)
    return BindingTarget(**base)


# --- validation happens before anything touches the database -------------


def test_an_unsupported_workload_kind_is_refused() -> None:
    with pytest.raises(BindingError) as error:
        validate_target(target(workload_kind="CronJob"), PRESETS)
    assert error.value.code == "unsupported_workload_kind"


def test_an_unknown_preset_or_policy_is_refused() -> None:
    with pytest.raises(BindingError) as error:
        validate_target(target(preset_key="something.invented"), PRESETS)
    assert error.value.code == "unknown_preset"
    with pytest.raises(BindingError) as error:
        validate_target(target(health_policy_key="nope.v1"), PRESETS)
    assert error.value.code == "unknown_health_policy"


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet"])
def test_the_three_supported_kinds_validate(kind: str) -> None:
    validate_target(target(workload_kind=kind), PRESETS)


async def _world(engine: AsyncEngine) -> dict[str, uuid.UUID]:
    """A project → environment → service, plus a cluster, all active."""
    async with engine.begin() as connection:
        org = (
            await connection.execute(
                text(
                    "SELECT id FROM scopes WHERE scope_type='organization' AND external_ref='root'"
                )
            )
        ).scalar_one()
        project_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('project', :ref, :parent) RETURNING id"
                ),
                {"ref": f"p-{uuid.uuid4().hex[:8]}", "parent": org},
            )
        ).scalar_one()
        project = (
            await connection.execute(
                text(
                    "INSERT INTO projects (project_key, display_name, repo_provider, "
                    "repo_owner, repo_name, tenant_model, catalog_source_kind, scope_id) "
                    "VALUES (:k, 'Pilot', 'github', 'acme', :k, 'none', 'manifest', :scope) "
                    "RETURNING id"
                ),
                {"k": f"pilot{uuid.uuid4().hex[:6]}", "scope": project_scope},
            )
        ).scalar_one()
        env_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('environment', :ref, :parent) RETURNING id"
                ),
                {"ref": f"e-{uuid.uuid4().hex[:8]}", "parent": project_scope},
            )
        ).scalar_one()
        environment = (
            await connection.execute(
                text(
                    "INSERT INTO environments (project_id, environment_key, runtime, "
                    "catalog_source_kind, cluster_id, namespace, scope_id) "
                    "VALUES (:p, 'dev', 'external', 'manifest', NULL, NULL, :scope) "
                    "RETURNING id"
                ),
                {"p": project, "scope": env_scope},
            )
        ).scalar_one()
        service = (
            await connection.execute(
                text(
                    "INSERT INTO service_definitions (project_id, service_key, display_name, "
                    "component, runtime, metrics_profile, catalog_source_kind) "
                    "VALUES (:p, :k, 'API', 'api', 'kubernetes', 'http', 'manifest') "
                    "RETURNING id"
                ),
                {"p": project, "k": f"api{uuid.uuid4().hex[:6]}"},
            )
        ).scalar_one()
        svc_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('service', :ref, :parent) RETURNING id"
                ),
                {"ref": f"s-{uuid.uuid4().hex[:8]}", "parent": env_scope},
            )
        ).scalar_one()
        environment_service = (
            await connection.execute(
                text(
                    "INSERT INTO environment_services (environment_id, service_id, project_id, "
                    "scope_id) VALUES (:e, :s, :p, :scope) RETURNING id"
                ),
                {"e": environment, "s": service, "p": project, "scope": svc_scope},
            )
        ).scalar_one()
        cluster_scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('cluster', :ref, :parent) RETURNING id"
                ),
                {"ref": f"c-{uuid.uuid4().hex[:8]}", "parent": org},
            )
        ).scalar_one()
        cluster = (
            await connection.execute(
                text(
                    "INSERT INTO clusters (cluster_ref, display_name, catalog_source_kind, "
                    "scope_id) VALUES (:ref, 'Cluster', 'manifest', :scope) RETURNING id"
                ),
                {"ref": f"cl-{uuid.uuid4().hex[:8]}", "scope": cluster_scope},
            )
        ).scalar_one()
    return {
        "environment_service_id": environment_service,
        "cluster_id": cluster,
        "service_scope": svc_scope,
    }


async def _owner(engine: AsyncEngine):
    """An identity holding Platform Owner at the organization root.

    Created directly: these functions take a Principal, so driving a login
    flow to obtain one would only add a dependency that can fail for
    reasons unrelated to what is under test.
    """
    issuer = "https://tests.drake.local/v2.0"
    subject = f"owner-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as connection:
        identity = (
            await connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, display_name) "
                    "VALUES (:i, :s, 'Owner') RETURNING id"
                ),
                {"i": issuer, "s": subject},
            )
        ).scalar_one()
    await grant_platform_owner(engine, issuer, subject)
    return issuer, identity


@pytest.mark.asyncio
async def test_create_resolve_disable_and_reenable(engine: AsyncEngine) -> None:
    issuer, identity = await _owner(engine)
    world = await _world(engine)

    async with engine.begin() as connection:
        principal = _principal(identity, issuer)
        created = await create_binding(
            connection,
            principal,
            target(
                environment_service_id=world["environment_service_id"],
                cluster_id=world["cluster_id"],
            ),
            PRESETS,
            identity,
        )
    assert created["revision"] == 1
    # The workload has never been reported, so it is unresolved — not absent.
    assert created["resolved"] is False

    binding_id = uuid.UUID(created["id"])
    async with engine.connect() as connection:
        principal = _principal(identity, issuer)
        fetched = await get_binding(connection, principal, binding_id)
    assert fetched is not None
    assert fetched["resolution"]["resolved"] is False
    assert fetched["lifecycle"] == "active"

    async with engine.begin() as connection:
        principal = _principal(identity, issuer)
        disabled = await set_lifecycle(connection, principal, binding_id, "disabled", 1, identity)
    assert disabled["lifecycle"] == "disabled"
    assert disabled["changed"] is True
    assert disabled["revision"] == 2

    async with engine.begin() as connection:
        principal = _principal(identity, issuer)
        again = await set_lifecycle(connection, principal, binding_id, "active", None, identity)
    assert again["lifecycle"] == "active"


def _principal(identity_id, issuer):
    from drake_api.rbac.service import Principal

    return Principal(identity_id=identity_id, issuer=issuer)


@pytest.mark.asyncio
async def test_a_duplicate_binding_is_refused(engine: AsyncEngine) -> None:
    issuer, identity = await _owner(engine)
    world = await _world(engine)
    spec = target(
        environment_service_id=world["environment_service_id"], cluster_id=world["cluster_id"]
    )
    async with engine.begin() as connection:
        principal = _principal(identity, issuer)
        await create_binding(connection, principal, spec, PRESETS, identity)

    async with engine.begin() as connection:
        principal = _principal(identity, issuer)
        with pytest.raises(BindingError) as error:
            await create_binding(connection, principal, spec, PRESETS, identity)
    assert error.value.code == "duplicate_binding"


@pytest.mark.asyncio
async def test_an_unknown_service_is_not_found_not_forbidden(engine: AsyncEngine) -> None:
    """A 404 for both cases: a 403 would confirm the row exists."""
    issuer, identity = await _owner(engine)
    world = await _world(engine)
    async with engine.begin() as connection:
        principal = _principal(identity, issuer)
        with pytest.raises(BindingError) as error:
            await create_binding(
                connection,
                principal,
                target(environment_service_id=uuid.uuid4(), cluster_id=world["cluster_id"]),
                PRESETS,
                identity,
            )
    assert error.value.code == "not_found"


@pytest.mark.asyncio
async def test_a_principal_without_grants_sees_nothing(engine: AsyncEngine) -> None:
    """Scope isolation: no grants means an empty list, never everything."""
    from drake_api.rbac.service import Principal

    issuer, identity = await _owner(engine)
    world = await _world(engine)
    async with engine.begin() as connection:
        principal = _principal(identity, issuer)
        created = await create_binding(
            connection,
            principal,
            target(
                environment_service_id=world["environment_service_id"],
                cluster_id=world["cluster_id"],
            ),
            PRESETS,
            identity,
        )

    stranger = Principal(identity_id=uuid.uuid4(), issuer="https://elsewhere.test")
    async with engine.connect() as connection:
        listing = await list_bindings(connection, stranger, limit=50)
        hidden = await get_binding(connection, stranger, uuid.UUID(created["id"]))
    assert listing["items"] == []
    assert listing["total"] == 0, "the total must not reveal invisible rows"
    assert hidden is None


@pytest.mark.asyncio
async def test_listing_is_bounded(engine: AsyncEngine) -> None:
    issuer, identity = await _owner(engine)
    async with engine.connect() as connection:
        principal = _principal(identity, issuer)
        page = await list_bindings(connection, principal, limit=1000)
    assert page["limit"] <= 100


@pytest.mark.asyncio
async def test_resolution_finds_a_workload_that_inventory_has_seen(engine: AsyncEngine) -> None:
    issuer, identity = await _owner(engine)
    world = await _world(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO inventory_resources
                    (cluster_id, api_group, api_version, kind, namespace, name, uid,
                     resource_version, payload, last_seen_at, observed_at)
                VALUES (:cluster, 'apps', 'v1', 'Deployment', 'hermes-dev', 'hermes-frontend',
                        'uid-123', '1', '{}'::jsonb, now(), now())
                """
            ),
            {"cluster": world["cluster_id"]},
        )

    async with engine.connect() as connection:
        found = await resolve_workload(
            connection, world["cluster_id"], "hermes-dev", "Deployment", "hermes-frontend"
        )
        absent = await resolve_workload(
            connection, world["cluster_id"], "hermes-dev", "Deployment", "not-there"
        )
    assert found is not None and found["uid"] == "uid-123"
    assert absent is None

    async with engine.begin() as connection:
        principal = _principal(identity, issuer)
        created = await create_binding(
            connection,
            principal,
            target(
                environment_service_id=world["environment_service_id"],
                cluster_id=world["cluster_id"],
            ),
            PRESETS,
            identity,
        )
    assert created["resolved"] is True


@pytest.mark.asyncio
async def test_a_workload_leaving_inventory_keeps_the_binding(engine: AsyncEngine) -> None:
    """An agent outage must not delete an operator's configuration."""
    issuer, identity = await _owner(engine)
    world = await _world(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO inventory_resources
                    (cluster_id, api_group, api_version, kind, namespace, name, uid,
                     resource_version, payload, last_seen_at, observed_at)
                VALUES (:cluster, 'apps', 'v1', 'Deployment', 'hermes-dev', 'hermes-frontend',
                        'uid-abc', '1', '{}'::jsonb, now(), now())
                """
            ),
            {"cluster": world["cluster_id"]},
        )
        principal = _principal(identity, issuer)
        created = await create_binding(
            connection,
            principal,
            target(
                environment_service_id=world["environment_service_id"],
                cluster_id=world["cluster_id"],
            ),
            PRESETS,
            identity,
        )
    binding_id = uuid.UUID(created["id"])

    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM inventory_resources WHERE cluster_id = :c"),
            {"c": world["cluster_id"]},
        )
        refreshed = await refresh_resolution(connection, binding_id)
    assert refreshed is not None
    assert refreshed["resolved"] is False

    async with engine.connect() as connection:
        principal = _principal(identity, issuer)
        still_there = await get_binding(connection, principal, binding_id)
    assert still_there is not None, "the binding must survive its workload disappearing"
    assert still_there["resolution"]["resolved"] is False
