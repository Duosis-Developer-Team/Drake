"""Development map: a hand-made portrait per person.

The map draws a person from their GitHub picture (an arcade sprite in the
browser). A portrait set here — one square image, made once, uploaded by an
operator with `python -m drake_api.dev_activity.portraits` — replaces that
picture everywhere the person appears. Keyed by the map's person key (an
alias person's key, or the GitHub login).

Additive; the downgrade drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dev_person_portraits",
        sa.Column("person_key", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("content", postgresql.BYTEA(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("person_key", name="pk_dev_person_portraits"),
        sa.CheckConstraint(
            "length(person_key) BETWEEN 1 AND 100 AND position('@' in person_key) = 0",
            name="ck_dev_portrait_key",
        ),
        sa.CheckConstraint(
            "media_type IN ('image/png', 'image/jpeg', 'image/webp')",
            name="ck_dev_portrait_media_type",
        ),
        sa.CheckConstraint(
            "octet_length(content) BETWEEN 1 AND 524288", name="ck_dev_portrait_size"
        ),
        sa.CheckConstraint(
            "width BETWEEN 64 AND 2048 AND height BETWEEN 64 AND 2048",
            name="ck_dev_portrait_dimensions",
        ),
    )


def downgrade() -> None:
    op.drop_table("dev_person_portraits")
