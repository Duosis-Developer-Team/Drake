"""MCP servers as things in the estate (Agent Visibility, phase 3c).

An MCP server is what agents call, not an agent: its spans are the server
side of `tools/call` and friends (OpenTelemetry MCP conventions —
`mcp.method.name` on a server span; Hermes MCP's `mcp.tool.name` tool spans). It appears here
the first time such a span arrives, bound to the catalog by its workload
like an agent.

Kept per server, hour, method, tool and calling client (the product name
of the client's User-Agent, `user_agent.name`): calls, failures and time.
Names and counts only.

`agent.view` reads them. Additive; the downgrade drops what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid(name: str, *args: object, **kw: object) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, **kw)


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        _uuid("id", primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("service_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("cluster_ref", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("namespace", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("workload", sa.Text(), nullable=False, server_default=sa.text("''")),
        _uuid(
            "binding_id",
            sa.ForeignKey("service_workload_bindings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _uuid("project_id", sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        _uuid(
            "environment_id", sa.ForeignKey("environments.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
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
        sa.UniqueConstraint("identity_key", name="uq_mcp_server_identity"),
        sa.CheckConstraint("length(identity_key) <= 512", name="ck_mcp_server_identity_length"),
    )
    op.create_index("ix_mcp_servers_project", "mcp_servers", ["project_id"])

    op.create_table(
        "mcp_server_rollups",
        _uuid("server_id", sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hour", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("client", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("errors", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint(
            "server_id", "hour", "method", "tool_name", "client", name="pk_mcp_server_rollups"
        ),
    )
    op.create_index("ix_mcp_server_rollups_hour", "mcp_server_rollups", ["hour"])


def downgrade() -> None:
    op.drop_index("ix_mcp_server_rollups_hour", table_name="mcp_server_rollups")
    op.drop_table("mcp_server_rollups")
    op.drop_index("ix_mcp_servers_project", table_name="mcp_servers")
    op.drop_table("mcp_servers")
