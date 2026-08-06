"""Catalog foundation: projects, clusters, environments, services, bindings,
project owners, integrations.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-06

Safety: no cascade deletes anywhere on catalog data (RESTRICT throughout —
archive is the lifecycle, history is never dropped); forward migration
touches no existing Sprint 0/1 tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _timestamps() -> list[sa.Column]:
    return [
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
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
    ]


def _provenance() -> list[sa.Column]:
    return [
        sa.Column("catalog_source_kind", sa.Text(), nullable=False),
        sa.Column("catalog_source_ref", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("source_revision", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "accepted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    ]


def _scope_ref() -> sa.Column:
    return sa.Column(
        "scope_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("scopes.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )


def upgrade() -> None:
    op.create_table(
        "projects",
        _uuid_pk(),
        sa.Column("project_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("repo_provider", sa.Text(), nullable=False),
        sa.Column("repo_owner", sa.Text(), nullable=False),
        sa.Column("repo_name", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("criticality", sa.Text(), nullable=False, server_default=sa.text("'medium'")),
        sa.Column("tenant_model", sa.Text(), nullable=False),
        *_provenance(),
        *_timestamps(),
        _scope_ref(),
        sa.UniqueConstraint("project_key", name="uq_project_key"),
        sa.CheckConstraint("lifecycle IN ('active', 'archived')", name="ck_project_lifecycle"),
        sa.CheckConstraint(
            "project_key ~ '^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$'", name="ck_project_key_shape"
        ),
        sa.CheckConstraint(
            "criticality IN ('low', 'medium', 'high', 'critical')",
            name="ck_project_criticality",
        ),
        sa.CheckConstraint(
            "tenant_model IN ('none','shared_table','schema_per_tenant',"
            "'database_per_tenant','external')",
            name="ck_project_tenant_model",
        ),
    )
    op.create_index(
        "uq_project_repo_active",
        "projects",
        ["repo_provider", "repo_owner", "repo_name"],
        unique=True,
        postgresql_where=sa.text("lifecycle = 'active'"),
    )

    op.create_table(
        "project_owners",
        _uuid_pk(),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("team_key", sa.Text(), nullable=False),
        sa.Column("owner_role", sa.Text(), nullable=False, server_default=sa.text("'primary'")),
        sa.UniqueConstraint("project_id", "team_key", "owner_role", name="uq_project_owner"),
        sa.CheckConstraint("owner_role IN ('primary', 'secondary')", name="ck_owner_role"),
    )

    op.create_table(
        "clusters",
        _uuid_pk(),
        sa.Column("cluster_ref", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("site", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        *_provenance(),
        *_timestamps(),
        _scope_ref(),
        sa.UniqueConstraint("cluster_ref", name="uq_cluster_ref"),
        sa.CheckConstraint("lifecycle IN ('active', 'archived')", name="ck_cluster_lifecycle"),
    )

    op.create_table(
        "environments",
        _uuid_pk(),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("environment_key", sa.Text(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("criticality", sa.Text(), nullable=False, server_default=sa.text("'medium'")),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("namespace", sa.Text(), nullable=True),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        *_provenance(),
        *_timestamps(),
        _scope_ref(),
        sa.UniqueConstraint("project_id", "environment_key", name="uq_environment_key"),
        sa.CheckConstraint("lifecycle IN ('active', 'archived')", name="ck_env_lifecycle"),
        sa.CheckConstraint("runtime IN ('kubernetes', 'external')", name="ck_env_runtime"),
        sa.CheckConstraint(
            "(runtime = 'kubernetes' AND cluster_id IS NOT NULL AND namespace IS NOT NULL) "
            "OR (runtime = 'external' AND cluster_id IS NULL)",
            name="ck_env_runtime_binding",
        ),
        sa.CheckConstraint(
            "criticality IN ('low', 'medium', 'high', 'critical')", name="ck_env_criticality"
        ),
    )
    op.create_index(
        "uq_env_cluster_namespace_active",
        "environments",
        ["cluster_id", "namespace"],
        unique=True,
        postgresql_where=sa.text("lifecycle = 'active' AND cluster_id IS NOT NULL"),
    )

    op.create_table(
        "service_definitions",
        _uuid_pk(),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("service_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False),
        sa.Column("metrics_profile", sa.Text(), nullable=False),
        sa.Column(
            "workload_selector",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "health", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        *_provenance(),
        *_timestamps(),
        sa.UniqueConstraint("project_id", "service_key", name="uq_service_key"),
        sa.CheckConstraint("lifecycle IN ('active', 'archived')", name="ck_service_lifecycle"),
    )

    op.create_table(
        "environment_services",
        _uuid_pk(),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "overrides", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        *_timestamps(),
        _scope_ref(),
        sa.UniqueConstraint("environment_id", "service_id", name="uq_environment_service"),
        sa.CheckConstraint("lifecycle IN ('active', 'archived')", name="ck_binding_lifecycle"),
    )

    op.create_table(
        "integrations",
        _uuid_pk(),
        sa.Column("integration_type", sa.Text(), nullable=False),
        sa.Column(
            "scope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scopes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "configuration_state",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'not_configured'"),
        ),
        sa.Column("observed_state", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        # Reference NAME into an external secret/config store — never a value,
        # and never exposed through the API.
        sa.Column("config_ref", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_sync_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.UniqueConstraint("integration_type", "scope_id", name="uq_integration_type_scope"),
        sa.CheckConstraint("lifecycle IN ('active', 'archived')", name="ck_integration_lifecycle"),
        sa.CheckConstraint(
            "configuration_state IN ('not_configured', 'configured')",
            name="ck_integration_config_state",
        ),
        sa.CheckConstraint(
            "observed_state IN ('unknown', 'not_configured', 'stale', 'degraded', 'ok')",
            name="ck_integration_observed_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("integrations")
    op.drop_table("environment_services")
    op.drop_table("service_definitions")
    op.drop_index("uq_env_cluster_namespace_active", table_name="environments")
    op.drop_table("environments")
    op.drop_table("clusters")
    op.drop_table("project_owners")
    op.drop_index("uq_project_repo_active", table_name="projects")
    op.drop_table("projects")
