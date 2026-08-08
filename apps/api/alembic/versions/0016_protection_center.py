"""Protection: backups, artifacts, integrity, offsite copies, restore drills.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-08

A green backup job is not a backup. The chain that actually matters is

    policy → run → artifact → integrity → offsite copy → restore drill

and every link can be missing while the one before it looks fine. So each
link is its own table with its own evidence and its own timestamps, and the
derived state is computed from what is present rather than assumed from a
job exit code.

Two things shape the schema throughout:

**Provider time and Drake time are different columns.** `source_event_at`
is when the provider says something happened; `ingested_at` is when Drake
heard about it. Collapsing them would make a late delivery look like a late
backup, and would let a replayed old event drag a current projection
backwards.

**Identity is the provider's, not ours.** Every table carries the external
key the connector uses, unique per connector, so the same event delivered
twice — by retry, by reconciliation, or by both at once — updates one row
instead of inventing a second backup that never happened.

Nothing here stores backup content, a dump, a credential, a signed URL, a
token, a bucket path, a filename, or a raw provider response. The columns
for those do not exist, which is a stronger guarantee than a policy about
not filling them in.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY_SHAPE = "~ '^[a-z0-9][a-z0-9_.:-]{0,127}$'"

_BACKUP_STATES = "'protected', 'at_risk', 'overdue', 'failed', 'unknown'"
_RECOVERABILITY_STATES = "'verified', 'unverified', 'failed', 'unknown'"
_OVERALL_STATES = (
    "'recoverable_verified', 'protected_unverified', 'at_risk', 'overdue', 'failed', 'unknown'"
)
_RUN_STATUSES = "'started', 'succeeded', 'failed', 'cancelled', 'unknown'"
_DRILL_RESULTS = "'passed', 'failed', 'partial', 'unknown'"


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


def upgrade() -> None:
    # --- policy ----------------------------------------------------------
    op.create_table(
        "backup_policies",
        _uuid_pk(),
        # The connector that reports this policy. Server-resolved from the
        # settings registry — a payload cannot name its own connector.
        sa.Column("connector_key", sa.Text(), nullable=False),
        sa.Column("policy_external_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # The canonical store this policy protects. Hermes' core and auth
        # databases are two stores, so evidence for one can never stand in
        # for the other.
        sa.Column("store_key", sa.Text(), nullable=False),
        sa.Column("store_kind", sa.Text(), nullable=False, server_default=sa.text("'postgresql'")),
        sa.Column("provider_key", sa.Text(), nullable=False),
        sa.Column("schedule_expression", sa.Text(), nullable=True),
        sa.Column("schedule_description", sa.Text(), nullable=True),
        # The promises. Everything downstream is judged against these.
        sa.Column("rpo_seconds", sa.Integer(), nullable=False),
        sa.Column("rto_seconds", sa.Integer(), nullable=True),
        sa.Column("retention_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "requires_offsite", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "requires_integrity_check",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # How long a successful restore drill keeps counting as evidence.
        # A drill from a year ago proves something about a year ago.
        sa.Column("restore_verification_ttl_seconds", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "effective_from",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "connector_key", "policy_external_key", name="uq_backup_policy_identity"
        ),
        sa.CheckConstraint(f"connector_key {_KEY_SHAPE}", name="ck_bp_connector_shape"),
        sa.CheckConstraint(f"store_key {_KEY_SHAPE}", name="ck_bp_store_shape"),
        sa.CheckConstraint(f"provider_key {_KEY_SHAPE}", name="ck_bp_provider_shape"),
        sa.CheckConstraint("rpo_seconds > 0", name="ck_bp_rpo"),
        sa.CheckConstraint("rto_seconds IS NULL OR rto_seconds > 0", name="ck_bp_rto"),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 200", name="ck_bp_name_length"),
        sa.CheckConstraint("version >= 1", name="ck_bp_version"),
        sa.CheckConstraint(
            "store_kind IN ('postgresql', 'object_storage', 'other')", name="ck_bp_store_kind"
        ),
    )
    op.create_index("ix_backup_policies_project", "backup_policies", ["project_id"])

    # --- run --------------------------------------------------------------
    op.create_table(
        "backup_runs",
        _uuid_pk(),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connector_key", sa.Text(), nullable=False),
        sa.Column("provider_run_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        # A bounded classification, never a provider message: an error
        # string is where a connection URI ends up.
        sa.Column("error_code", sa.Text(), nullable=True),
        # An explicit correction supersedes an earlier run rather than
        # silently overwriting it — the earlier evidence stays readable.
        sa.Column(
            "superseded_by_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        sa.UniqueConstraint("connector_key", "provider_run_id", name="uq_backup_run_identity"),
        sa.CheckConstraint(f"status IN ({_RUN_STATUSES})", name="ck_br_status"),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'", name="ck_br_error_code"
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_br_attempt"),
        sa.CheckConstraint("length(provider_run_id) <= 200", name="ck_br_run_id_length"),
    )
    op.create_index(
        "ix_backup_runs_policy_time", "backup_runs", ["policy_id", sa.text("started_at DESC")]
    )

    # --- artifact ----------------------------------------------------------
    op.create_table(
        "backup_artifacts",
        _uuid_pk(),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # An OPAQUE provider handle. Not a path, not a filename, not a URL —
        # there is no column here that could hold one.
        sa.Column("artifact_external_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_algorithm", sa.Text(), nullable=True),
        sa.Column("checksum", sa.Text(), nullable=True),
        sa.Column("encrypted", sa.Boolean(), nullable=True),
        sa.Column("storage_provider_key", sa.Text(), nullable=True),
        sa.Column("storage_site_key", sa.Text(), nullable=True),
        sa.Column("created_at_source", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # An artifact the provider stops reporting becomes `missing`, never
        # deleted: a reporter outage is not proof that a backup is gone,
        # and retention expiring is not proof that a file was removed.
        sa.Column("presence", sa.Text(), nullable=False, server_default=sa.text("'present'")),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "artifact_external_key", name="uq_backup_artifact_identity"),
        sa.CheckConstraint("presence IN ('present', 'missing', 'expired')", name="ck_ba_presence"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_ba_size"),
        sa.CheckConstraint(
            "checksum_algorithm IS NULL OR checksum_algorithm IN ('sha256', 'sha512', 'md5')",
            name="ck_ba_checksum_algorithm",
        ),
        sa.CheckConstraint(
            "checksum IS NULL OR checksum ~ '^[0-9a-f]{32,128}$'", name="ck_ba_checksum_shape"
        ),
        sa.CheckConstraint(
            "length(artifact_external_key) <= 200", name="ck_ba_external_key_length"
        ),
    )
    op.create_index(
        "ix_backup_artifacts_policy_time",
        "backup_artifacts",
        ["policy_id", sa.text("source_event_at DESC")],
    )

    # --- offsite copy -------------------------------------------------------
    op.create_table(
        "replication_copies",
        _uuid_pk(),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The destination as a KEY into the connector's site registry.
        # Never a bucket URL, never a container path.
        sa.Column("site_key", sa.Text(), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("is_offsite", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        sa.UniqueConstraint("artifact_id", "site_key", name="uq_replication_copy_identity"),
        sa.CheckConstraint(
            "state IN ('present', 'pending', 'missing', 'failed')", name="ck_rc_state"
        ),
        sa.CheckConstraint(f"site_key {_KEY_SHAPE}", name="ck_rc_site_shape"),
    )

    # --- integrity check ----------------------------------------------------
    op.create_table(
        "integrity_checks",
        _uuid_pk(),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_external_key", sa.Text(), nullable=False),
        # How it was checked, from a fixed vocabulary: "we verified it" is
        # not a claim, it is a method with a result.
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "artifact_id", "check_external_key", name="uq_integrity_check_identity"
        ),
        sa.CheckConstraint(
            "method IN ('checksum', 'restore_probe', 'archive_verify', 'other')",
            name="ck_ic_method",
        ),
        sa.CheckConstraint("result IN ('passed', 'failed', 'skipped')", name="ck_ic_result"),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'", name="ck_ic_error_code"
        ),
    )

    # --- restore drill ------------------------------------------------------
    op.create_table(
        "restore_drills",
        _uuid_pk(),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("connector_key", sa.Text(), nullable=False),
        sa.Column("drill_external_id", sa.Text(), nullable=False),
        # Where it was restored to, as a profile name: `ephemeral`,
        # `staging`. Never a connection string.
        sa.Column("target_profile", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("rto_met", sa.Boolean(), nullable=True),
        # Typed pass/fail checks only — schema present, row counts sane,
        # migrations applied, smoke passed. No row samples, no SQL, no
        # command output, no business data.
        sa.Column(
            "validations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        sa.UniqueConstraint("connector_key", "drill_external_id", name="uq_restore_drill_identity"),
        sa.CheckConstraint(f"result IN ({_DRILL_RESULTS})", name="ck_rd_result"),
        sa.CheckConstraint(f"target_profile {_KEY_SHAPE}", name="ck_rd_target_shape"),
        sa.CheckConstraint("jsonb_typeof(validations) = 'object'", name="ck_rd_validations_object"),
        sa.CheckConstraint("pg_column_size(validations) <= 2048", name="ck_rd_validations_size"),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'", name="ck_rd_error_code"
        ),
    )
    op.create_index(
        "ix_restore_drills_policy_time",
        "restore_drills",
        ["policy_id", sa.text("completed_at DESC")],
    )

    # --- derived evaluation --------------------------------------------------
    op.create_table(
        "protection_evaluations",
        _uuid_pk(),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backup_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The policy revision this verdict was reached under. A historical
        # evaluation is never rewritten by today's policy — it recorded
        # what the promise was at the time.
        sa.Column("policy_version", sa.Integer(), nullable=False),
        # The period this verdict covers, so re-evaluating the same moment
        # updates one row rather than accumulating duplicates.
        sa.Column("evaluated_for", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("backup_state", sa.Text(), nullable=False),
        sa.Column("recoverability_state", sa.Text(), nullable=False),
        sa.Column("overall_state", sa.Text(), nullable=False),
        sa.Column(
            "reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_restore_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reporter_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "policy_id", "evaluated_for", "policy_version", name="uq_protection_evaluation_period"
        ),
        sa.CheckConstraint(f"backup_state IN ({_BACKUP_STATES})", name="ck_pe_backup_state"),
        sa.CheckConstraint(
            f"recoverability_state IN ({_RECOVERABILITY_STATES})", name="ck_pe_recoverability"
        ),
        sa.CheckConstraint(f"overall_state IN ({_OVERALL_STATES})", name="ck_pe_overall"),
        sa.CheckConstraint("jsonb_typeof(reasons) = 'array'", name="ck_pe_reasons_array"),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_pe_failures"),
    )
    op.create_index(
        "ix_protection_evaluations_policy",
        "protection_evaluations",
        ["policy_id", sa.text("evaluated_for DESC")],
    )

    # --- ingest idempotency ---------------------------------------------------
    op.create_table(
        "protection_ingest_events",
        # The connector's own event id. This IS the idempotency key: the
        # same delivery, retried or replayed, lands on the primary key.
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("connector_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('applied', 'ignored_stale', 'rejected', 'duplicate')",
            name="ck_pie_outcome",
        ),
        sa.CheckConstraint("length(event_id) <= 200", name="ck_pie_event_id_length"),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'", name="ck_pie_error_code"
        ),
    )
    op.create_index(
        "ix_protection_ingest_recent",
        "protection_ingest_events",
        ["connector_key", sa.text("received_at DESC")],
    )

    # --- reconciliation snapshots ----------------------------------------------
    op.create_table(
        "protection_snapshots",
        _uuid_pk(),
        sa.Column("connector_key", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("records", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.CheckConstraint("state IN ('open', 'completed', 'discarded')", name="ck_ps_state"),
    )
    op.create_index(
        "ix_protection_snapshots_connector",
        "protection_snapshots",
        ["connector_key", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("protection_snapshots")
    op.drop_table("protection_ingest_events")
    op.drop_table("protection_evaluations")
    op.drop_table("restore_drills")
    op.drop_table("integrity_checks")
    op.drop_table("replication_copies")
    op.drop_table("backup_artifacts")
    op.drop_table("backup_runs")
    op.drop_table("backup_policies")
