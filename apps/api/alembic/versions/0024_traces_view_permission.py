"""The `traces.view` permission (ADR-0029).

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-04

Same shape and same reasoning as 0023 (`logs.view`): the key must exist the
moment the code that checks it does, and it is granted to exactly one role,
the `Platform Owner` system template, which by definition holds every
permission in the catalog. No other role is widened.

Additive. No existing row is read or rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO permissions (key, description, catalog_version)
            VALUES ('traces.view', 'Read request traces of services in scope', 6)
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_key)
            SELECT id, 'traces.view' FROM roles
            WHERE name = 'Platform Owner' AND is_system
            ON CONFLICT (role_id, permission_key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Removes only the Platform Owner mapping this migration added. A grant of
    # `traces.view` to any other role makes the permission row's foreign key
    # refuse the delete below, and the downgrade stops rather than remove it.
    op.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_key = 'traces.view'
              AND role_id IN (SELECT id FROM roles WHERE name = 'Platform Owner' AND is_system)
            """
        )
    )
    op.execute(sa.text("DELETE FROM permissions WHERE key = 'traces.view'"))
