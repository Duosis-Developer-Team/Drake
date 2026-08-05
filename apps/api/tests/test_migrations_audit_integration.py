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
