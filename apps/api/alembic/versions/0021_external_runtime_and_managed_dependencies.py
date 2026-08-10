"""Runtimes Drake does not run, and dependencies it does not manage.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-11

Two columns, both driven by the same problem: the catalog could only
describe a service Drake could scrape, in a cluster Drake could see.

**`environments.hosting_provider`.** An external environment had a runtime
kind and nothing else, so "hosted somewhere Drake does not run" and "hosted
on a specific platform" were the same row. Nullable, and constrained to a
fixed vocabulary rather than free text — a provider column that accepts any
string becomes an unbounded label, and unbounded labels are how cardinality
problems and free-text leaks arrive together.

**`service_definitions.metrics_profile` becomes nullable.** It was NOT NULL,
so a service with no metrics source had to name a profile anyway, and every
such row asserted a scrape target that does not exist. A required column
with no honest value manufactures false claims. NULL now means exactly
`not_configured`, which the health layer already knows how to say.

Both changes are additive and backward compatible. Every existing row keeps
its value: `hosting_provider` defaults to NULL, which reads as "not
recorded" rather than as any provider, and no existing `metrics_profile`
is touched. Nothing infers a provider for a Kubernetes environment, because
an inferred provider is a fact nobody stated.

The downgrade is deliberately lossy in one direction and says so: rows that
legitimately have no metrics profile cannot be represented by a NOT NULL
column, so downgrading refuses rather than inventing a placeholder for them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Kept in one place and mirrored by the manifest schema's `hostingProvider`
# enum. A closed vocabulary: `unknown` is a real answer, free text is not.
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


def upgrade() -> None:
    op.add_column(
        "environments",
        sa.Column("hosting_provider", sa.Text(), nullable=True),
    )
    values = ", ".join(f"'{name}'" for name in _PROVIDERS)
    op.create_check_constraint(
        "ck_env_hosting_provider",
        "environments",
        f"hosting_provider IS NULL OR hosting_provider IN ({values})",
    )
    # A Kubernetes environment is run by whoever runs the cluster; recording
    # a hosting provider for it would be a claim nobody made.
    op.create_check_constraint(
        "ck_env_provider_only_external",
        "environments",
        "hosting_provider IS NULL OR runtime = 'external'",
    )

    op.alter_column(
        "service_definitions",
        "metrics_profile",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # Refuse rather than fabricate. A service that genuinely has no metrics
    # source cannot be expressed by a NOT NULL column, and filling those rows
    # with a placeholder would recreate the exact false claim this migration
    # removed — silently, and in the direction nobody is watching.
    connection = op.get_bind()
    unprofiled = connection.execute(
        sa.text("SELECT count(*) FROM service_definitions WHERE metrics_profile IS NULL")
    ).scalar_one()
    if unprofiled:
        raise RuntimeError(
            f"{unprofiled} service definition(s) have no metrics profile. Downgrading "
            "would require inventing one for each. Decide what those services should "
            "declare, set it explicitly, then downgrade."
        )

    op.alter_column(
        "service_definitions",
        "metrics_profile",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_constraint("ck_env_provider_only_external", "environments", type_="check")
    op.drop_constraint("ck_env_hosting_provider", "environments", type_="check")
    op.drop_column("environments", "hosting_provider")
