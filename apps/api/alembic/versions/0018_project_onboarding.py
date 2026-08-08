"""Reviewable project onboarding: sessions, analyses, plans, applies.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-09

Sprint 5B could scan a repository and import it. What it could not do was
show anyone what the import would DO before it did it — a valid manifest
went straight to catalog rows. That is fine for a repository nobody has
onboarded and wrong for everything else: an import that silently overwrites
an existing service, or quietly picks one of two matching environments, is
a change nobody reviewed.

So this migration adds the missing middle of the chain:

    repository → analysis → PLAN → approval → apply

Six ideas carry the schema:

- **The source-of-truth boundary is explicit.** The Drake catalog is
  authoritative; `.drake/project.yaml` is versioned repository INTENT;
  discovery is evidence. A plan is the proposal that reconciles the three,
  and nothing in it takes effect until an authorized human approves that
  exact version.
- **An analysis is identified by what it analysed.** `(repository, commit,
  analyzer_version)` is unique, so re-running the same analysis returns the
  same row instead of manufacturing a second opinion about one commit.
- **A plan freezes its inputs.** It records the commit, the manifest digest
  and the analyzer version it was built from. If any of them moves, the
  plan is stale and cannot be applied — approving a plan and applying a
  different one is the failure this prevents.
- **Apply is idempotent and never destructive.** One apply per
  `(plan, idempotency_key)`. Plan items are typed, and there is no `delete`
  kind: a service that disappeared from a repository is reported, never
  removed from the catalog behind someone's back.
- **Ambiguity is a state, not a default.** `conflict` and `unmapped` items
  block apply rather than resolving themselves toward whichever row sorted
  first.
- **GitOps is a request with a provider outcome.** A pull request Drake has
  not created yet, or failed to create, is never shown as open.

Nothing here stores a repository file body, a manifest body, a provider
response, a token, a webhook signature, or a URL. Findings carry a path, a
kind and a confidence — enough to explain a proposal, and not enough to
become a copy of the repository.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY_SHAPE = "~ '^[a-z0-9][a-z0-9_.:/-]{0,127}$'"

# The onboarding state machine. Every transition is server-decided; there is
# no endpoint that accepts a state.
_SESSION_STATES = (
    "'draft', 'discovery_pending', 'analyzing', 'needs_review', 'ready', "
    "'approved', 'applying', 'imported', 'failed', 'cancelled', "
    "'not_configured', 'stale', 'provider_unavailable'"
)

# What a plan proposes to do. Deliberately no `delete`: a catalog row is
# never removed because a repository stopped mentioning it.
_ITEM_ACTIONS = (
    "'create', 'link', 'update_metadata', 'no_change', 'conflict', 'unmapped', 'unsupported'"
)

_ENTITY_KINDS = (
    "'project', 'environment', 'service', 'owner_team', 'repository', "
    "'cluster_binding', 'namespace_binding', 'metric_profile', 'slo_profile', "
    "'deployment_source'"
)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _sessions() -> None:
    op.create_table(
        "onboarding_sessions",
        _uuid_pk(),
        # The repository projection this session is about. Identity comes
        # from the projection's numeric provider id, never from a name a
        # payload supplied.
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Visibility follows the repository's scope, resolved server-side.
        sa.Column(
            "scope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scopes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # The commit this session's newest analysis ran against. Null until
        # discovery has happened; a session that has looked at nothing must
        # not claim a commit.
        sa.Column("analyzed_commit_sha", sa.Text(), nullable=True),
        sa.Column("analyzed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "approved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("approved_plan_version", sa.Integer(), nullable=True),
        sa.Column(
            "imported_project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("imported_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.CheckConstraint(f"state IN ({_SESSION_STATES})", name="ck_onboarding_session_state"),
        sa.CheckConstraint(
            f"reason_code IS NULL OR reason_code {_KEY_SHAPE}",
            name="ck_onboarding_session_reason",
        ),
        sa.CheckConstraint(
            "analyzed_commit_sha IS NULL OR analyzed_commit_sha ~ '^[0-9a-f]{7,64}$'",
            name="ck_onboarding_session_commit",
        ),
        # An approval names a plan version. Without both, "approved" would
        # not say what was approved.
        sa.CheckConstraint(
            "(approved_at IS NULL) = (approved_plan_version IS NULL)",
            name="ck_onboarding_session_approval",
        ),
        sa.CheckConstraint("version >= 1", name="ck_onboarding_session_version"),
    )
    # At most one live session per repository. Two people onboarding the
    # same repository at once would approve two different plans and race to
    # apply them; the loser's review would be silently discarded.
    op.create_index(
        "uq_onboarding_session_active",
        "onboarding_sessions",
        ["repository_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('imported', 'cancelled', 'failed')"),
    )
    op.create_index("ix_onboarding_sessions_scope", "onboarding_sessions", ["scope_id", "state"])


def _analyses() -> None:
    op.create_table(
        "onboarding_analyses",
        _uuid_pk(),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        # Bumped whenever discovery's rules change. Part of the identity so
        # a smarter analyzer legitimately produces a NEW analysis of the
        # same commit rather than colliding with the old one.
        sa.Column("analyzer_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'complete'")),
        # An analysis that hit a budget describes part of a repository. It
        # is reported as partial and never counted as a full picture.
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("manifest_found", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # A digest, not the manifest. Enough to detect drift; not a copy.
        sa.Column("manifest_digest", sa.Text(), nullable=True),
        sa.Column("files_read", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("bytes_read", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("provider_calls", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_code", sa.Text(), nullable=True),
        # Provider time, Drake time. A slow analysis is not an old commit.
        sa.Column("source_committed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "analyzed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        # Same repository, same commit, same analyzer → one analysis. A
        # retried request finds the existing row instead of manufacturing a
        # second opinion about one commit.
        sa.UniqueConstraint(
            "repository_id",
            "commit_sha",
            "analyzer_version",
            name="uq_onboarding_analysis_identity",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'failed')", name="ck_onboarding_analysis_status"
        ),
        sa.CheckConstraint("commit_sha ~ '^[0-9a-f]{7,64}$'", name="ck_onboarding_analysis_commit"),
        sa.CheckConstraint("analyzer_version >= 1", name="ck_onboarding_analysis_version"),
        # `partial` and `truncated` are the same fact stated twice; letting
        # them disagree would make one of them meaningless.
        sa.CheckConstraint(
            "(status = 'partial') = truncated", name="ck_onboarding_analysis_partial"
        ),
    )
    op.create_index(
        "ix_onboarding_analyses_session",
        "onboarding_analyses",
        ["session_id", sa.text("analyzed_at DESC")],
    )

    op.create_table(
        "onboarding_findings",
        _uuid_pk(),
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("finding_type", sa.Text(), nullable=False),
        # The PATH inside the repository, never the content at it.
        sa.Column("safe_path", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("evidence_kind", sa.Text(), nullable=False),
        sa.Column("proposed_target", sa.Text(), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')", name="ck_onboarding_finding_confidence"
        ),
        sa.CheckConstraint(f"finding_type {_KEY_SHAPE}", name="ck_onboarding_finding_type"),
        sa.CheckConstraint(f"evidence_kind {_KEY_SHAPE}", name="ck_onboarding_finding_evidence"),
        # A path, bounded. Long enough for a real repository path and short
        # enough that nothing else fits.
        sa.CheckConstraint("length(safe_path) <= 512", name="ck_onboarding_finding_path"),
        sa.CheckConstraint(
            "proposed_target IS NULL OR length(proposed_target) <= 256",
            name="ck_onboarding_finding_target",
        ),
        sa.CheckConstraint(
            "review_reason IS NULL OR length(review_reason) <= 256",
            name="ck_onboarding_finding_reason",
        ),
    )
    op.create_index("ix_onboarding_findings_analysis", "onboarding_findings", ["analysis_id"])


def _plans() -> None:
    op.create_table(
        "onboarding_plans",
        _uuid_pk(),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_analyses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        # The inputs this plan was built from, frozen. If the branch moved
        # or the manifest changed, the plan describes a repository state
        # that no longer exists and must not be applied.
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("manifest_digest", sa.Text(), nullable=True),
        sa.Column("analyzer_version", sa.Integer(), nullable=False),
        # A digest over the canonical plan items. What was approved and what
        # is applied are compared on this, not on a row count.
        sa.Column("plan_digest", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'ready'")),
        sa.Column("blocking_items", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("session_id", "plan_version", name="uq_onboarding_plan_version"),
        sa.CheckConstraint(
            "state IN ('ready', 'needs_review', 'stale', 'applied', 'superseded')",
            name="ck_onboarding_plan_state",
        ),
        sa.CheckConstraint("plan_version >= 1", name="ck_onboarding_plan_version_positive"),
        sa.CheckConstraint(
            "length(plan_digest) BETWEEN 16 AND 128", name="ck_onboarding_plan_digest"
        ),
        # A plan with blocking items is not ready, whatever else is true.
        sa.CheckConstraint(
            "blocking_items = 0 OR state <> 'ready'", name="ck_onboarding_plan_blocking"
        ),
    )
    # One live plan per session. A second one would mean an approval could
    # name a version that is no longer the current proposal.
    op.create_index(
        "uq_onboarding_plan_active",
        "onboarding_plans",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('ready', 'needs_review')"),
    )

    op.create_table(
        "onboarding_plan_items",
        _uuid_pk(),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        # A stable, server-composed key for this proposal within the plan:
        # `service:api`, `environment:dev`. Unique per plan so a rendered
        # plan cannot contain the same proposal twice.
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column("proposed_name", sa.Text(), nullable=True),
        # The catalog row this links to or conflicts with, when there is one.
        sa.Column("existing_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("existing_name", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.Text(), nullable=True),
        # Bounded, server-composed detail: keys and codes only.
        sa.Column(
            "detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("plan_id", "item_key", name="uq_onboarding_plan_item_key"),
        sa.CheckConstraint(f"entity_kind IN ({_ENTITY_KINDS})", name="ck_onboarding_item_entity"),
        sa.CheckConstraint(f"action IN ({_ITEM_ACTIONS})", name="ck_onboarding_item_action"),
        sa.CheckConstraint(f"item_key {_KEY_SHAPE}", name="ck_onboarding_item_key_shape"),
        sa.CheckConstraint("jsonb_typeof(detail) = 'object'", name="ck_onboarding_item_detail"),
        sa.CheckConstraint("pg_column_size(detail) <= 2048", name="ck_onboarding_item_detail_size"),
    )
    op.create_index("ix_onboarding_plan_items_plan", "onboarding_plan_items", ["plan_id"])


def _applies() -> None:
    op.create_table(
        "onboarding_applies",
        _uuid_pk(),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_entities", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("linked_entities", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unchanged_entities", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Supplied by the caller. A client that lost the response repeats
        # the call and gets the same answer instead of a second project.
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "applied_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("plan_id", "idempotency_key", name="uq_onboarding_apply_identity"),
        sa.CheckConstraint(
            "outcome IN ('applied', 'unchanged', 'failed')", name="ck_onboarding_apply_outcome"
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 8 AND 128", name="ck_onboarding_apply_idem"
        ),
        # Success names a project; failure does not name one it did not make.
        sa.CheckConstraint(
            "(outcome = 'failed') = (project_id IS NULL)", name="ck_onboarding_apply_project"
        ),
    )
    op.create_index("ix_onboarding_applies_session", "onboarding_applies", ["session_id"])


def _gitops() -> None:
    op.create_table(
        "gitops_requests",
        _uuid_pk(),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Server-composed. There is no request field for a branch, a path or
        # a base repository: a caller who could choose them could write
        # anywhere the installation can reach.
        sa.Column("branch_name", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        # The commit the proposal was built against. If the base moved, the
        # request is stale rather than silently rebased onto something else.
        sa.Column("base_commit_sha", sa.Text(), nullable=False),
        # A digest of the proposed file. The content itself is regenerated
        # deterministically at send time and never stored.
        sa.Column("content_digest", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        # Assigned by the provider, and only once it accepted the PR.
        sa.Column("provider_pr_number", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'failed', 'stale', 'cancelled')",
            name="ck_gitops_state",
        ),
        # Allowlisted path, enforced in the database as well as in code.
        sa.CheckConstraint("file_path = '.drake/project.yaml'", name="ck_gitops_allowlisted_path"),
        sa.CheckConstraint(
            "branch_name ~ '^drake/[a-z0-9][a-z0-9/-]{0,80}$'", name="ck_gitops_branch_shape"
        ),
        sa.CheckConstraint("base_commit_sha ~ '^[0-9a-f]{7,64}$'", name="ck_gitops_base_commit"),
        # Open means the provider said so and gave us a number to prove it.
        sa.CheckConstraint(
            "state <> 'active' OR provider_pr_number IS NOT NULL",
            name="ck_gitops_active_requires_provider",
        ),
        sa.CheckConstraint("version >= 1", name="ck_gitops_version"),
    )
    op.create_index("ix_gitops_requests_session", "gitops_requests", ["session_id"])
    op.create_index(
        "ix_gitops_requests_due",
        "gitops_requests",
        ["next_attempt_at"],
        postgresql_where=sa.text("state = 'pending'"),
    )


def upgrade() -> None:
    _sessions()
    _analyses()
    _plans()
    _applies()
    _gitops()


def downgrade() -> None:
    op.drop_table("gitops_requests")
    op.drop_table("onboarding_applies")
    op.drop_table("onboarding_plan_items")
    op.drop_table("onboarding_plans")
    op.drop_table("onboarding_findings")
    op.drop_table("onboarding_analyses")
    op.drop_table("onboarding_sessions")
