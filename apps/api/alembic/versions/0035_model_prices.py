"""Model prices, versioned in time (Agent Visibility, phase 4).

What an agent's tokens cost is read from a public price list (LiteLLM's
model price table by default), fetched periodically and kept here:

- `model_price_fetches`: every attempt — when, from where, the document's
  sha256, how many models, and whether it was the live list, the bundled
  fallback or a failure;
- `model_prices`: per model key, USD per million tokens for input, cached
  input read, cache write, output and reasoning output, valid from a time
  until another price replaces it. A changed price closes the old row and
  opens a new one, so an hour is always costed at the price in force then;
- `model_price_matches`: which price key each (provider, model) seen in
  telemetry resolved to, and how — or that it resolved to none.

Additive; the downgrade drops what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USD = sa.Numeric(14, 6)


def upgrade() -> None:
    op.create_table(
        "model_price_fetches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("models", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("changed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('live', 'fallback', 'failed')", name="ck_model_price_fetch_status"
        ),
    )
    op.create_index("ix_model_price_fetches_at", "model_price_fetches", ["fetched_at"])

    op.create_table(
        "model_prices",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("mode", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("input_usd", _USD, nullable=False),
        sa.Column("output_usd", _USD, nullable=False),
        sa.Column("cache_read_usd", _USD, nullable=True),
        sa.Column("cache_write_usd", _USD, nullable=True),
        sa.Column("reasoning_usd", _USD, nullable=True),
        sa.Column("valid_from", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("valid_to", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "fetch_id",
            sa.BigInteger(),
            sa.ForeignKey("model_price_fetches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_model_price_span"),
    )
    op.create_index(
        "uq_model_prices_current",
        "model_prices",
        ["model_key"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index("ix_model_prices_key_from", "model_prices", ["model_key", "valid_from"])

    op.create_table(
        "model_price_matches",
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_key", sa.Text(), nullable=True),
        sa.Column("matched_by", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider", "model", name="pk_model_price_matches"),
    )


def downgrade() -> None:
    op.drop_table("model_price_matches")
    op.drop_index("ix_model_prices_key_from", table_name="model_prices")
    op.drop_index("uq_model_prices_current", table_name="model_prices")
    op.drop_table("model_prices")
    op.drop_index("ix_model_price_fetches_at", table_name="model_price_fetches")
    op.drop_table("model_price_fetches")
