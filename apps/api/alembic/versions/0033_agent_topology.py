"""Who calls whom among agents, and where their tools live (Agent Visibility, phase 3b).

- `agent_edges`: per hour, how many times one agent ran inside another's
  run (a supervisor handing work to a sub-agent, an agent calling an
  agent-as-a-tool), and how many of those failed.
- `agent_tool_rollups.server`: the address the tool call went to
  (`server.address`) — an MCP server, an API — so tools can be grouped
  by where they run. Empty when the span does not say.

Counts and names only, like the rest of the agent tables.

Additive. The downgrade folds per-server tool rows back into one per tool
and drops what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_tool_rollups",
        sa.Column("server", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.drop_constraint("pk_agent_tool_rollups", "agent_tool_rollups", type_="primary")
    op.create_primary_key(
        "pk_agent_tool_rollups", "agent_tool_rollups", ["agent_id", "hour", "tool_name", "server"]
    )

    op.create_table(
        "agent_edges",
        sa.Column(
            "caller_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "callee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hour", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("errors", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("caller_id", "callee_id", "hour", name="pk_agent_edges"),
        sa.CheckConstraint("caller_id <> callee_id", name="ck_agent_edges_not_self"),
    )
    op.create_index("ix_agent_edges_callee", "agent_edges", ["callee_id", "hour"])


def downgrade() -> None:
    op.drop_index("ix_agent_edges_callee", table_name="agent_edges")
    op.drop_table("agent_edges")
    op.execute(
        sa.text(
            """
            INSERT INTO agent_tool_rollups (agent_id, hour, tool_name, server, calls, errors,
                                            duration_ms)
            SELECT agent_id, hour, tool_name, '', sum(calls), sum(errors), sum(duration_ms)
            FROM agent_tool_rollups WHERE server <> ''
            GROUP BY agent_id, hour, tool_name
            ON CONFLICT (agent_id, hour, tool_name, server) DO UPDATE SET
                calls = agent_tool_rollups.calls + EXCLUDED.calls,
                errors = agent_tool_rollups.errors + EXCLUDED.errors,
                duration_ms = agent_tool_rollups.duration_ms + EXCLUDED.duration_ms
            """
        )
    )
    op.execute(sa.text("DELETE FROM agent_tool_rollups WHERE server <> ''"))
    op.drop_constraint("pk_agent_tool_rollups", "agent_tool_rollups", type_="primary")
    op.drop_column("agent_tool_rollups", "server")
    op.create_primary_key(
        "pk_agent_tool_rollups", "agent_tool_rollups", ["agent_id", "hour", "tool_name"]
    )
