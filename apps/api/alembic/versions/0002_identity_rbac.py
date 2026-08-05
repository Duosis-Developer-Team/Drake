"""Identity and dynamic RBAC foundation.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_uuid_pk = [
    sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
]


def upgrade() -> None:
    op.create_table(
        "identities",
        *_uuid_pk,
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("identity_type", sa.Text(), nullable=False, server_default=sa.text("'human'")),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("email", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("issuer", "subject", name="uq_identity_issuer_subject"),
    )

    op.create_table(
        "permissions",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("catalog_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "roles",
        *_uuid_pk,
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
        sa.UniqueConstraint("name", name="uq_role_name"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_role_status"),
    )

    op.create_table(
        "role_permissions",
        *_uuid_pk,
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission_key",
            sa.Text(),
            sa.ForeignKey("permissions.key", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint("role_id", "permission_key", name="uq_role_permission"),
    )

    op.create_table(
        "scopes",
        *_uuid_pk,
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scopes.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("scope_type", "external_ref", name="uq_scope_type_ref"),
        sa.CheckConstraint(
            "scope_type IN ('organization','cluster','project','environment','service','tenant')",
            name="ck_scope_type",
        ),
    )

    op.create_table(
        "group_mappings",
        *_uuid_pk,
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("group_object_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("issuer", "group_object_id", name="uq_group_mapping"),
    )

    op.create_table(
        "grants",
        *_uuid_pk,
        sa.Column(
            "identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "group_mapping_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("group_mappings.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "scope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scopes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "valid_from",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("valid_to", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(identity_id IS NOT NULL) != (group_mapping_id IS NOT NULL)",
            name="ck_grant_exactly_one_principal",
        ),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_grant_interval"),
    )
    op.create_index("ix_grants_identity", "grants", ["identity_id"])
    op.create_index("ix_grants_group_mapping", "grants", ["group_mapping_id"])
    op.create_index("ix_grants_scope", "grants", ["scope_id"])


def downgrade() -> None:
    op.drop_index("ix_grants_scope", table_name="grants")
    op.drop_index("ix_grants_group_mapping", table_name="grants")
    op.drop_index("ix_grants_identity", table_name="grants")
    op.drop_table("grants")
    op.drop_table("group_mappings")
    op.drop_table("scopes")
    op.drop_table("role_permissions")
    op.drop_table("roles")
    op.drop_table("permissions")
    op.drop_table("identities")
