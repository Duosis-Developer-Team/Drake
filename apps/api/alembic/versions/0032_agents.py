"""AI agents as managed things in the estate (Tracesis Agent Visibility, phase 2).

An agent appears here the first time telemetry shows it — no registration.
Its identity is taken from the most trustworthy signal available and the
confidence of that identity is kept beside it:

- `stable`   — a provider-assigned agent id (`gen_ai.agent.id`);
- `named`    — a low-cardinality agent name within a service;
- `service`  — only the service that calls a model;
- `observed` — only seen on the wire by eBPF, no instrumentation at all.

Kept here, because the trace store holds 72 hours: per agent, hour, provider
and model, how many invocations, model calls, tool calls and failures, and
the tokens by kind (input including cache, output including reasoning, and
the cache-read / cache-write / reasoning subsets). Per agent, hour and tool,
the tool calls. Counts and names only: no prompt, answer or argument ever
reaches Tracesis (the gateway removes them).

`agent.view` reads them, granted to the Platform Owner template only.

Additive. The downgrade drops what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid(name: str, *args: object, **kw: object) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, **kw)


def upgrade() -> None:
    op.create_table(
        "agents",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("agent_external_id", sa.Text(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("service_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("cluster_ref", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("namespace", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("workload", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("ingest", sa.Text(), nullable=False, server_default=sa.text("'cluster'")),
        _uuid(
            "binding_id",
            sa.ForeignKey("service_workload_bindings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _uuid("project_id", sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        _uuid(
            "environment_id",
            sa.ForeignKey("environments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'observed'")),
        _uuid(
            "owner_identity_id",
            sa.ForeignKey("identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("identity_key", name="uq_agent_identity"),
        sa.CheckConstraint(
            "confidence IN ('stable', 'named', 'service', 'observed')", name="ck_agent_confidence"
        ),
        sa.CheckConstraint(
            "lifecycle IN ('observed', 'approved', 'retired')", name="ck_agent_lifecycle"
        ),
        sa.CheckConstraint("ingest IN ('cluster', 'external')", name="ck_agent_ingest"),
        sa.CheckConstraint("length(identity_key) <= 512", name="ck_agent_identity_length"),
    )
    op.create_index("ix_agents_project", "agents", ["project_id"])
    op.create_index("ix_agents_last_seen", "agents", ["last_seen_at"])

    counts = [
        sa.Column(name, sa.BigInteger(), nullable=False, server_default=sa.text("0"))
        for name in (
            "invocations",
            "inference_calls",
            "tool_calls",
            "errors",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "duration_ms",
        )
    ]
    op.create_table(
        "agent_rollups",
        _uuid("agent_id", sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hour", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("model", sa.Text(), nullable=False, server_default=sa.text("''")),
        *counts,
        sa.PrimaryKeyConstraint("agent_id", "hour", "provider", "model", name="pk_agent_rollups"),
        sa.CheckConstraint(
            "extract(epoch FROM hour)::bigint % 3600 = 0", name="ck_agent_rollup_hour"
        ),
    )
    op.create_index("ix_agent_rollups_hour", "agent_rollups", ["hour"])

    op.create_table(
        "agent_tool_rollups",
        _uuid("agent_id", sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hour", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("errors", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("agent_id", "hour", "tool_name", name="pk_agent_tool_rollups"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO permissions (key, description, catalog_version)
            VALUES ('agent.view', 'View AI agents, their activity, models, tools and usage', 10)
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_key)
            SELECT id, 'agent.view' FROM roles
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
            WHERE permission_key = 'agent.view'
              AND role_id IN (SELECT id FROM roles WHERE name = 'Platform Owner' AND is_system)
            """
        )
    )
    op.execute(sa.text("DELETE FROM permissions WHERE key = 'agent.view'"))
    op.drop_table("agent_tool_rollups")
    op.drop_index("ix_agent_rollups_hour", table_name="agent_rollups")
    op.drop_table("agent_rollups")
    op.drop_index("ix_agents_last_seen", table_name="agents")
    op.drop_index("ix_agents_project", table_name="agents")
    op.drop_table("agents")
