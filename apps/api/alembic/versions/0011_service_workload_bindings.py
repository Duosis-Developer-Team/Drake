"""Bind a catalog service to the Kubernetes workload that runs it.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-08

Drake already knows two things separately: the catalog (project →
environment → service) and the cluster inventory (cluster → namespace →
workload). Nothing joined them, so a service's health could only ever be
guessed at.

This is that join, stored explicitly rather than inferred from naming
conventions: one row says "this service is that Deployment in that
namespace on that cluster, read through that datasource". Health is then a
computation over signals, not a heuristic over names.

The binding stores no query. Which metrics are read comes from the curated
registry via `preset_key`, and how they are judged comes from a typed
policy via `health_policy_key`. Neither is user-supplied text, so a binding
can never become a way to run an arbitrary query.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same bounded shape the other tables use for machine-readable keys.
_KEY_SHAPE = "~ '^[a-z0-9][a-z0-9_.-]{0,63}$'"


def upgrade() -> None:
    op.create_table(
        "service_workload_bindings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # The catalog side. environment_service is the row that already
        # represents "this service, in this environment", so it is the
        # anchor; project and environment are carried for scope checks and
        # for the matchers a query is built from.
        sa.Column(
            "environment_service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environment_services.id", ondelete="RESTRICT"),
            nullable=False,
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
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # The infrastructure side.
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("workload_kind", sa.Text(), nullable=False),
        sa.Column("workload_name", sa.Text(), nullable=False),
        # Set once the workload is actually found in inventory. Null means
        # "not seen yet", which is reported as `unresolved` rather than
        # quietly treated as absent or as healthy.
        sa.Column("resolved_resource_uid", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Which curated metric preset and which typed policy. Keys, never
        # expressions: the binding cannot carry a query.
        sa.Column("preset_key", sa.Text(), nullable=False),
        sa.Column("health_policy_key", sa.Text(), nullable=False),
        # Which telemetry datasource answers for this workload. The
        # integration row holds the connection; nothing here does.
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # Disabled rather than deleted, matching the catalog's lifecycle.
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
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
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # Optimistic concurrency, as the catalog uses elsewhere: two
        # operators editing the same binding cannot silently overwrite.
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        # One live binding per (service-in-environment, cluster, namespace,
        # workload). A second identical one would produce two health
        # answers for the same thing.
        sa.UniqueConstraint(
            "environment_service_id",
            "cluster_id",
            "namespace",
            "workload_kind",
            "workload_name",
            name="uq_service_workload_binding_target",
        ),
        sa.CheckConstraint(
            "workload_kind IN ('Deployment', 'StatefulSet', 'DaemonSet')",
            name="ck_service_workload_binding_kind",
        ),
        sa.CheckConstraint(
            "lifecycle IN ('active', 'disabled')",
            name="ck_service_workload_binding_lifecycle",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_service_workload_binding_revision"),
        # Kubernetes name rules, enforced in the database as well as the
        # application: this value becomes a query label matcher, so it must
        # never be able to carry anything but a name.
        sa.CheckConstraint(
            "namespace ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'",
            name="ck_service_workload_binding_namespace",
        ),
        sa.CheckConstraint(
            "workload_name ~ '^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$'",
            name="ck_service_workload_binding_name",
        ),
        sa.CheckConstraint(f"preset_key {_KEY_SHAPE}", name="ck_service_workload_binding_preset"),
        sa.CheckConstraint(
            f"health_policy_key {_KEY_SHAPE}", name="ck_service_workload_binding_policy"
        ),
    )
    op.create_index(
        "ix_service_workload_bindings_service",
        "service_workload_bindings",
        ["environment_service_id"],
    )
    op.create_index(
        "ix_service_workload_bindings_cluster", "service_workload_bindings", ["cluster_id"]
    )
    op.create_index(
        "ix_service_workload_bindings_env", "service_workload_bindings", ["environment_id"]
    )


def downgrade() -> None:
    op.drop_table("service_workload_bindings")
