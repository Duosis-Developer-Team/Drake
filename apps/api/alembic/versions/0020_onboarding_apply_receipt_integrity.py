"""The apply receipt records everything a retry has to return.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-09

Two defects in the receipt, both found by reading `0149a31` rather than by
a test:

**A retry did not return the same answer.** The receipt stored three
counters; the API returns seven. So the first apply reported
`metadata_updated: 3`, and the identical retried request reported `0` —
which breaks the one promise an idempotency key makes. Fixed by storing
every counter the response carries.

**Key reuse across plans was not a conflict.** Uniqueness was
`(plan_id, idempotency_key)`, so the same key under a *different* plan
version was treated as a different request and quietly applied. An
idempotency key is a client's statement that a request is the same request;
letting it mean a different one under a new plan turns a safety mechanism
into a way to apply an approval the client did not intend. Uniqueness moves
to `(session_id, idempotency_key)` — the natural scope — and a reuse under
another plan now collides in the database rather than in an application
pre-check that a race can lose.

Both are additive. Existing rows keep their three counters and default the
rest to zero; the read path treats a receipt written before this migration
as what it is — a receipt with unknown extended counters — rather than
inventing values it never recorded.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "onboarding_applies"
_OLD_CONSTRAINT = "uq_onboarding_apply_identity"
_NEW_CONSTRAINT = "uq_onboarding_apply_session_key"

# Every counter the apply response carries beyond the original three.
_COUNTERS = (
    "metadata_updated",
    "slo_definitions_created",
    "slo_definitions_updated",
    "bindings_created",
)


def upgrade() -> None:
    for name in _COUNTERS:
        op.add_column(
            _TABLE, sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
    # Marks a receipt written before this migration, so a retry of one can
    # say "these counters were never recorded" instead of returning zeros
    # that look like measured work.
    op.add_column(
        _TABLE,
        sa.Column(
            "counters_complete", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    for name in _COUNTERS:
        op.create_check_constraint(f"ck_onboarding_apply_{name}", _TABLE, f"{name} >= 0")

    # An idempotency key is scoped to the session, not to one plan version.
    # Doing this in the database is the point: an application pre-check
    # loses the race that matters.
    op.drop_constraint(_OLD_CONSTRAINT, _TABLE, type_="unique")
    op.create_unique_constraint(_NEW_CONSTRAINT, _TABLE, ["session_id", "idempotency_key"])


def downgrade() -> None:
    """Widen the key scope back and drop the extended counters.

    Unlike 0019, this does not refuse. The distinction is what would be
    lost. 0019's downgrade deleted plan ITEMS — the evidence of what
    somebody approved, and the input its digest is computed over — so
    refusing was the only safe answer. Here the receipts themselves survive
    with the three counters the older schema holds; what goes is four
    columns this migration added, which is the definition of undoing it.
    Refusing would instead make `downgrade base` impossible after any apply
    had ever run, and a schema you cannot roll back is its own risk.

    A receipt that comes back through a later re-upgrade reports
    `counters_complete = false`, so it says the extended counters are
    unknown rather than presenting a restored zero as a measurement.
    """
    op.drop_constraint(_NEW_CONSTRAINT, _TABLE, type_="unique")
    op.create_unique_constraint(_OLD_CONSTRAINT, _TABLE, ["plan_id", "idempotency_key"])
    for name in _COUNTERS:
        op.drop_constraint(f"ck_onboarding_apply_{name}", _TABLE, type_="check")
    op.drop_column(_TABLE, "counters_complete")
    for name in reversed(_COUNTERS):
        op.drop_column(_TABLE, name)
