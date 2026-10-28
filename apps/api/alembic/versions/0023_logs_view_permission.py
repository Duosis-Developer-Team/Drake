"""The `logs.view` permission (ADR-0029).

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-28

Permissions otherwise reach a database only when an operator runs one of the
bootstrap commands, which re-seed the catalog. A permission that exists in
code but not in the table cannot be granted, so a deployment would ship a
logs screen nobody can be given access to until someone remembers to run a
command that has nothing to do with logs. This migration makes the new key
exist the moment the code that checks it does.

It is granted to exactly one role: the `Platform Owner` system template,
which by definition holds every permission in the catalog. No other role is
widened. Adding a right to a starter template silently grants it to everyone
who already holds that role, and log content is the last place to do that.

Additive. No existing row is read or rewritten.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO permissions (key, description, catalog_version)
            VALUES ('logs.view', 'Read application logs of services in scope', 5)
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_key)
            SELECT id, 'logs.view' FROM roles
            WHERE name = 'Platform Owner' AND is_system
            ON CONFLICT (role_id, permission_key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Removes the Platform Owner mapping this migration added. If any OTHER
    # role was given `logs.view` since, the permission row's foreign key
    # refuses the delete below and the downgrade stops: removing someone's
    # deliberate grant to make a revision fit is not this file's decision.
    op.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_key = 'logs.view'
              AND role_id IN (SELECT id FROM roles WHERE name = 'Platform Owner' AND is_system)
            """
        )
    )
    op.execute(sa.text("DELETE FROM permissions WHERE key = 'logs.view'"))
