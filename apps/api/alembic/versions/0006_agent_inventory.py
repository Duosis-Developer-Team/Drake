"""Cluster agent identity and Kubernetes inventory.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06

- Agent enrollment tokens: hashed only (never plaintext), one-time,
  short-lived, atomically consumable.
- Cluster agents: identity metadata, the enrolled PUBLIC key, certificate
  metadata (serial/expiry — never private material), heartbeat/freshness,
  monotonic sequence + inventory state for dedupe/reconcile.
- Snapshots: staged pages with per-snapshot uniqueness (idempotent
  duplicates), bounded counters, atomic-projection bookkeeping.
- Current inventory projection: normalized identity
  (cluster + api_group + kind + namespace + uid), bounded JSONB fields,
  lifecycle (missing, never hard-deleted), health + reasons.
- Bounded change events (append-only history; no cascade delete).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    op.create_table(
        "agent_enrollment_tokens",
        _uuid_pk(),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        _created_at(),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("used_by_agent", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_enrollment_tokens_cluster", "agent_enrollment_tokens", ["cluster_id"])

    op.create_table(
        "cluster_agents",
        _uuid_pk(),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("agent_version", sa.Text(), nullable=False, server_default=sa.text("''")),
        # The enrolled PUBLIC key (PEM). Private material never exists here.
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("certificate_serial", sa.Text(), nullable=False),
        sa.Column("certificate_not_after", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        _created_at(),
        sa.Column("last_heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "inventory_state",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'empty'"),
        ),
        sa.Column("last_reconcile_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("lifecycle IN ('active', 'revoked')", name="ck_agent_lifecycle"),
        sa.CheckConstraint(
            "inventory_state IN ('empty', 'reconciling', 'fresh', 'stale', 'reconcile_required')",
            name="ck_agent_inventory_state",
        ),
    )
    op.create_index("ix_cluster_agents_cluster", "cluster_agents", ["cluster_id"])

    op.create_table(
        "inventory_snapshots",
        _uuid_pk(),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cluster_agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("snapshot_uid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expected_pages", sa.Integer(), nullable=True),
        sa.Column("received_pages", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("resource_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("cluster_id", "snapshot_uid", name="uq_snapshot_cluster_uid"),
        sa.CheckConstraint(
            "status IN ('pending', 'complete', 'discarded')", name="ck_snapshot_status"
        ),
        sa.CheckConstraint("received_pages >= 0", name="ck_snapshot_received_pages"),
        sa.CheckConstraint(
            "resource_count >= 0 AND resource_count <= 500000",
            name="ck_snapshot_resource_count",
        ),
    )
    op.create_index(
        "ix_inventory_snapshots_cluster_status", "inventory_snapshots", ["cluster_id", "status"]
    )

    op.create_table(
        "inventory_snapshot_pages",
        _uuid_pk(),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("resource_count", sa.Integer(), nullable=False),
        _created_at(),
        sa.UniqueConstraint("snapshot_id", "page_number", name="uq_snapshot_page"),
        sa.CheckConstraint(
            "page_number >= 1 AND page_number <= 10000", name="ck_page_number_bounds"
        ),
        sa.CheckConstraint(
            "resource_count >= 0 AND resource_count <= 500", name="ck_page_resource_bounds"
        ),
    )

    op.create_table(
        "inventory_staging_resources",
        _uuid_pk(),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("api_group", sa.Text(), nullable=False),
        sa.Column("api_version", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("resource_version", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("snapshot_id", "uid", name="uq_staging_snapshot_uid"),
        sa.CheckConstraint("length(uid) <= 64", name="ck_staging_uid_len"),
        sa.CheckConstraint("pg_column_size(payload) <= 32768", name="ck_staging_payload_bound"),
    )

    op.create_table(
        "inventory_resources",
        _uuid_pk(),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("api_group", sa.Text(), nullable=False),
        sa.Column("api_version", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("resource_version", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("health", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column(
            "health_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("lifecycle", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("cluster_id", "uid", name="uq_inventory_cluster_uid"),
        sa.CheckConstraint(
            "health IN ('healthy', 'degraded', 'unhealthy', 'unknown')",
            name="ck_inventory_health",
        ),
        sa.CheckConstraint("lifecycle IN ('active', 'missing')", name="ck_inventory_lifecycle"),
        sa.CheckConstraint(
            "kind NOT IN ('Secret', 'ConfigMap')", name="ck_inventory_forbidden_kinds"
        ),
        sa.CheckConstraint("pg_column_size(payload) <= 32768", name="ck_inventory_payload_bound"),
    )
    op.create_index(
        "ix_inventory_cluster_kind_ns",
        "inventory_resources",
        ["cluster_id", "kind", "namespace"],
    )
    op.create_index("ix_inventory_cluster_health", "inventory_resources", ["cluster_id", "health"])
    op.create_index(
        "ix_inventory_cluster_lifecycle", "inventory_resources", ["cluster_id", "lifecycle"]
    )

    op.create_table(
        "inventory_change_events",
        _uuid_pk(),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "change_type IN ('added', 'updated', 'missing', 'restored')",
            name="ck_change_type",
        ),
    )
    op.create_index(
        "ix_inventory_changes_cluster_time",
        "inventory_change_events",
        ["cluster_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("inventory_change_events")
    op.drop_table("inventory_resources")
    op.drop_table("inventory_staging_resources")
    op.drop_table("inventory_snapshot_pages")
    op.drop_table("inventory_snapshots")
    op.drop_table("cluster_agents")
    op.drop_table("agent_enrollment_tokens")
