"""Effective-time semantics and canonical destination identity.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-08

Two acceptance gaps from Sprint 7, both of which needed schema to close
properly rather than application care.

**When a policy starts applying.** Freezing a delivery's payload was not
enough: an incident event that had not been planned yet would still be
matched by a policy created — or re-scoped — after it happened. So a
policy and each of its destination bindings now carry `effective_from`,
and the planner compares it against the event's immutable `created_at`.
Correctness no longer depends on anyone remembering to run a baseline
command before turning notifications on.

**Who the recipient really is.** Uniqueness on `(event, destination_row)`
only prevents duplicates per destination ROW. Two rows naming the same
person, or two rows pointing at the same webhook key, would each produce
their own notification. Uniqueness now sits on the canonical target — the
recipient identity for in-app, the runtime destination key for webhooks —
so the guarantee is "one per event per final recipient per channel", which
is what a person on the receiving end actually experiences.

Additive: 0013 is left as it was applied. The two indexes it created are
replaced here because their guarantee was too narrow, not wrong.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- effective time --------------------------------------------------
    # Existing rows get `now()`, which is the conservative answer: an
    # already-configured policy starts applying from this migration
    # forward, never retroactively.
    op.add_column(
        "notification_policies",
        sa.Column(
            "effective_from",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "notification_policy_destinations",
        sa.Column(
            "effective_from",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_notification_policies_effective",
        "notification_policies",
        ["project_id", "effective_from"],
    )

    # --- canonical destination identity -----------------------------------
    # In-app: the recipient identity IS the canonical target. Two policies,
    # or two destination rows, naming the same person now collide here.
    op.drop_constraint("uq_in_app_event_destination", "in_app_notifications", type_="unique")
    op.create_unique_constraint(
        "uq_in_app_event_recipient",
        "in_app_notifications",
        ["incident_event_id", "recipient_identity_id"],
    )

    # Webhook: the runtime destination key is the canonical target. Two
    # destination rows pointing at the same operator endpoint are one
    # endpoint, and it should be called once.
    op.drop_constraint("uq_webhook_event_destination", "webhook_deliveries", type_="unique")
    op.create_unique_constraint(
        "uq_webhook_event_destination_key",
        "webhook_deliveries",
        ["incident_event_id", "destination_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_webhook_event_destination_key", "webhook_deliveries", type_="unique")
    op.create_unique_constraint(
        "uq_webhook_event_destination",
        "webhook_deliveries",
        ["incident_event_id", "destination_id"],
    )
    op.drop_constraint("uq_in_app_event_recipient", "in_app_notifications", type_="unique")
    op.create_unique_constraint(
        "uq_in_app_event_destination",
        "in_app_notifications",
        ["incident_event_id", "destination_id"],
    )
    op.drop_index("ix_notification_policies_effective", table_name="notification_policies")
    op.drop_column("notification_policy_destinations", "effective_from")
    op.drop_column("notification_policies", "effective_from")
