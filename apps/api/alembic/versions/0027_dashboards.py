"""Custom dashboards (ADR-0033): one per environment, every save a version.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-19

Two tables:

- `dashboards`: an environment's dashboard, its title and widget layout.
  Drake draws a default layout for every environment without a row; a row
  appears the first time someone saves a change. One per environment.
- `dashboard_versions`: every saved layout, kept. Restoring an old one saves
  it again as the newest, so history only ever grows.

Archiving sets `archived_at`; nothing here is deleted by the application.
Foreign keys are RESTRICT: removing a project or environment never takes a
dashboard's history with it silently.

The `dashboard.manage` permission is granted to the Platform Owner template
only, as 0026 did for alert rules. Seeing a dashboard needs no new
permission: it is `environment.view`, and every number on it still needs
`traces.view` or `logs.view` (ADR-0029).

Additive. No existing row is read or rewritten. The downgrade drops what
this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("layout", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="ck_dash_title"),
        sa.CheckConstraint("jsonb_typeof(layout) = 'array'", name="ck_dash_layout"),
        sa.CheckConstraint("version >= 1", name="ck_dash_version"),
        sa.UniqueConstraint("environment_id", name="uq_dashboard_environment"),
    )
    op.create_index("ix_dashboards_project", "dashboards", ["project_id"])

    op.create_table(
        "dashboard_versions",
        sa.Column(
            "dashboard_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboards.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("layout", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("saved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "saved_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("dashboard_id", "version"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 200", name="ck_dashv_note"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO permissions (key, description, catalog_version)
            VALUES ('dashboard.manage', 'Change and restore environment dashboards', 8)
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_key)
            SELECT id, 'dashboard.manage' FROM roles
            WHERE name = 'Platform Owner' AND is_system
            ON CONFLICT (role_id, permission_key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_key = 'dashboard.manage'
              AND role_id IN (SELECT id FROM roles WHERE name = 'Platform Owner' AND is_system)
            """
        )
    )
    op.execute(sa.text("DELETE FROM permissions WHERE key = 'dashboard.manage'"))
    op.drop_table("dashboard_versions")
    op.drop_index("ix_dashboards_project", table_name="dashboards")
    op.drop_table("dashboards")
