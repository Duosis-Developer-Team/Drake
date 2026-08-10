"""An explicit plan-item kind for service → workload bindings.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-09

Sprint 12A.1 landed workload bindings as `entity_kind = 'service'` with a
`binding` flag in `detail`, because that slice was scoped to add no
migration. It worked and it was wrong to keep:

- a service item and a binding item read as the same kind, so the plan a
  human reviews does not distinguish two genuinely different decisions;
- apply dispatch had to look inside a free-form `detail` object, which
  makes the handler registry's key a half-truth;
- every future API consumer, including the Sprint 12A.2 UI, would inherit
  the discriminator.

So the vocabulary gets the kind it needed. This migration widens exactly one
CHECK constraint and touches nothing else: no table, no column, no data.

Existing rows are unaffected — nothing has ever been written with the new
value, and every previously valid value stays valid.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_onboarding_item_entity"
_TABLE = "onboarding_plan_items"

_KINDS_0018 = (
    "'project', 'environment', 'service', 'owner_team', 'repository', "
    "'cluster_binding', 'namespace_binding', 'metric_profile', 'slo_profile', "
    "'deployment_source'"
)

_KINDS_0019 = _KINDS_0018 + ", 'workload_binding'"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"entity_kind IN ({_KINDS_0019})")


def downgrade() -> None:
    """Narrow the vocabulary again — and refuse rather than delete.

    An earlier draft deleted the rows that would violate the narrowed
    constraint. That is not a safe downgrade: a plan item is the evidence of
    what somebody approved, and the digest over those items is what makes an
    approval checkable afterwards. Removing part of an approved plan to make
    a schema change fit destroys the record of a decision.

    So this fails closed. An operator who genuinely wants to go back decides
    what happens to those plans first; the migration will not decide for
    them. Note that cancelling a session does not clear its plan items —
    the decision has to be about the items themselves.
    """
    conn = op.get_bind()
    # The table name is a module constant and the value is a literal, so
    # there is nothing here to inject through.
    query = f"SELECT count(*) FROM {_TABLE} WHERE entity_kind = 'workload_binding'"  # noqa: S608
    remaining = conn.exec_driver_sql(query).scalar_one()
    if remaining:
        raise RuntimeError(
            f"cannot downgrade 0019: {remaining} plan item(s) use the "
            "'workload_binding' kind. Downgrading would delete part of an "
            "approved plan and the evidence its digest was computed over. "
            "Remove those plan items deliberately, with a decision recorded "
            "for why the approvals they belong to no longer matter, before "
            "downgrading. Cancelling the sessions is not enough on its own: "
            "a cancelled session keeps its plan items."
        )
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"entity_kind IN ({_KINDS_0018})")
