"""An index for the rows that left the cluster.

Revision ID: 0030
Revises: 0029

Missing rows leave `inventory_resources` after their retention (the
maintenance pass and `prune_inventory_cli`). Without an index, finding
them is a sequential scan of the whole table, and while deleted rows are
still physically there each batch walks past every row the previous
batches removed. On drake-prod (4.3 M rows, 3.4 GB) one such scan reads
about 2.6 GB from disk and takes 6.5 s, which on a node shared with
applications is load to avoid, not merely time.

A partial index on `(cluster_id, last_seen_at) WHERE lifecycle = 'missing'`
lets each batch take the oldest qualifying rows directly. It holds only
missing rows, so in steady state (a day of events, a week of the rest)
it stays small, and active rows, which is everything users see, never
enter it.

Built CONCURRENTLY so ingest is not blocked while it builds. Additive;
the downgrade drops it the same way.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        # An interrupted concurrent build leaves an INVALID index behind;
        # dropping it first makes a re-run clean.
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_inventory_missing_since")
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_inventory_missing_since "
            "ON inventory_resources (cluster_id, last_seen_at) "
            "WHERE lifecycle = 'missing'"
        )


def downgrade() -> None:
    # Inside the migration's transaction, not an autocommit block: an
    # autocommit block commits everything downgraded before it, so a later
    # migration that refuses its downgrade would leave the revision and the
    # schema disagreeing. A downgrade is not a hot path; the brief lock of a
    # plain DROP INDEX is the price of it being all-or-nothing.
    op.execute("DROP INDEX IF EXISTS ix_inventory_missing_since")
