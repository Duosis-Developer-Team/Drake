"""Alembic environment.

The URL comes from ``sqlalchemy.url`` when set programmatically (tests) or
from the ``DRAKE_DATABASE_URL`` environment variable. Migrations only ever run
against local/disposable databases in Sprint 0.
"""

import os

from alembic import context
from drake_api.audit import models as audit_models  # noqa: F401  (registers tables)
from drake_api.db import Base
from sqlalchemy import engine_from_config, pool

config = context.config

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", os.environ.get("DRAKE_DATABASE_URL", ""))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
