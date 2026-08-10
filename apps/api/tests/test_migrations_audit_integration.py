"""Migration + append-only audit integration tests.

Run only against the disposable local stack (``DRAKE_IT_DATABASE_URL``).
Flow: upgrade head -> schema check -> audit insert via service -> UPDATE and
DELETE are blocked by the database -> downgrade base -> upgrade head again.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.testing import integration_settings
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]


def require_database_url() -> str:
    settings = integration_settings()
    if settings is None:
        pytest.skip("DRAKE_IT_DATABASE_URL / DRAKE_IT_REDIS_URL not set")
    return settings.database_url


def alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


async def test_upgrade_audit_appendonly_downgrade_upgrade() -> None:
    database_url = require_database_url()
    config = alembic_config(database_url)

    # 1-2. empty/disposable DB -> upgrade head
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        # 3. schema verification
        inspector = inspect(engine)
        assert "audit_events" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("audit_events")}
        assert {
            "id",
            "occurred_at",
            "actor_type",
            "actor_id",
            "action",
            "result",
            "correlation_id",
            "metadata",
            "schema_version",
        } <= columns

        # 4. audit insert through the service layer
        async_engine = create_async_engine(database_url)
        try:
            event_id = await record_audit_event(
                async_engine,
                AuditEventData(
                    actor_type="system",
                    actor_id="migration-test",
                    action="audit.selftest",
                    result="success",
                    correlation_id="it-migration-0001",
                    metadata={"phase": "integration"},
                ),
            )
        finally:
            await async_engine.dispose()

        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM audit_events WHERE id = :id"), {"id": event_id}
            ).scalar_one()
            assert count == 1

            # 5. append-only negative tests (database-level enforcement)
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(
                    text("UPDATE audit_events SET action = 'tampered' WHERE id = :id"),
                    {"id": event_id},
                )
            connection.rollback()
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(
                    text("DELETE FROM audit_events WHERE id = :id"), {"id": event_id}
                )
            connection.rollback()
            with pytest.raises(DBAPIError, match="append-only"):
                connection.execute(text("TRUNCATE audit_events"))
            connection.rollback()
    finally:
        engine.dispose()

    # 6. downgrade to base removes the table
    command.downgrade(config, "base")
    engine = create_engine(database_url)
    try:
        assert "audit_events" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    # 7. upgrade again is clean
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        assert "audit_events" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


# ===========================================================================
# 0019 / 0020 — a downgrade must not destroy the record of a decision
# ===========================================================================


def _revision(database_url: str) -> str:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return str(
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            )
    finally:
        engine.dispose()


def _seed_plan_with(database_url: str, entity_kind: str) -> tuple[str, int]:
    """One session, one plan, two items — one of them the kind under test.

    Written in raw SQL rather than through the service, because the point is
    what the SCHEMA does to rows that already exist when somebody rolls a
    migration back.
    """
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            scope_id = connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, display_name) "
                    "VALUES ('organization', 'migration-test', 'Migration Test') "
                    "ON CONFLICT (scope_type, external_ref) DO UPDATE SET "
                    "display_name = EXCLUDED.display_name RETURNING id"
                )
            ).scalar_one()
            identity_id = connection.execute(
                text(
                    "INSERT INTO identities (issuer, subject, display_name) "
                    "VALUES ('migration-test', 'migration-test', 'Migration Test') "
                    "RETURNING id"
                )
            ).scalar_one()
            installation_id = connection.execute(
                text(
                    "INSERT INTO github_installations (external_id, scope_id, account_login, "
                    "account_type, state) VALUES (912001, :scope, 'acme', 'Organization', "
                    "'active') RETURNING id"
                ),
                {"scope": scope_id},
            ).scalar_one()
            repository_id = connection.execute(
                text(
                    "INSERT INTO github_repositories (installation_id, scope_id, external_id, "
                    "full_name, owner_login, name, default_branch) VALUES "
                    "(:i, :scope, 912002, 'acme/widget', 'acme', 'widget', 'main') RETURNING id"
                ),
                {"i": installation_id, "scope": scope_id},
            ).scalar_one()
            session_id = connection.execute(
                text(
                    "INSERT INTO onboarding_sessions (repository_id, scope_id, state, "
                    "created_by) VALUES (:r, :scope, 'approved', :who) RETURNING id"
                ),
                {"r": repository_id, "scope": scope_id, "who": identity_id},
            ).scalar_one()
            analysis_id = connection.execute(
                text(
                    "INSERT INTO onboarding_analyses (session_id, repository_id, commit_sha, "
                    "analyzer_version, manifest_found, manifest_digest) VALUES "
                    "(:s, :r, :c, 1, true, 'manifest-digest') RETURNING id"
                ),
                {"s": session_id, "r": repository_id, "c": "a" * 40},
            ).scalar_one()
            plan_id = connection.execute(
                text(
                    "INSERT INTO onboarding_plans (session_id, analysis_id, plan_version, "
                    "commit_sha, manifest_digest, analyzer_version, plan_digest, state, "
                    "total_items) VALUES (:s, :a, 1, :c, 'manifest-digest', 1, :digest, "
                    "'ready', 2) RETURNING id"
                ),
                {"s": session_id, "a": analysis_id, "c": "a" * 40, "digest": "b" * 64},
            ).scalar_one()
            for kind, key in (("project", "project:widget"), (entity_kind, "other:widget")):
                connection.execute(
                    text(
                        "INSERT INTO onboarding_plan_items (plan_id, entity_kind, action, "
                        "item_key, detail) VALUES (:p, :k, 'create', :i, '{}'::jsonb)"
                    ),
                    {"p": plan_id, "k": kind, "i": key},
                )
        with engine.connect() as connection:
            count = int(
                connection.execute(
                    text("SELECT count(*) FROM onboarding_plan_items WHERE plan_id = :p"),
                    {"p": plan_id},
                ).scalar_one()
            )
        return str(plan_id), count
    finally:
        engine.dispose()


def _wipe(database_url: str) -> None:
    """Clear the onboarding tables and this file's own seed rows.

    Every other integration test starts by resetting these same tables (see
    `reset_catalog`), so nothing depends on rows a previous test left. These
    tests do not use that fixture and they downgrade the schema, so they
    have to do it themselves: 0019 now refuses to downgrade while any
    `workload_binding` plan item exists, and one left behind by an earlier
    test would block a downgrade that has nothing to do with it.

    A TRUNCATE ... CASCADE would reach tables this file knows nothing about,
    so the deletes are explicit and ordered by dependency.
    """
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            for table in (
                "onboarding_plan_items",
                "onboarding_applies",
                "onboarding_plans",
                "onboarding_findings",
                "onboarding_analyses",
                "onboarding_sessions",
            ):
                connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608 - fixed names
            for statement in (
                "DELETE FROM github_repository_projects WHERE repository_id IN "
                "(SELECT id FROM github_repositories WHERE external_id = 912002)",
                "DELETE FROM github_repositories WHERE external_id = 912002",
                "DELETE FROM github_installations WHERE external_id = 912001",
                "DELETE FROM identities WHERE issuer = 'migration-test'",
                "DELETE FROM scopes WHERE external_ref = 'migration-test'",
            ):
                connection.execute(text(statement))
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_onboarding_rows() -> None:
    """These tests move the schema, so they start from known rows."""
    settings = integration_settings()
    if settings is None:
        return
    command.upgrade(alembic_config(settings.database_url), "head")
    _wipe(settings.database_url)


def test_0019_downgrades_cleanly_when_no_binding_item_exists() -> None:
    """The ordinary case still works. A guard that never lets go is a wall."""
    database_url = require_database_url()
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    _wipe(database_url)
    _seed_plan_with(database_url, "service")

    command.downgrade(config, "0018")
    assert _revision(database_url) == "0018"
    command.upgrade(config, "head")
    _wipe(database_url)


def test_0019_refuses_to_downgrade_rather_than_delete_an_approved_plan() -> None:
    """A plan item is the evidence of what somebody approved.

    The first draft of this downgrade deleted every `workload_binding` item
    so the narrowed CHECK would fit. That silently removes part of an
    approved plan — and the digest the approval is checked against is
    computed over exactly those items, so the deletion breaks the integrity
    check for everything that remains.
    """
    database_url = require_database_url()
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    _wipe(database_url)
    plan_id, before = _seed_plan_with(database_url, "workload_binding")

    revision_before = _revision(database_url)
    with pytest.raises(Exception) as raised:
        command.downgrade(config, "0018")
    assert "workload_binding" in str(raised.value)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            after = int(
                connection.execute(
                    text("SELECT count(*) FROM onboarding_plan_items WHERE plan_id = :p"),
                    {"p": plan_id},
                ).scalar_one()
            )
            kinds = {
                row[0]
                for row in connection.execute(
                    text("SELECT entity_kind FROM onboarding_plan_items WHERE plan_id = :p"),
                    {"p": plan_id},
                ).all()
            }
            # The constraint is untouched too: a failed downgrade that left
            # the schema half-narrowed would be its own outage.
            connection.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = 'ck_onboarding_item_entity'")
            ).scalar_one()
    finally:
        engine.dispose()

    assert after == before, "no row may be lost to a refused downgrade"
    assert kinds == {"project", "workload_binding"}
    # Revision, schema and data all still agree. Compared against the
    # revision recorded BEFORE the attempt rather than a hard-coded number:
    # the point is that a refused downgrade moved nothing, and hard-coding
    # head means every future migration fails this test for a reason that
    # has nothing to do with what it guards.
    assert _revision(database_url) == revision_before
    _wipe(database_url)


def test_the_chain_walks_0018_to_head_and_back() -> None:
    """Every step, in both directions, on a database with no data in it."""
    database_url = require_database_url()
    config = alembic_config(database_url)
    command.upgrade(config, "head")
    _wipe(database_url)

    for revision in ("0019", "0018"):
        command.downgrade(config, revision)
        assert _revision(database_url) == revision
    for revision in ("0019", "0020"):
        command.upgrade(config, revision)
        assert _revision(database_url) == revision

    command.upgrade(config, "head")


def test_there_is_exactly_one_head() -> None:
    """Two heads is a merge nobody noticed writing."""
    from alembic.script import ScriptDirectory

    config = alembic_config(require_database_url())
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
