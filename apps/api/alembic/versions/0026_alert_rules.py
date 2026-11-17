"""Alert rules (ADR-0031): rules people write, the samples they are judged on.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-17

Four tables and three widened vocabularies:

- `service_health_samples`: one row per binding per incident-runner cycle,
  the signal values that cycle measured. Rules are judged on these, and the
  alert screen draws its charts and runs its "how often would this have
  fired" previews from them, so none of that queries the provider again.
- `alert_rules`: a rule is a signal, a comparison, a threshold, how long it
  must hold, and a severity, aimed at a project, an environment or one
  service.
- `alert_rule_states`: where each rule stands on each binding it covers.
- `alert_rule_events`: when it fired and when it resolved, which is the
  rule's history.

Incidents gain `rule` as a source and `rule_resolved` as a resolution source,
so a critical rule can open one and close it. The `alert_rule.manage`
permission is granted to the Platform Owner template only, the same way
0023 and 0024 added theirs.

Additive. No existing row is read or rewritten. The downgrade drops what
this adds and restores the two constraints.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNALS = (
    "'error_ratio', 'latency_p95_seconds', 'request_rate', 'restarts', "
    "'cpu_utilization', 'memory_utilization', 'unavailable_replicas'"
)


def upgrade() -> None:
    op.create_table(
        "service_health_samples",
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_workload_bindings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("error_ratio", sa.Float(), nullable=True),
        sa.Column("latency_p95_seconds", sa.Float(), nullable=True),
        sa.Column("request_rate", sa.Float(), nullable=True),
        sa.Column("restarts", sa.Float(), nullable=True),
        sa.Column("cpu_utilization", sa.Float(), nullable=True),
        sa.Column("memory_utilization", sa.Float(), nullable=True),
        sa.Column("unavailable_replicas", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("binding_id", "observed_at"),
    )
    op.create_index("ix_shs_observed_at", "service_health_samples", ["observed_at"])

    op.create_table(
        "alert_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("signal", sa.Text(), nullable=False),
        sa.Column("operator", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("for_seconds", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("muted_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
        sa.CheckConstraint(f"signal IN ({SIGNALS})", name="ck_ar_signal"),
        sa.CheckConstraint("operator IN ('>', '<')", name="ck_ar_operator"),
        sa.CheckConstraint("severity IN ('critical', 'warning')", name="ck_ar_severity"),
        sa.CheckConstraint("for_seconds BETWEEN 0 AND 86400", name="ck_ar_for_seconds"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_ar_name"),
        # A rule on one service names the environment too, so "this service"
        # is never ambiguous between dev and test.
        sa.CheckConstraint(
            "service_id IS NULL OR environment_id IS NOT NULL", name="ck_ar_service_needs_env"
        ),
    )
    op.create_index("ix_alert_rules_project", "alert_rules", ["project_id"])

    op.create_table(
        "alert_rule_states",
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_workload_bindings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("breach_since", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("firing_since", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_value", sa.Float(), nullable=True),
        sa.Column("evaluated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("rule_id", "binding_id"),
        sa.CheckConstraint("state IN ('ok', 'pending', 'firing')", name="ck_ars_state"),
    )

    op.create_table(
        "alert_rule_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_workload_bindings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("kind IN ('fired', 'resolved')", name="ck_are_kind"),
    )
    op.create_index("ix_are_rule_at", "alert_rule_events", ["rule_id", "at"])

    op.drop_constraint("ck_incident_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_source",
        "incidents",
        "source IN ('service_health', 'protection', 'alert', 'rule')",
    )
    op.drop_constraint("ck_incident_resolution_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_resolution_source",
        "incidents",
        "resolution_source IS NULL OR resolution_source IN "
        "('health_recovered', 'alert_resolved', 'protection_recovered', 'rule_resolved')",
    )

    op.execute(
        sa.text(
            """
            INSERT INTO permissions (key, description, catalog_version)
            VALUES ('alert_rule.manage', 'Create, change, mute and remove alert rules', 7)
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_key)
            SELECT id, 'alert_rule.manage' FROM roles
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
            WHERE permission_key = 'alert_rule.manage'
              AND role_id IN (SELECT id FROM roles WHERE name = 'Platform Owner' AND is_system)
            """
        )
    )
    op.execute(sa.text("DELETE FROM permissions WHERE key = 'alert_rule.manage'"))
    op.drop_constraint("ck_incident_resolution_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_resolution_source",
        "incidents",
        "resolution_source IS NULL OR resolution_source IN "
        "('health_recovered', 'alert_resolved', 'protection_recovered')",
    )
    op.drop_constraint("ck_incident_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_source",
        "incidents",
        "source IN ('service_health', 'protection', 'alert')",
    )
    op.drop_index("ix_are_rule_at", table_name="alert_rule_events")
    op.drop_table("alert_rule_events")
    op.drop_table("alert_rule_states")
    op.drop_index("ix_alert_rules_project", table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_index("ix_shs_observed_at", table_name="service_health_samples")
    op.drop_table("service_health_samples")
