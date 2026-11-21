"""Guardrails on AI agents (Agent Visibility, phase 5).

A guardrail is a line an agent should not cross, set per agent:

- `failure_ratio`  — more than this share of its calls failed (last two hours);
- `avg_run_seconds` — its runs took longer than this on average (last two hours);
- `cost_day`       — it cost more than this many US dollars (last 24 hours);
- `silence_hours`  — it has not been seen for this many hours.

`agent_guardrails` holds what was set (one per agent and kind),
`agent_guardrail_states` what the evaluator last found, and
`agent_guardrail_events` when each one fired and resolved. A critical
guardrail on an agent that belongs to a project environment opens an
incident (new source `agent`, resolved as `agent_recovered`), so the
existing notification policies route it.

`agent.manage` sets guardrails; granted to the Platform Owner template.

Additive. The downgrade removes agent incidents (their events cascade)
before restoring the incident source list, then drops what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCES = "'service_health', 'protection', 'alert', 'rule', 'slo'"
_RESOLUTIONS = (
    "'health_recovered', 'alert_resolved', 'protection_recovered', 'rule_resolved', 'slo_recovered'"
)


def _uuid(name: str, *args: object, **kw: object) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, **kw)


def upgrade() -> None:
    op.create_table(
        "agent_guardrails",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _uuid("agent_id", sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Numeric(14, 4), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'warning'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _uuid("created_by", sa.ForeignKey("identities.id", ondelete="SET NULL"), nullable=True),
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
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("agent_id", "kind", name="uq_agent_guardrail_kind"),
        sa.CheckConstraint(
            "kind IN ('failure_ratio', 'avg_run_seconds', 'cost_day', 'silence_hours')",
            name="ck_agent_guardrail_kind",
        ),
        sa.CheckConstraint(
            "severity IN ('warning', 'critical')", name="ck_agent_guardrail_severity"
        ),
        sa.CheckConstraint(
            "threshold > 0 AND (kind <> 'failure_ratio' OR threshold <= 1)",
            name="ck_agent_guardrail_threshold",
        ),
    )
    op.create_table(
        "agent_guardrail_states",
        _uuid(
            "guardrail_id",
            sa.ForeignKey("agent_guardrails.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=True),
        sa.Column("since", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        _uuid("incident_id", sa.ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("state IN ('ok', 'firing', 'no_data')", name="ck_agent_guardrail_state"),
    )
    op.create_table(
        "agent_guardrail_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        _uuid(
            "guardrail_id",
            sa.ForeignKey("agent_guardrails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=True),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("event IN ('fired', 'resolved')", name="ck_agent_guardrail_event"),
    )
    op.create_index(
        "ix_agent_guardrail_events_rule", "agent_guardrail_events", ["guardrail_id", "at"]
    )

    op.drop_constraint("ck_incident_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_source", "incidents", f"source IN ({_SOURCES}, 'agent')"
    )
    op.drop_constraint("ck_incident_resolution_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_resolution_source",
        "incidents",
        f"resolution_source IS NULL OR resolution_source IN ({_RESOLUTIONS}, 'agent_recovered')",
    )

    op.execute(
        sa.text(
            """
            INSERT INTO permissions (key, description, catalog_version)
            VALUES ('agent.manage', 'Set and change guardrails on AI agents', 11)
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_key)
            SELECT id, 'agent.manage' FROM roles WHERE name = 'Platform Owner' AND is_system
            ON CONFLICT (role_id, permission_key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_key = 'agent.manage'
              AND role_id IN (SELECT id FROM roles WHERE name = 'Platform Owner' AND is_system)
            """
        )
    )
    op.execute(sa.text("DELETE FROM permissions WHERE key = 'agent.manage'"))
    op.execute(sa.text("DELETE FROM incidents WHERE source = 'agent'"))
    op.drop_constraint("ck_incident_resolution_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_resolution_source",
        "incidents",
        f"resolution_source IS NULL OR resolution_source IN ({_RESOLUTIONS})",
    )
    op.drop_constraint("ck_incident_source", "incidents", type_="check")
    op.create_check_constraint("ck_incident_source", "incidents", f"source IN ({_SOURCES})")
    op.drop_index("ix_agent_guardrail_events_rule", table_name="agent_guardrail_events")
    op.drop_table("agent_guardrail_events")
    op.drop_table("agent_guardrail_states")
    op.drop_table("agent_guardrails")
