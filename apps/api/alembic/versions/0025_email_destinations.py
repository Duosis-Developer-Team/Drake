"""E-mail notification destinations (ADR-0030).

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-16

An e-mail destination is shaped exactly like a webhook one: an opaque KEY
into the operator's runtime registry, no address in the database. Its
deliveries use the existing webhook outbox, so no table is created and no
column is added. Two check constraints learn the third type:

- `ck_nd_type` admits `email`.
- `ck_nd_webhook_shape` becomes "a keyed type carries a key": webhook and
  email rows must hold `destination_key`, in-app rows must not. The
  in-app shape constraint is unchanged.

No existing row is read or rewritten: every row that satisfied the old
constraints satisfies the new ones. The downgrade restores the old ones and
therefore refuses while an `email` row exists, which is the honest outcome:
removing the type would otherwise orphan those rows.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_nd_type", "notification_destinations", type_="check")
    op.create_check_constraint(
        "ck_nd_type",
        "notification_destinations",
        "destination_type IN ('in_app_user', 'webhook', 'email')",
    )
    op.drop_constraint("ck_nd_webhook_shape", "notification_destinations", type_="check")
    op.create_check_constraint(
        "ck_nd_webhook_shape",
        "notification_destinations",
        "(destination_type IN ('webhook', 'email')) = (destination_key IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_nd_webhook_shape", "notification_destinations", type_="check")
    op.create_check_constraint(
        "ck_nd_webhook_shape",
        "notification_destinations",
        "(destination_type = 'webhook') = (destination_key IS NOT NULL)",
    )
    op.drop_constraint("ck_nd_type", "notification_destinations", type_="check")
    op.create_check_constraint(
        "ck_nd_type",
        "notification_destinations",
        "destination_type IN ('in_app_user', 'webhook')",
    )
