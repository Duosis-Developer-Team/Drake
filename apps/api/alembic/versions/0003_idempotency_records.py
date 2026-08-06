"""Transactional idempotency records for RBAC mutations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-06

PostgreSQL is the single idempotency authority: the claim, the domain
mutation, the audit row, and the stored response commit in ONE transaction.
The unique constraint (actor, operation, key) is what serializes concurrent
replicas — an application lock could not.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "actor_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'in_progress'")),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
        sa.UniqueConstraint(
            "actor_identity_id", "operation", "idempotency_key", name="uq_idempotency_claim"
        ),
        sa.CheckConstraint("status IN ('in_progress', 'completed')", name="ck_idempotency_status"),
    )
    op.create_index("ix_idempotency_expires_at", "idempotency_records", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_expires_at", table_name="idempotency_records")
    op.drop_table("idempotency_records")
