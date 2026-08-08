"""Deployment revisions, rollout state and the evidence behind them.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-08

Drake could already say a service was unhealthy. It could not say *what
changed* — and "what changed" is the first question anyone asks during an
incident.

A revision is one observed generation of one workload: the images it runs,
what Drake can prove about where they came from, how the rollout went, and
how health looked either side of it. Two properties shape the schema:

**A snapshot is a snapshot.** Images and digests are recorded as observed.
A mutable tag repointed tomorrow does not rewrite what ran yesterday, which
is the whole reason a deployment history is worth keeping.

**Evidence is graded, never assumed.** `evidence_state` says how much of
the commit → workflow → digest → workload chain Drake actually saw. A
deployment is `verified` only when the chain holds end to end; a mutable
tag with no digest is `unverified`, and contradictory evidence is
`conflict` rather than a guess at which side is right.

Identity is `(cluster, workload_uid, revision)`. The UID survives a rename
and an agent reconnect, and the revision is the workload's own generation —
so re-reading the same workload produces the same row rather than a new
"deployment" every time the agent reconnects.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLLOUT_STATES = (
    "'pending', 'progressing', 'healthy', 'degraded', 'failed', 'stalled', 'unknown'"
)
_EVIDENCE_STATES = "'verified', 'partial', 'unverified', 'conflict'"
_COMPARISON_VERDICTS = "'improved', 'stable', 'regressed', 'insufficient_data'"


def upgrade() -> None:
    op.create_table(
        "deployment_revisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # --- where it runs ------------------------------------------------
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("workload_kind", sa.Text(), nullable=False),
        sa.Column("workload_name", sa.Text(), nullable=False),
        # The Kubernetes UID: stable across renames and agent reconnects,
        # and the reason a dropped connection does not invent a deployment.
        sa.Column("workload_uid", sa.Text(), nullable=False),
        # --- what it belongs to -------------------------------------------
        # All nullable: a workload Drake can see is worth recording even
        # before anyone binds it to a catalog service. Guessing the owner
        # from naming conventions is exactly what ADR-0022 refused to do.
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_workload_bindings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "environment_service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environment_services.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # --- the revision itself -------------------------------------------
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("observed_generation", sa.BigInteger(), nullable=True),
        # Bounded list of {name, image, digest}: references, never payloads.
        sa.Column(
            "images", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        # The image the workload is identified by — usually its first
        # container. Kept alongside `images` so a list can be read at a
        # glance without unpacking JSON.
        sa.Column("primary_image", sa.Text(), nullable=True),
        # Immutable, and the anchor of the whole evidence chain: a digest
        # cannot be repointed the way a tag can.
        sa.Column("primary_digest", sa.Text(), nullable=True),
        # --- provenance ----------------------------------------------------
        # Typed fields, never a URL. A link is composed by the server from a
        # configured base URL, so nothing here can carry an arbitrary one.
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column("workflow_provider", sa.Text(), nullable=True),
        sa.Column("workflow_repository", sa.Text(), nullable=True),
        sa.Column("workflow_run_id", sa.Text(), nullable=True),
        sa.Column(
            "evidence_state", sa.Text(), nullable=False, server_default=sa.text("'unverified'")
        ),
        # Which specific links of the chain were observed, for the UI to
        # show without re-deriving the verdict.
        sa.Column(
            "evidence_detail",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # --- rollout --------------------------------------------------------
        sa.Column(
            "rollout_state", sa.Text(), nullable=False, server_default=sa.text("'unknown'")
        ),
        sa.Column("rollout_reason", sa.Text(), nullable=True),
        sa.Column("desired_replicas", sa.Integer(), nullable=True),
        sa.Column("ready_replicas", sa.Integer(), nullable=True),
        sa.Column("updated_replicas", sa.Integer(), nullable=True),
        sa.Column("available_replicas", sa.Integer(), nullable=True),
        sa.Column("rollout_started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rollout_completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        # --- lineage ---------------------------------------------------------
        sa.Column(
            "previous_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployment_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "metadata_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
        # Canonical identity. Re-reading the same workload updates this row
        # instead of creating a second "deployment" for the same rollout.
        sa.UniqueConstraint(
            "cluster_id", "workload_uid", "revision", name="uq_deployment_revision_identity"
        ),
        sa.CheckConstraint(f"rollout_state IN ({_ROLLOUT_STATES})", name="ck_dr_rollout_state"),
        sa.CheckConstraint(f"evidence_state IN ({_EVIDENCE_STATES})", name="ck_dr_evidence_state"),
        sa.CheckConstraint(
            "workload_kind IN ('Deployment', 'StatefulSet', 'DaemonSet')",
            name="ck_dr_workload_kind",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_dr_revision"),
        sa.CheckConstraint("jsonb_typeof(images) = 'array'", name="ck_dr_images_array"),
        sa.CheckConstraint("pg_column_size(images) <= 8192", name="ck_dr_images_size"),
        sa.CheckConstraint(
            "pg_column_size(metadata_snapshot) <= 4096", name="ck_dr_metadata_size"
        ),
        # A digest is a digest. Anything else in this column would defeat
        # the point of having it.
        sa.CheckConstraint(
            "primary_digest IS NULL OR primary_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_dr_digest_shape",
        ),
        sa.CheckConstraint(
            "commit_sha IS NULL OR commit_sha ~ '^[0-9a-f]{7,64}$'", name="ck_dr_commit_shape"
        ),
        sa.CheckConstraint(
            "workflow_provider IS NULL OR workflow_provider IN ('github')",
            name="ck_dr_workflow_provider",
        ),
        sa.CheckConstraint("length(workflow_repository) <= 200", name="ck_dr_repository_length"),
        sa.CheckConstraint("length(workflow_run_id) <= 64", name="ck_dr_run_id_length"),
        sa.CheckConstraint("length(primary_image) <= 512", name="ck_dr_image_length"),
    )
    op.create_index(
        "ix_deployment_revisions_workload",
        "deployment_revisions",
        ["cluster_id", "workload_uid", sa.text("revision DESC")],
    )
    op.create_index(
        "ix_deployment_revisions_recent",
        "deployment_revisions",
        [sa.text("rollout_started_at DESC")],
    )
    op.create_index(
        "ix_deployment_revisions_service", "deployment_revisions", ["environment_service_id"]
    )
    op.create_index("ix_deployment_revisions_project", "deployment_revisions", ["project_id"])

    # --- health either side of a rollout ------------------------------------
    op.create_table(
        "deployment_health_comparisons",
        sa.Column(
            "deployment_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployment_revisions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Temporal correlation, and labelled as such. Drake does not claim
        # a deployment CAUSED anything — it says what health looked like
        # before and after, and lets a human draw the conclusion.
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("before_from", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("before_to", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("after_from", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("after_to", sa.TIMESTAMP(timezone=True), nullable=False),
        # Per-signal before/after/delta, each nullable — a missing signal is
        # missing, never zero.
        sa.Column(
            "signals", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("incident_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "missing_signals",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(f"verdict IN ({_COMPARISON_VERDICTS})", name="ck_dhc_verdict"),
        sa.CheckConstraint("before_to > before_from", name="ck_dhc_before_window"),
        sa.CheckConstraint("after_to > after_from", name="ck_dhc_after_window"),
        sa.CheckConstraint("incident_count >= 0", name="ck_dhc_incident_count"),
        sa.CheckConstraint("pg_column_size(signals) <= 8192", name="ck_dhc_signals_size"),
    )


def downgrade() -> None:
    op.drop_table("deployment_health_comparisons")
    op.drop_table("deployment_revisions")
