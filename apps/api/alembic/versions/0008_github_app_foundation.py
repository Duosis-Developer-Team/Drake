"""GitHub App installations, repository onboarding, webhooks, and policy.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07

- github_installations: one row per GitHub App installation, keyed on
  GitHub's permanent numeric installation id. Granted permissions and
  subscribed events are stored as bounded, non-secret metadata; no
  private key, JWT, installation token, or webhook secret is ever a
  column value (ADR-0019).
- github_repositories: keyed on GitHub's PERMANENT repository id, never
  on owner/name — renames and transfers reconcile onto the same row
  (ADR-0020). Carries the onboarding state machine, the manual security
  gate, and access state. Removal is soft state, never a delete.
- github_webhook_deliveries: delivery-id uniqueness IS the replay
  defence. Stores a bounded envelope plus the payload digest, never the
  raw payload.
- github_policy_evaluations: append-only read-only policy snapshots with
  bounded rule results and deterministic evidence digests.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ERROR_CODE_SHAPE = "~ '^[a-z0-9][a-z0-9_.-]{0,63}$'"


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "github_installations",
        _uuid_pk(),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'github'")),
        # GitHub's permanent numeric installation id — the identity.
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "scope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scopes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("account_login", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("account_external_id", sa.BigInteger(), nullable=True),
        sa.Column("account_type", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("app_slug", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "repository_selection", sa.Text(), nullable=False, server_default=sa.text("'selected'")
        ),
        # Granted permissions/events as observed from GitHub. Non-secret
        # metadata only, and bounded so a hostile payload cannot bloat rows.
        sa.Column(
            "granted_permissions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "subscribed_events",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("suspended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("provider", "external_id", name="uq_github_installation_identity"),
        sa.CheckConstraint(
            "state IN ('active', 'suspended', 'deleted')", name="ck_github_installation_state"
        ),
        sa.CheckConstraint(
            "repository_selection IN ('all', 'selected')",
            name="ck_github_installation_repo_selection",
        ),
        sa.CheckConstraint(
            f"last_error_code IS NULL OR last_error_code {_ERROR_CODE_SHAPE}",
            name="ck_github_installation_error_code",
        ),
        sa.CheckConstraint(
            "pg_column_size(granted_permissions) <= 8192",
            name="ck_github_installation_permissions_bound",
        ),
        sa.CheckConstraint(
            "pg_column_size(subscribed_events) <= 4096",
            name="ck_github_installation_events_bound",
        ),
    )
    op.create_index("ix_github_installations_scope", "github_installations", ["scope_id"])

    op.create_table(
        "github_repositories",
        _uuid_pk(),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'github'")),
        # GitHub's permanent numeric repository id — the identity. Name,
        # owner and full_name are observed attributes that reconcile.
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_installations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "scope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scopes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("owner_login", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("full_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("private", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("visibility", sa.Text(), nullable=False, server_default=sa.text("'private'")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("default_branch", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "onboarding_state", sa.Text(), nullable=False, server_default=sa.text("'discovered'")
        ),
        sa.Column("state_reason", sa.Text(), nullable=True),
        # An operator-controlled manual gate. While set, the repository is
        # BLOCKED and no credential read or API call may target it.
        sa.Column("security_gate", sa.Text(), nullable=True),
        sa.Column(
            "access_state", sa.Text(), nullable=False, server_default=sa.text("'accessible'")
        ),
        sa.Column("last_reconciled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_policy_evaluated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("provider", "external_id", name="uq_github_repository_identity"),
        sa.CheckConstraint(
            "onboarding_state IN ('discovered', 'validating', 'ready', 'blocked', "
            "'degraded', 'disabled')",
            name="ck_github_repository_onboarding_state",
        ),
        sa.CheckConstraint(
            "access_state IN ('accessible', 'suspended', 'removed')",
            name="ck_github_repository_access_state",
        ),
        sa.CheckConstraint(
            f"state_reason IS NULL OR state_reason {_ERROR_CODE_SHAPE}",
            name="ck_github_repository_state_reason",
        ),
        sa.CheckConstraint(
            f"security_gate IS NULL OR security_gate {_ERROR_CODE_SHAPE}",
            name="ck_github_repository_security_gate",
        ),
        sa.CheckConstraint(
            f"last_error_code IS NULL OR last_error_code {_ERROR_CODE_SHAPE}",
            name="ck_github_repository_error_code",
        ),
    )
    op.create_index("ix_github_repositories_scope", "github_repositories", ["scope_id"])
    op.create_index(
        "ix_github_repositories_installation", "github_repositories", ["installation_id"]
    )
    op.create_index("ix_github_repositories_state", "github_repositories", ["onboarding_state"])

    op.create_table(
        "github_webhook_deliveries",
        _uuid_pk(),
        # GitHub's X-GitHub-Delivery. The UNIQUE constraint IS the replay
        # defence: concurrent duplicates race here and exactly one wins.
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        # sha256 of the RAW body. Same delivery + same digest = idempotent
        # replay; same delivery + different digest = refused as tampering.
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("installation_external_id", sa.BigInteger(), nullable=True),
        sa.Column("repository_external_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'accepted'")),
        # A bounded envelope of explicitly chosen fields — NEVER the raw
        # payload, and never headers.
        sa.Column(
            "envelope", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("delivery_id", name="uq_github_delivery_id"),
        sa.CheckConstraint(
            "status IN ('accepted', 'processed', 'duplicate', 'rejected')",
            name="ck_github_delivery_status",
        ),
        sa.CheckConstraint("length(delivery_id) <= 128", name="ck_github_delivery_id_len"),
        sa.CheckConstraint("length(payload_digest) = 64", name="ck_github_delivery_digest_len"),
        sa.CheckConstraint(
            "pg_column_size(envelope) <= 8192", name="ck_github_delivery_envelope_bound"
        ),
    )
    op.create_index("ix_github_deliveries_received", "github_webhook_deliveries", ["received_at"])

    op.create_table(
        "github_policy_evaluations",
        _uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_repositories.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("profile", sa.Text(), nullable=False, server_default=sa.text("'default'")),
        sa.Column("overall", sa.Text(), nullable=False),
        sa.Column("blocking_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unknown_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Bounded, deterministic rule results — secret-free by construction.
        sa.Column(
            "results", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("evidence_digest", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "evaluated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "overall IN ('pass', 'warn', 'fail', 'unknown')", name="ck_github_policy_overall"
        ),
        sa.CheckConstraint(
            "blocking_count >= 0 AND unknown_count >= 0", name="ck_github_policy_counts"
        ),
        sa.CheckConstraint(
            "pg_column_size(results) <= 65536", name="ck_github_policy_results_bound"
        ),
    )
    op.create_index(
        "ix_github_policy_repository_time",
        "github_policy_evaluations",
        ["repository_id", "evaluated_at"],
    )


def downgrade() -> None:
    op.drop_table("github_policy_evaluations")
    op.drop_table("github_webhook_deliveries")
    op.drop_table("github_repositories")
    op.drop_table("github_installations")
