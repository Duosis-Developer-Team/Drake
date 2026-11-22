"""Who used an MCP server (Agent Visibility: per-call attribution).

An MCP server's tool span may name the person behind the call: OpenTelemetry
`user.id` (or the older `enduser.id`) and `user.name`. Hermes MCP sets both
for a token bound to a user. This keeps them per server, hour, tool, calling
client and person, with calls, failures and when that person was last seen —
so a server can say not only "claude-code called it" but who did.

A person is the id when there is one, else the name (`caller_key`). Names
only, never an email: discovery drops a value that looks like one, and the
table refuses it too. Unknown stays unknown — a call without either is not
counted here (the server's own rollups still count it).

Kept separately from `mcp_server_rollups` so the per-person breakdown never
crowds the server's own counts out of the store's row ceiling.

Additive; the downgrade drops what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_server_callers",
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hour", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("client", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("caller_key", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("errors", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "server_id", "hour", "tool_name", "client", "caller_key", name="pk_mcp_server_callers"
        ),
        sa.CheckConstraint(
            "length(caller_key) BETWEEN 1 AND 200", name="ck_mcp_server_caller_key_length"
        ),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR user_name IS NOT NULL", name="ck_mcp_server_caller_known"
        ),
        # Names, never emails.
        sa.CheckConstraint(
            "position('@' in coalesce(user_id, '') || coalesce(user_name, '')) = 0",
            name="ck_mcp_server_caller_no_email",
        ),
    )
    op.create_index("ix_mcp_server_callers_hour", "mcp_server_callers", ["hour"])


def downgrade() -> None:
    op.drop_index("ix_mcp_server_callers_hour", table_name="mcp_server_callers")
    op.drop_table("mcp_server_callers")
