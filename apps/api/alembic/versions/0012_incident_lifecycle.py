"""Health transition history and the incident lifecycle it drives.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-08

Sprint 5 could answer "is this service healthy right now". Nothing
remembered the answer, so nothing could notice it changing — and an
observability plane that cannot tell "still broken" from "just broke" is
a dashboard, not a control plane.

Four tables, each with one job:

- `service_health_state` — one row per binding, the running state a
  decision is made against: the last verdict, the last evaluation actually
  processed, and the consecutive-critical / consecutive-healthy counters.
  It is the row a processor locks, so two workers cannot both decide.
- `service_health_transitions` — append-only, and written only when the
  status or the meaningful reason set actually changes. A row per poll
  would be a metric, not a history.
- `incidents` — at most one active incident per binding, enforced by a
  partial unique index rather than by application code alone. Two workers
  racing must lose the race in the database, where losing is possible.
- `incident_events` — append-only lifecycle timeline. Nothing here is ever
  updated or deleted, so "what happened and when" cannot be rewritten
  after the fact.

None of these tables stores a query, a credential, a provider label set,
or a metric payload. What is kept is what a human needs to understand the
decision: statuses, reason codes from the server's own dictionary, and
timestamps.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same bounded shape the other tables use for machine-readable keys.
_KEY_SHAPE = "~ '^[a-z0-9][a-z0-9_.-]{0,63}$'"

# The statuses the health engine can produce. Kept in the database too, so
# a bug in a new caller cannot persist a status the UI has no meaning for.
_HEALTH_STATUSES = "'healthy', 'degraded', 'critical', 'unknown', 'stale', 'not_configured'"


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


def upgrade() -> None:
    # --- current state -------------------------------------------------
    op.create_table(
        "service_health_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # One row per binding. The catalog ids are carried alongside so a
        # scope filter never has to join back through the binding.
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_workload_bindings.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "environment_service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environment_services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
            nullable=False,
        ),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # The binding revision this state was last observed under. A
        # binding edited to a different preset or policy is measuring
        # something else, so the streaks it accumulated no longer apply.
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("current_status", sa.Text(), nullable=False),
        # Reason CODES only, from the server's own dictionary. Never a
        # message, never a label, never anything a provider produced.
        sa.Column(
            "current_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # When this status was first seen, and when it was last seen. The
        # pair is what makes "critical for 40 minutes" answerable.
        sa.Column("status_since", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        # The last evaluation that was trustworthy enough to act on —
        # distinct from the last one merely received.
        sa.Column("last_reliable_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Identity of the last evaluation actually processed. Re-processing
        # a cached verdict must not advance a streak, so the processor
        # compares this before it does anything else.
        sa.Column("last_evaluation_id", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_critical", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("consecutive_healthy", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.CheckConstraint(
            f"current_status IN ({_HEALTH_STATUSES})", name="ck_health_state_status"
        ),
        sa.CheckConstraint("consecutive_critical >= 0", name="ck_health_state_critical_count"),
        sa.CheckConstraint("consecutive_healthy >= 0", name="ck_health_state_healthy_count"),
        sa.CheckConstraint("version >= 1", name="ck_health_state_version"),
        sa.CheckConstraint(
            "jsonb_typeof(current_reasons) = 'array'", name="ck_health_state_reasons_array"
        ),
        sa.CheckConstraint("length(last_evaluation_id) <= 128", name="ck_health_state_eval_id"),
    )
    op.create_index(
        "ix_service_health_state_service", "service_health_state", ["environment_service_id"]
    )
    op.create_index("ix_service_health_state_env", "service_health_state", ["environment_id"])

    # --- transition history --------------------------------------------
    op.create_table(
        "service_health_transitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_workload_bindings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "environment_service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environment_services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # NULL previous_status means "first observation", which is a real
        # and distinct fact from a transition out of `unknown`.
        sa.Column("previous_status", sa.Text(), nullable=True),
        sa.Column("new_status", sa.Text(), nullable=False),
        sa.Column(
            "reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        # When the verdict was computed, and when Drake wrote it down. Two
        # different facts: a delayed write must not look like a late change.
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("evaluation_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            f"new_status IN ({_HEALTH_STATUSES})", name="ck_health_transition_new_status"
        ),
        sa.CheckConstraint(
            f"previous_status IS NULL OR previous_status IN ({_HEALTH_STATUSES})",
            name="ck_health_transition_previous_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reasons) = 'array'", name="ck_health_transition_reasons_array"
        ),
        sa.CheckConstraint("length(evaluation_id) <= 128", name="ck_health_transition_eval_id"),
    )
    op.create_index(
        "ix_health_transitions_binding_time",
        "service_health_transitions",
        ["binding_id", sa.text("computed_at DESC")],
    )
    op.create_index(
        "ix_health_transitions_service_time",
        "service_health_transitions",
        ["environment_service_id", sa.text("computed_at DESC")],
    )

    # --- incidents ------------------------------------------------------
    op.create_table(
        "incidents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_workload_bindings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "environment_service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environment_services.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
            nullable=False,
        ),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'critical'")),
        # Composed by the server from the reason dictionary. There is no
        # endpoint that accepts a title, so no user text can reach it.
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("primary_reason", sa.Text(), nullable=False),
        sa.Column(
            "opening_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("binding_revision", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_critical_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Only one source exists in this sprint. The column is here because
        # a resolution whose cause is unrecorded is not auditable, and
        # because manual resolution will need to be distinguishable later.
        sa.Column("resolution_source", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('open', 'acknowledged', 'resolved')", name="ck_incident_state"
        ),
        # One severity this sprint. Constrained rather than free text so a
        # future severity is a reviewed migration, not a typo.
        sa.CheckConstraint("severity IN ('critical')", name="ck_incident_severity"),
        sa.CheckConstraint(
            "resolution_source IS NULL OR resolution_source IN ('health_recovered')",
            name="ck_incident_resolution_source",
        ),
        # A resolved incident has a resolution; an unresolved one has none.
        # Without this, "resolved" could mean two different things.
        sa.CheckConstraint(
            "(state = 'resolved') = (resolved_at IS NOT NULL)",
            name="ck_incident_resolved_consistency",
        ),
        sa.CheckConstraint(
            "(acknowledged_at IS NULL) = (acknowledged_by IS NULL)",
            name="ck_incident_ack_consistency",
        ),
        sa.CheckConstraint("version >= 1", name="ck_incident_version"),
        sa.CheckConstraint("length(title) <= 200", name="ck_incident_title_length"),
        sa.CheckConstraint(f"primary_reason {_KEY_SHAPE}", name="ck_incident_primary_reason"),
        sa.CheckConstraint(
            "jsonb_typeof(opening_reasons) = 'array'", name="ck_incident_reasons_array"
        ),
    )
    # The rule that application code must not be trusted with alone: at
    # most one active incident per binding. Two workers racing to open one
    # both pass an application-level check; only one survives this.
    op.create_index(
        "uq_incident_active_per_binding",
        "incidents",
        ["binding_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'acknowledged')"),
    )
    op.create_index("ix_incidents_service", "incidents", ["environment_service_id"])
    op.create_index("ix_incidents_environment", "incidents", ["environment_id"])
    op.create_index("ix_incidents_state_opened", "incidents", ["state", sa.text("opened_at DESC")])

    # --- incident timeline ----------------------------------------------
    op.create_table(
        "incident_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Present only for events a person caused. A system event has no
        # actor, and pretending otherwise would misattribute it.
        sa.Column(
            "actor_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # Bounded, server-composed detail: reason codes and counters only.
        sa.Column(
            "detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type IN ('opened', 'acknowledged', 'recovery_started', "
            "'recovery_interrupted', 'auto_resolved')",
            name="ck_incident_event_type",
        ),
        sa.CheckConstraint("jsonb_typeof(detail) = 'object'", name="ck_incident_event_detail"),
        # A timeline entry is a note, not a payload. The bound is small on
        # purpose: anything that does not fit does not belong here.
        sa.CheckConstraint("pg_column_size(detail) <= 4096", name="ck_incident_event_detail_size"),
    )
    op.create_index(
        "ix_incident_events_incident_time", "incident_events", ["incident_id", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_table("incident_events")
    op.drop_table("incidents")
    op.drop_table("service_health_transitions")
    op.drop_table("service_health_state")
