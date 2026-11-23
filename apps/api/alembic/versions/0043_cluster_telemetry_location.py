"""Where a cluster's logs, traces and metrics live (ADR-0037).

`central` is the store Tracesis itself reads, which is every cluster so far.
`cluster` means the cluster's own stores, installed by the tracesis-agent
chart and read through that cluster's agent. Existing rows keep `central`,
so nothing is read differently after this migration.

Additive; the downgrade drops the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clusters",
        sa.Column(
            "telemetry_location",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'central'"),
        ),
    )
    op.create_check_constraint(
        "ck_clusters_telemetry_location",
        "clusters",
        "telemetry_location IN ('central', 'cluster')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_clusters_telemetry_location", "clusters", type_="check")
    op.drop_column("clusters", "telemetry_location")
