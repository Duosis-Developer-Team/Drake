"""Alert rules that filter, and rules against a signal's normal (ADR-0035).

Revision ID: 0029
Revises: 0028

`alert_rules` gains two nullable filters:

- `log_contains`: a log rule counts only lines containing this text,
  case-insensitively.
- `route`: a trace rule watches only this endpoint.

It also gains a signal, `matching_logs` (lines containing a text, whatever
their severity), and two operators, `above_normal` and `below_normal`. Those
two judge a stored signal against the same time of day over the past week,
with `threshold` as the sensitivity.

Additive. No existing row is read or rewritten: every existing rule has no
filter and one of the two fixed operators, which keep their meaning. The
downgrade restores the narrower checks and drops the two columns; like the
checks themselves, it refuses while a rule still uses what it removes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SIGNALS = (
    "'error_ratio', 'latency_p95_seconds', 'request_rate', 'restarts', "
    "'cpu_utilization', 'memory_utilization', 'unavailable_replicas', "
    "'error_logs', 'fatal_logs', 'slow_requests', 'slowest_route_p95_seconds'"
)
NEW_SIGNALS = OLD_SIGNALS + ", 'matching_logs'"


def upgrade() -> None:
    op.add_column("alert_rules", sa.Column("log_contains", sa.Text(), nullable=True))
    op.add_column("alert_rules", sa.Column("route", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_ar_log_contains_length",
        "alert_rules",
        "log_contains IS NULL OR char_length(log_contains) BETWEEN 1 AND 200",
    )
    op.create_check_constraint(
        "ck_ar_route_length", "alert_rules", "route IS NULL OR char_length(route) BETWEEN 1 AND 300"
    )
    op.drop_constraint("ck_ar_signal", "alert_rules", type_="check")
    op.create_check_constraint("ck_ar_signal", "alert_rules", f"signal IN ({NEW_SIGNALS})")
    op.drop_constraint("ck_ar_operator", "alert_rules", type_="check")
    op.create_check_constraint(
        "ck_ar_operator",
        "alert_rules",
        "operator IN ('>', '<', 'above_normal', 'below_normal')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ar_operator", "alert_rules", type_="check")
    op.create_check_constraint("ck_ar_operator", "alert_rules", "operator IN ('>', '<')")
    op.drop_constraint("ck_ar_signal", "alert_rules", type_="check")
    op.create_check_constraint("ck_ar_signal", "alert_rules", f"signal IN ({OLD_SIGNALS})")
    op.drop_constraint("ck_ar_route_length", "alert_rules", type_="check")
    op.drop_constraint("ck_ar_log_contains_length", "alert_rules", type_="check")
    op.drop_column("alert_rules", "route")
    op.drop_column("alert_rules", "log_contains")
