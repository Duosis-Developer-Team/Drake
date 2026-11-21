"""Governance of AI agents (Agent Visibility, phase 5).

Every agent is found on first sight, which makes it visible, not
accepted. This adds what an organisation decides about one:

- `purpose`: what the agent is for, in words (what it supports);
- the owner (`owner_identity_id`, already present since 0032);
- the lifecycle (`observed` → `approved` → `retired`, present since 0032),
  now with who changed it and when.

An agent nobody approved, that runs outside the catalog or is seen only on
the wire, is shadow AI; the screens list it for review. Retired agents
leave the inventory, and are flagged if they are seen again.

Additive; the downgrade drops what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("purpose", sa.Text(), nullable=True))
    op.add_column(
        "agents", sa.Column("lifecycle_changed_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "agents",
        sa.Column(
            "lifecycle_changed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_agent_purpose_length", "agents", "purpose IS NULL OR length(purpose) <= 500"
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_purpose_length", "agents", type_="check")
    op.drop_column("agents", "lifecycle_changed_by")
    op.drop_column("agents", "lifecycle_changed_at")
    op.drop_column("agents", "purpose")
