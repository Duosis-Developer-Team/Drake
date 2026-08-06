"""Catalog integrity hardening.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-06

- Cross-project binding protection: environment_services carries the
  authoritative project_id, backfilled from environments, guarded by
  composite foreign keys to BOTH parents — PostgreSQL now rejects any
  binding whose environment and service definition belong to different
  projects. Existing invalid rows make the migration FAIL (no silent
  fixes, no deletions); valid rows are preserved. RESTRICT policy kept.
- Safe error-code boundary: integrations.last_error_code becomes a bounded
  machine-readable code (no newlines/URLs/free text) at the DB level.
- Key-shape checks for externally-sourced stable refs (environment key,
  service key, cluster ref, team key, integration type). Adding the CHECKs
  validates existing rows — invalid data fails the migration (fail-closed).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY_SHAPE = "~ '^[a-z0-9]([a-z0-9_.-]{0,62}[a-z0-9])?$'"
_ERROR_CODE_SHAPE = "~ '^[a-z0-9][a-z0-9_.-]{0,63}$'"


def upgrade() -> None:
    # Composite identity targets for the dual foreign keys.
    op.create_unique_constraint("uq_environment_id_project", "environments", ["id", "project_id"])
    op.create_unique_constraint(
        "uq_service_definition_id_project", "service_definitions", ["id", "project_id"]
    )

    # Authoritative project on bindings: backfill, then fail-closed audit.
    op.add_column(
        "environment_services",
        sa.Column("project_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE environment_services b
        SET project_id = e.project_id
        FROM environments e
        WHERE e.id = b.environment_id
        """
    )
    # Any pre-existing cross-project binding is a data-integrity incident:
    # refuse to migrate rather than silently "fixing" or deleting it.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM environment_services b
                JOIN service_definitions s ON s.id = b.service_id
                WHERE b.project_id IS DISTINCT FROM s.project_id
            ) THEN
                RAISE EXCEPTION
                    'cross-project service bindings detected; manual review required';
            END IF;
        END
        $$;
        """
    )
    op.alter_column("environment_services", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_binding_environment_project",
        "environment_services",
        "environments",
        ["environment_id", "project_id"],
        ["id", "project_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_binding_service_project",
        "environment_services",
        "service_definitions",
        ["service_id", "project_id"],
        ["id", "project_id"],
        ondelete="RESTRICT",
    )

    # Bounded machine-readable error codes.
    op.create_check_constraint(
        "ck_integration_error_code",
        "integrations",
        f"last_error_code IS NULL OR last_error_code {_ERROR_CODE_SHAPE}",
    )

    # Key-shape boundaries for externally-sourced stable refs.
    op.create_check_constraint(
        "ck_environment_key_shape", "environments", f"environment_key {_KEY_SHAPE}"
    )
    op.create_check_constraint(
        "ck_service_key_shape", "service_definitions", f"service_key {_KEY_SHAPE}"
    )
    op.create_check_constraint("ck_cluster_ref_shape", "clusters", f"cluster_ref {_KEY_SHAPE}")
    op.create_check_constraint("ck_team_key_shape", "project_owners", f"team_key {_KEY_SHAPE}")
    op.create_check_constraint(
        "ck_integration_type_shape", "integrations", f"integration_type {_KEY_SHAPE}"
    )


def downgrade() -> None:
    op.drop_constraint("ck_integration_type_shape", "integrations", type_="check")
    op.drop_constraint("ck_team_key_shape", "project_owners", type_="check")
    op.drop_constraint("ck_cluster_ref_shape", "clusters", type_="check")
    op.drop_constraint("ck_service_key_shape", "service_definitions", type_="check")
    op.drop_constraint("ck_environment_key_shape", "environments", type_="check")
    op.drop_constraint("ck_integration_error_code", "integrations", type_="check")
    op.drop_constraint("fk_binding_service_project", "environment_services", type_="foreignkey")
    op.drop_constraint("fk_binding_environment_project", "environment_services", type_="foreignkey")
    op.drop_column("environment_services", "project_id")
    op.drop_constraint("uq_service_definition_id_project", "service_definitions", type_="unique")
    op.drop_constraint("uq_environment_id_project", "environments", type_="unique")
