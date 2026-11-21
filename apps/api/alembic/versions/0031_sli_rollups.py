"""SLOs measured from the requests Tracesis stores (traces).

Services here publish no HTTP metrics; their requests are server spans in
the trace store, which keeps 72 hours. An objective is judged over 30 days,
so what it needs is kept here instead: per binding and five-minute bucket,
how many requests were served, how many failed, how many had a measurable
duration, and how many of those took longer than each of a fixed set of
thresholds. Counts only — never a span, an attribute or a route.

A latency objective names one of the thresholds; an availability objective
uses requests and errors. Five minutes is the shortest burn window of the
standard profile, so the fast page can still be judged.

`slo.manage` lets a holder define objectives in the screen, granted to the
Platform Owner template only, like 0026's `alert_rule.manage`.

An objective burning its budget at a paging rate opens an incident like a
critical rule does: incidents gain `slo` as a source and `slo_recovered` as
a resolution source.

Additive. The downgrade drops what this adds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

THRESHOLDS_MS = (100, 250, 500, 1000, 2500, 5000)


def upgrade() -> None:
    op.create_table(
        "sli_rollups",
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_workload_bindings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bucket_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("requests", sa.BigInteger(), nullable=False),
        sa.Column("errors", sa.BigInteger(), nullable=False),
        sa.Column("timed", sa.BigInteger(), nullable=False),
        *(sa.Column(f"over_{ms}ms", sa.BigInteger(), nullable=False) for ms in THRESHOLDS_MS),
        sa.Column(
            "written_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("binding_id", "bucket_start", name="pk_sli_rollups"),
        sa.CheckConstraint(
            "requests >= 0 AND errors >= 0 AND errors <= requests AND timed >= 0 "
            "AND timed <= requests",
            name="ck_sli_rollup_counts",
        ),
        sa.CheckConstraint(
            "extract(epoch FROM bucket_start)::bigint % 300 = 0",
            name="ck_sli_rollup_bucket",
        ),
    )
    op.create_index("ix_sli_rollups_bucket", "sli_rollups", ["bucket_start"])
    op.drop_constraint("ck_incident_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_source",
        "incidents",
        "source IN ('service_health', 'protection', 'alert', 'rule', 'slo')",
    )
    op.drop_constraint("ck_incident_resolution_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_resolution_source",
        "incidents",
        "resolution_source IS NULL OR resolution_source IN "
        "('health_recovered', 'alert_resolved', 'protection_recovered', 'rule_resolved', "
        "'slo_recovered')",
    )
    op.execute(
        sa.text(
            """
            INSERT INTO permissions (key, description, catalog_version)
            VALUES ('slo.manage', 'Define, change and remove service objectives', 9)
            ON CONFLICT (key) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_key)
            SELECT id, 'slo.manage' FROM roles
            WHERE name = 'Platform Owner' AND is_system
            ON CONFLICT (role_id, permission_key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_key = 'slo.manage'
              AND role_id IN (SELECT id FROM roles WHERE name = 'Platform Owner' AND is_system)
            """
        )
    )
    op.execute(sa.text("DELETE FROM permissions WHERE key = 'slo.manage'"))
    op.drop_constraint("ck_incident_resolution_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_resolution_source",
        "incidents",
        "resolution_source IS NULL OR resolution_source IN "
        "('health_recovered', 'alert_resolved', 'protection_recovered', 'rule_resolved')",
    )
    op.drop_constraint("ck_incident_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_source",
        "incidents",
        "source IN ('service_health', 'protection', 'alert', 'rule')",
    )
    op.drop_index("ix_sli_rollups_bucket", table_name="sli_rollups")
    op.drop_table("sli_rollups")
