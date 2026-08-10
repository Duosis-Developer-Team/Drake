"""Dependencies a project has but Drake does not run.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-11

`dataStores` existed in the manifest schema and nowhere else — no table, no
plan item, no API field. So a provider-managed database survived validation
and drift and then vanished at import, which made `provider` and
`verification` decorative.

This is its own table rather than a column on an existing one. A managed
data platform is not a service (no workload, no replicas, nothing to
restart) and not an in-cluster datastore (Drake does not operate it), and
putting it in either table would have meant a nullable discriminator plus
every reader remembering to check it. The rows that share a shape share a
table.

**Verification is deliberately NOT free.** `repository_intent`,
`owner_confirmed` and `provider_observed` are three different claims, and
only the first can be established by reading a repository. The import path
records `repository_intent` regardless of what a manifest asserts, because
a repository claiming that Drake observed something is not evidence that
Drake observed anything. The column accepts the higher values so that an
out-of-band confirmation can set them later.

**No credential material.** No connection string, no secret reference, no
endpoint. A dependency row says what kind of thing exists and who operates
it; anything needed to reach it lives where it already lives.

Backward compatible: additive, and no existing row is read or rewritten.
Existing in-cluster datastores are not backfilled from anywhere, because
nothing was persisted before this — there is no history to reinterpret and
no provider to guess.

The downgrade drops the table, which is lossy by definition. It is safe
exactly when nothing depends on those rows: they are re-derivable by
re-applying the manifest that created them, which is why this one may drop
rather than refuse.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLASSES = ("in_cluster", "managed_data_platform", "external_service")
_VERIFICATION = ("repository_intent", "owner_confirmed", "provider_observed")
# Mirrors HostingProvider and the manifest schema's provider enum.
_PROVIDERS = (
    "vercel",
    "netlify",
    "cloudflare",
    "aws",
    "gcp",
    "azure",
    "fly",
    "render",
    "heroku",
    "supabase",
    "self-managed",
    "other",
    "unknown",
)


_PLAN_ITEMS = "onboarding_plan_items"
_ENTITY_CONSTRAINT = "ck_onboarding_item_entity"
_KINDS_0019 = (
    "'project', 'environment', 'service', 'owner_team', 'repository', "
    "'cluster_binding', 'namespace_binding', 'metric_profile', 'slo_profile', "
    "'deployment_source', 'workload_binding'"
)
_KINDS_0022 = _KINDS_0019 + ", 'dependency'"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "project_dependencies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("dependency_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("dependency_class", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("store_scope", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column(
            "verification",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'repository_intent'"),
        ),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("catalog_source_kind", sa.Text(), nullable=False),
        sa.Column("catalog_source_ref", sa.Text(), nullable=False),
        sa.Column("source_revision", sa.Text(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Identity is (project, key): the same dependency name under two
        # different projects is two different things and must not collide.
        sa.UniqueConstraint("project_id", "dependency_key", name="uq_project_dependency_key"),
        sa.CheckConstraint(
            f"dependency_class IN ({_quoted(_CLASSES)})", name="ck_dependency_class"
        ),
        sa.CheckConstraint(
            f"verification IN ({_quoted(_VERIFICATION)})", name="ck_dependency_verification"
        ),
        sa.CheckConstraint(
            f"provider IS NULL OR provider IN ({_quoted(_PROVIDERS)})",
            name="ck_dependency_provider",
        ),
        sa.CheckConstraint("lifecycle IN ('active', 'archived')", name="ck_dependency_lifecycle"),
        # A provider identity only means something for something a provider
        # operates. An in-cluster store naming a provider would be a claim
        # about infrastructure Drake itself runs.
        sa.CheckConstraint(
            "provider IS NULL OR dependency_class <> 'in_cluster'",
            name="ck_dependency_provider_not_in_cluster",
        ),
    )
    op.create_index("ix_project_dependencies_project", "project_dependencies", ["project_id"])

    # A plan item's entity_kind is constrained to a fixed list (0018, widened
    # by 0019). Without widening it here the planner emits `dependency` and
    # the INSERT fails inside the apply transaction — the whole import rolls
    # back on a constraint, not on anything a reviewer could see in the plan.
    op.drop_constraint(_ENTITY_CONSTRAINT, _PLAN_ITEMS, type_="check")
    op.create_check_constraint(_ENTITY_CONSTRAINT, _PLAN_ITEMS, f"entity_kind IN ({_KINDS_0022})")


def downgrade() -> None:
    # Safe to drop: every row is re-derivable by re-applying the manifest
    # that produced it, and nothing else references this table. That is why
    # this downgrade may drop where 0021's had to refuse — there, the data
    # could not be reconstructed from anything.
    # Narrow the plan-item vocabulary back first. Rows recording a
    # `dependency` decision would violate it, so this refuses rather than
    # deleting somebody's plan history to make a constraint fit.
    connection = op.get_bind()
    recorded = connection.execute(
        # Fixed table name from a module constant, not user input.
        sa.text(f"SELECT count(*) FROM {_PLAN_ITEMS} WHERE entity_kind = 'dependency'")  # noqa: S608
    ).scalar_one()
    if recorded:
        raise RuntimeError(
            f"{recorded} plan item(s) record a dependency decision. Downgrading would "
            "have to delete them to satisfy the narrower constraint."
        )
    op.drop_constraint(_ENTITY_CONSTRAINT, _PLAN_ITEMS, type_="check")
    op.create_check_constraint(_ENTITY_CONSTRAINT, _PLAN_ITEMS, f"entity_kind IN ({_KINDS_0019})")

    op.drop_index("ix_project_dependencies_project", table_name="project_dependencies")
    op.drop_table("project_dependencies")
