"""Inventory writer/generation model and two-phase certificate renewal.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-06

- cluster_inventory_state: ONE row per cluster — the serialization point
  for inventory writes. Tracks the single active writer (agent), the
  monotonic snapshot generation counter, the applied generation/snapshot,
  and the pending snapshot. Superseded agents and stale snapshots are
  refused against this row, never against heuristics.
- inventory_snapshots.generation: server-assigned monotonic generation;
  an older generation can never complete over a newer applied one.
- inventory_snapshot_pages.content_hash: replayed pages are idempotent
  only when their content matches; same page number with different
  content is a torn stream.
- cluster_agents pending_* columns: two-phase certificate renewal
  (prepare → activate). Only PUBLIC pending material is stored, with a
  bounded expiry; the current key keeps working until activation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cluster_inventory_state",
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "active_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cluster_agents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "current_generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "applied_generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("applied_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pending_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "applied_generation <= current_generation", name="ck_state_generation_order"
        ),
    )

    op.add_column(
        "inventory_snapshots",
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "inventory_snapshot_pages",
        sa.Column("content_hash", sa.Text(), nullable=False, server_default=sa.text("''")),
    )

    # Two-phase renewal: pending PUBLIC material only, bounded expiry.
    op.add_column("cluster_agents", sa.Column("pending_renewal_id", postgresql.UUID(as_uuid=True)))
    op.add_column("cluster_agents", sa.Column("pending_csr_hash", sa.Text(), nullable=True))
    op.add_column("cluster_agents", sa.Column("pending_public_key_pem", sa.Text(), nullable=True))
    op.add_column("cluster_agents", sa.Column("pending_certificate_pem", sa.Text(), nullable=True))
    op.add_column(
        "cluster_agents", sa.Column("pending_certificate_serial", sa.Text(), nullable=True)
    )
    op.add_column(
        "cluster_agents",
        sa.Column("pending_certificate_not_after", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "cluster_agents",
        sa.Column("pending_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cluster_agents", "pending_expires_at")
    op.drop_column("cluster_agents", "pending_certificate_not_after")
    op.drop_column("cluster_agents", "pending_certificate_serial")
    op.drop_column("cluster_agents", "pending_certificate_pem")
    op.drop_column("cluster_agents", "pending_public_key_pem")
    op.drop_column("cluster_agents", "pending_csr_hash")
    op.drop_column("cluster_agents", "pending_renewal_id")
    op.drop_column("inventory_snapshot_pages", "content_hash")
    op.drop_column("inventory_snapshots", "generation")
    op.drop_table("cluster_inventory_state")
