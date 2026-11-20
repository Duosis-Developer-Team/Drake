"""Alert rules on logs and traces (ADR-0034).

Revision ID: 0028
Revises: 0027

`service_health_samples` gains four signals the incident runner reads from
the logs and traces store each cycle, next to the metric signals it already
records:

- `error_logs`, `fatal_logs`: log lines at ERROR or above, and at FATAL, in
  the last five minutes.
- `slow_requests`: requests that took a second or longer in that window.
- `slowest_route_p95_seconds`: the p95 of the slowest route in that window.

The columns are nullable: unknown is not zero, and rows written before this
revision have no value for them. `alert_rules.signal` accepts the four new
signal names.

Additive. No existing row is read or rewritten. The downgrade drops the
four columns and restores the narrower signal check; it refuses (as the
check itself would) while a rule still watches one of the new signals.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

METRIC_SIGNALS = (
    "'error_ratio', 'latency_p95_seconds', 'request_rate', 'restarts', "
    "'cpu_utilization', 'memory_utilization', 'unavailable_replicas'"
)
TELEMETRY_SIGNALS = "'error_logs', 'fatal_logs', 'slow_requests', 'slowest_route_p95_seconds'"
COLUMNS = ("error_logs", "fatal_logs", "slow_requests", "slowest_route_p95_seconds")


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("service_health_samples", sa.Column(column, sa.Float(), nullable=True))
    op.drop_constraint("ck_ar_signal", "alert_rules", type_="check")
    op.create_check_constraint(
        "ck_ar_signal", "alert_rules", f"signal IN ({METRIC_SIGNALS}, {TELEMETRY_SIGNALS})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_ar_signal", "alert_rules", type_="check")
    op.create_check_constraint("ck_ar_signal", "alert_rules", f"signal IN ({METRIC_SIGNALS})")
    for column in reversed(COLUMNS):
        op.drop_column("service_health_samples", column)
