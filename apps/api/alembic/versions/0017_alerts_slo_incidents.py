"""Alertmanager alerts, SLO evaluation, and the operations they feed.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-08

The division of labour this migration encodes:

    PrometheusRule = decides when a condition is true
    Alertmanager   = grouping, dedupe, inhibition, silence, base notification
    Drake          = business context, incident projection, ownership,
                     timeline, SLO visibility, controlled operations

Drake is not writing a second Prometheus and not writing a second
Alertmanager. What it adds is the part neither of them has: which project
and service an alert belongs to, who owns it, what happened afterwards, and
how much error budget is left.

Six ideas carry the schema:

- **Identity is the provider's fingerprint, per integration.** Not the
  `groupKey` — a group is a notification batch and its membership changes
  between deliveries, so keying anything on it would merge unrelated
  services and split related ones.
- **Events are append-only and deduplicated in the DATABASE.** Alertmanager
  retries; a retried notification must not grow the timeline. The unique
  constraint is the arbiter, not an application pre-check.
- **Provider time and Drake time are separate columns**, everywhere. A late
  delivery is late, not old, and an out-of-order resolved event must never
  drag a current firing projection backwards.
- **An incident no longer requires a workload binding.** A protection or
  project-level signal with a resolved project/environment scope is a real
  problem; refusing to record it because no service binding existed was
  silence, not safety. Dedup identity moves to a canonical correlation key.
- **SLO evaluations freeze their objective.** The objective, its version,
  and the burn profile are written onto every evaluation row, so tightening
  a target tomorrow cannot retroactively rewrite last month's compliance.
- **Silences are a request with a provider outcome**, not a local flag. A
  silence Drake failed to create at Alertmanager is `failed`, never
  `active` — showing it as active would mean an operator believes an alert
  is suppressed when it is not.

Nothing here stores a raw webhook body, an authorization header, a bearer
token, a provider URL, an arbitrary annotation URL, a PromQL expression, or
a metric sample. There are no columns for them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY_SHAPE = "~ '^[a-z0-9][a-z0-9_.:/-]{0,127}$'"

_ALERT_STATUSES = "'firing', 'resolved'"
_SEVERITIES = "'critical', 'high', 'medium', 'info', 'unknown'"
_PRIORITIES = "'P1', 'P2', 'P3', 'P4'"
_MAPPING_STATES = "'mapped', 'unmapped', 'ambiguous'"


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


def _incident_extensions() -> None:
    """Teach the Sprint 6 incident table about sources other than health."""
    op.add_column(
        "incidents",
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'service_health'")),
    )
    # The canonical dedup identity for an incident that has no binding.
    # Server-composed from resolved identifiers — there is no endpoint
    # through which a caller supplies one.
    op.add_column("incidents", sa.Column("correlation_key", sa.Text(), nullable=True))
    op.add_column("incidents", sa.Column("priority", sa.Text(), nullable=True))
    op.add_column("incidents", sa.Column("owner_team", sa.Text(), nullable=True))
    op.add_column(
        "incidents",
        sa.Column(
            "assigned_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column("incidents", sa.Column("assigned_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        "incidents",
        sa.Column(
            "assigned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    # An alert clearing is not the same fact as a service proving healthy
    # twice. It is recorded as mitigation and leaves the incident open for a
    # human to close through the normal lifecycle.
    op.add_column(
        "incidents", sa.Column("mitigated_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )

    # A project-level signal has no workload. Requiring one meant a real
    # problem went unrecorded because it could not be filed against a pod.
    for column in ("binding_id", "environment_service_id", "service_id"):
        op.alter_column("incidents", column, nullable=True)

    op.create_check_constraint(
        "ck_incident_source",
        "incidents",
        "source IN ('service_health', 'protection', 'alert')",
    )
    op.create_check_constraint(
        "ck_incident_priority", "incidents", f"priority IS NULL OR priority IN ({_PRIORITIES})"
    )
    op.create_check_constraint(
        "ck_incident_correlation_key_shape",
        "incidents",
        f"correlation_key IS NULL OR correlation_key {_KEY_SHAPE}",
    )
    op.create_check_constraint(
        "ck_incident_owner_team_shape",
        "incidents",
        f"owner_team IS NULL OR owner_team {_KEY_SHAPE}",
    )
    # Every incident must be dedupable by SOMETHING. Without this an
    # incident with neither a binding nor a correlation key would escape
    # both unique indexes and duplicate freely.
    op.create_check_constraint(
        "ck_incident_dedup_identity",
        "incidents",
        "binding_id IS NOT NULL OR correlation_key IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_incident_assignment_consistency",
        "incidents",
        "(assigned_identity_id IS NULL) = (assigned_at IS NULL)",
    )

    # `high` joins the vocabulary because P2 is a real incident severity and
    # calling it `critical` would flatten the distinction the priority
    # mapping exists to make.
    op.drop_constraint("ck_incident_severity", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_severity", "incidents", "severity IN ('critical', 'high')"
    )
    op.drop_constraint("ck_incident_resolution_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_resolution_source",
        "incidents",
        "resolution_source IS NULL OR resolution_source IN "
        "('health_recovered', 'alert_resolved', 'protection_recovered')",
    )

    # Active-incident uniqueness, per SOURCE. An Alertmanager alert and a
    # health verdict on the same workload are two different problems with
    # two different resolutions; one index across both would let the first
    # to arrive suppress the second silently.
    op.drop_index("uq_incident_active_per_binding", table_name="incidents")
    op.create_index(
        "uq_incident_active_per_binding",
        "incidents",
        ["source", "binding_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'acknowledged') AND binding_id IS NOT NULL"),
    )
    # The same rule for incidents that have no binding. Two workers racing
    # to open one for the same alert lose the race here, in the only place
    # losing it is possible.
    op.create_index(
        "uq_incident_active_per_correlation",
        "incidents",
        ["correlation_key"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('open', 'acknowledged') AND correlation_key IS NOT NULL"
        ),
    )
    op.create_index("ix_incidents_source_state", "incidents", ["source", "state"])

    op.drop_constraint("ck_incident_event_type", "incident_events", type_="check")
    op.create_check_constraint(
        "ck_incident_event_type",
        "incident_events",
        "event_type IN ('opened', 'acknowledged', 'recovery_started', "
        "'recovery_interrupted', 'auto_resolved', 'assigned', 'unassigned', "
        "'alert_firing', 'alert_resolved', 'alert_reopened', 'mitigated', "
        "'correlated', 'silence_requested', 'silence_active', 'silence_failed', "
        "'silence_expired')",
    )

    # Notification policies may now subscribe to P2 as well. Existing rows
    # are untouched: widening the vocabulary is not the same as widening a
    # policy someone already wrote.
    op.drop_constraint("ck_np_severities_allowed", "notification_policies", type_="check")
    op.create_check_constraint(
        "ck_np_severities_allowed",
        "notification_policies",
        'severities <@ \'["critical", "high"]\'::jsonb',
    )


def _alert_tables() -> None:
    op.create_table(
        "alert_instances",
        _uuid_pk(),
        # Which Alertmanager reported this. Identity is per integration, so
        # two Alertmanagers using the same fingerprint stay distinct.
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("alert_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("priority", sa.Text(), nullable=False, server_default=sa.text("'P3'")),
        # Catalog resolution: nullable on purpose. An alert Drake cannot map
        # is kept as evidence, not attached to a guess.
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "environment_service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environment_services.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("namespace", sa.Text(), nullable=True),
        sa.Column("owner_team", sa.Text(), nullable=True),
        # REFERENCE keys, resolved server-side against a reviewed registry.
        # Never a URL: `generatorURL`, `externalURL` and annotation links are
        # attacker-influenceable and are not stored anywhere in this schema.
        sa.Column("slo_key", sa.Text(), nullable=True),
        sa.Column("runbook_key", sa.Text(), nullable=True),
        sa.Column("mapping_state", sa.Text(), nullable=False, server_default=sa.text("'unmapped'")),
        sa.Column("mapping_error_code", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ends_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        # What the provider says happened, and when Drake heard it. Two
        # different facts; collapsing them makes a delayed delivery look
        # like a delayed outage.
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Allowlisted labels and annotations only, bounded below. Anything
        # outside the allowlist is dropped at ingest and never reaches here.
        sa.Column(
            "labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "annotations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # How many separate firing episodes this alert identity has had. A
        # reopen increments it, which is what makes a repeated startsAt from
        # a new episode a new event rather than a duplicate.
        sa.Column("occurrence", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("silenced", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("inhibited", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        # The canonical identity. Alertmanager's `groupKey` is deliberately
        # NOT part of it: a group is a notification batch whose membership
        # changes between deliveries.
        sa.UniqueConstraint("integration_id", "fingerprint", name="uq_alert_identity"),
        sa.CheckConstraint(f"status IN ({_ALERT_STATUSES})", name="ck_alert_status"),
        sa.CheckConstraint(f"severity IN ({_SEVERITIES})", name="ck_alert_severity"),
        sa.CheckConstraint(f"priority IN ({_PRIORITIES})", name="ck_alert_priority"),
        sa.CheckConstraint(f"mapping_state IN ({_MAPPING_STATES})", name="ck_alert_mapping_state"),
        sa.CheckConstraint("length(fingerprint) BETWEEN 1 AND 128", name="ck_alert_fingerprint"),
        sa.CheckConstraint("length(alert_name) BETWEEN 1 AND 200", name="ck_alert_name_length"),
        sa.CheckConstraint(
            f"owner_team IS NULL OR owner_team {_KEY_SHAPE}", name="ck_alert_owner_team"
        ),
        sa.CheckConstraint(f"slo_key IS NULL OR slo_key {_KEY_SHAPE}", name="ck_alert_slo_key"),
        sa.CheckConstraint(
            f"runbook_key IS NULL OR runbook_key {_KEY_SHAPE}", name="ck_alert_runbook_key"
        ),
        sa.CheckConstraint("jsonb_typeof(labels) = 'object'", name="ck_alert_labels_object"),
        sa.CheckConstraint(
            "jsonb_typeof(annotations) = 'object'", name="ck_alert_annotations_object"
        ),
        # Small on purpose. A label set that does not fit is not a label set.
        sa.CheckConstraint("pg_column_size(labels) <= 4096", name="ck_alert_labels_size"),
        sa.CheckConstraint("pg_column_size(annotations) <= 4096", name="ck_alert_annotations_size"),
        sa.CheckConstraint("occurrence >= 1", name="ck_alert_occurrence"),
        sa.CheckConstraint("version >= 1", name="ck_alert_version"),
        # An unmapped alert has no project, and a mapped one has a project.
        # Without this, "mapped" could quietly mean nothing.
        sa.CheckConstraint(
            "(mapping_state = 'mapped') = (project_id IS NOT NULL)",
            name="ck_alert_mapping_consistency",
        ),
    )
    op.create_index("ix_alert_instances_project", "alert_instances", ["project_id", "status"])
    op.create_index(
        "ix_alert_instances_status_seen",
        "alert_instances",
        ["status", sa.text("last_seen_at DESC")],
    )
    op.create_index("ix_alert_instances_incident", "alert_instances", ["incident_id"])

    op.create_table(
        "alert_events",
        _uuid_pk(),
        sa.Column(
            "alert_instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("occurrence", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Derived from the alert's own immutable fields, so a retried
        # Alertmanager notification produces the same key and lands on the
        # unique constraint instead of growing the timeline.
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column(
            "delivery_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alertmanager_deliveries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # The database is the arbiter of "already recorded". An application
        # pre-check loses to a concurrent delivery; this does not.
        sa.UniqueConstraint("alert_instance_id", "dedupe_key", name="uq_alert_event_identity"),
        sa.CheckConstraint(
            "event_type IN ('firing', 'resolved', 'reopened', 'suppressed', "
            "'silenced', 'inhibited')",
            name="ck_alert_event_type",
        ),
        sa.CheckConstraint(f"status IN ({_ALERT_STATUSES})", name="ck_alert_event_status"),
        sa.CheckConstraint("jsonb_typeof(detail) = 'object'", name="ck_alert_event_detail"),
        sa.CheckConstraint("pg_column_size(detail) <= 2048", name="ck_alert_event_detail_size"),
        sa.CheckConstraint("length(dedupe_key) <= 128", name="ck_alert_event_dedupe_key"),
    )
    op.create_index(
        "ix_alert_events_instance_time",
        "alert_events",
        ["alert_instance_id", sa.text("source_event_at DESC")],
    )

    op.create_table(
        "alert_incident_links",
        _uuid_pk(),
        sa.Column(
            "alert_instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # `primary` — this alert opened the incident. `correlated` — the
        # incident already existed for the same problem, so the alert is
        # attached rather than duplicated.
        sa.Column("link_type", sa.Text(), nullable=False, server_default=sa.text("'primary'")),
        sa.Column(
            "linked_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("alert_instance_id", "incident_id", name="uq_alert_incident_link"),
        sa.CheckConstraint(
            "link_type IN ('primary', 'correlated')", name="ck_alert_incident_link_type"
        ),
    )


def _delivery_table() -> None:
    op.create_table(
        "alertmanager_deliveries",
        _uuid_pk(),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # A digest of the exact payload, not the payload. Replaying the same
        # notification is recognised without keeping the body it carried.
        sa.Column("delivery_digest", sa.Text(), nullable=False),
        sa.Column("receiver", sa.Text(), nullable=True),
        # Recorded for operator diagnostics only. It is deliberately not an
        # identity for anything: group membership changes between sends.
        sa.Column("group_key_digest", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("truncated_alerts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unmapped_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("outcome", sa.Text(), nullable=False, server_default=sa.text("'accepted'")),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("payload_version", sa.Integer(), nullable=True),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Alertmanager retries the same notification. Recognising it here
        # keeps a retry from being counted as new evidence of anything.
        sa.UniqueConstraint(
            "integration_id", "delivery_digest", name="uq_alertmanager_delivery_identity"
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'partial', 'rejected', 'duplicate')",
            name="ck_am_delivery_outcome",
        ),
        sa.CheckConstraint("length(delivery_digest) <= 128", name="ck_am_delivery_digest"),
        sa.CheckConstraint("truncated_alerts >= 0", name="ck_am_delivery_truncated"),
        sa.CheckConstraint("alert_count >= 0", name="ck_am_delivery_alert_count"),
    )
    op.create_index(
        "ix_am_deliveries_integration_time",
        "alertmanager_deliveries",
        ["integration_id", sa.text("received_at DESC")],
    )


def _slo_tables() -> None:
    op.create_table(
        "slo_definitions",
        _uuid_pk(),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "environment_service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environment_services.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("slo_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("indicator", sa.Text(), nullable=False),
        # Stored as a ratio (0.995), never a percentage. One representation,
        # so nothing can be off by a factor of a hundred.
        sa.Column("objective_ratio", sa.Numeric(9, 7), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        # Which CURATED query template measures this. Not PromQL: there is
        # no column here an expression could be stored in.
        sa.Column("sli_template_key", sa.Text(), nullable=False),
        sa.Column(
            "sli_template_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        # Latency thresholds come from a reviewed profile, so a frontend
        # cannot decide what "fast enough" means.
        sa.Column("threshold_profile_key", sa.Text(), nullable=True),
        sa.Column("burn_profile_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "effective_from",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("catalog_revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.UniqueConstraint("project_id", "slo_key", name="uq_slo_definition_identity"),
        sa.CheckConstraint("indicator IN ('availability', 'latency')", name="ck_slo_indicator"),
        # A 0% objective is meaningless and a >100% one is impossible. 100%
        # itself is allowed and handled explicitly as a zero-error policy.
        sa.CheckConstraint(
            "objective_ratio > 0 AND objective_ratio <= 1", name="ck_slo_objective_range"
        ),
        sa.CheckConstraint("window_seconds BETWEEN 3600 AND 7776000", name="ck_slo_window_range"),
        sa.CheckConstraint(f"slo_key {_KEY_SHAPE}", name="ck_slo_key_shape"),
        sa.CheckConstraint(f"sli_template_key {_KEY_SHAPE}", name="ck_slo_template_shape"),
        sa.CheckConstraint(f"burn_profile_key {_KEY_SHAPE}", name="ck_slo_burn_profile_shape"),
        sa.CheckConstraint("version >= 1", name="ck_slo_version"),
    )
    op.create_index("ix_slo_definitions_project", "slo_definitions", ["project_id", "enabled"])
    op.create_index("ix_slo_definitions_service", "slo_definitions", ["environment_service_id"])

    op.create_table(
        "slo_evaluations",
        _uuid_pk(),
        sa.Column(
            "slo_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("slo_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The definition version this evaluation judged. Together with the
        # frozen objective below it is what stops a tightened target from
        # rewriting last month's compliance.
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("objective_ratio", sa.Numeric(9, 7), nullable=False),
        sa.Column("burn_profile_key", sa.Text(), nullable=False),
        sa.Column("evaluated_for", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("window_end", sa.TIMESTAMP(timezone=True), nullable=False),
        # A bounded SUMMARY of the window. Raw samples stay in Prometheus:
        # copying them into Postgres would make Drake a second, worse TSDB
        # and a second copy of everything a metric happens to carry.
        sa.Column("good_observations", sa.Float(), nullable=True),
        sa.Column("bad_observations", sa.Float(), nullable=True),
        sa.Column("total_observations", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("compliance_ratio", sa.Float(), nullable=True),
        sa.Column("error_budget_total", sa.Float(), nullable=True),
        sa.Column("error_budget_consumed", sa.Float(), nullable=True),
        # May be negative. An exhausted budget that renders as zero is a
        # comfortable lie about how far past the objective a service is.
        sa.Column("error_budget_remaining", sa.Float(), nullable=True),
        sa.Column(
            "burn_rates", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("data_quality", sa.Text(), nullable=False, server_default=sa.text("'ok'")),
        sa.Column("freshness_seconds", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("source_event_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "slo_definition_id",
            "evaluated_for",
            "definition_version",
            name="uq_slo_evaluation_period",
        ),
        sa.CheckConstraint(
            "status IN ('healthy', 'warning', 'critical', 'exhausted', "
            "'insufficient_data', 'stale', 'query_failed', 'not_configured')",
            name="ck_slo_evaluation_status",
        ),
        sa.CheckConstraint(
            "data_quality IN ('ok', 'partial', 'stale', 'empty', 'failed')",
            name="ck_slo_evaluation_data_quality",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(burn_rates) = 'array'", name="ck_slo_evaluation_burn_array"
        ),
        sa.CheckConstraint(
            "pg_column_size(burn_rates) <= 4096", name="ck_slo_evaluation_burn_size"
        ),
        sa.CheckConstraint("window_end > window_start", name="ck_slo_evaluation_window"),
        sa.CheckConstraint(
            "objective_ratio > 0 AND objective_ratio <= 1",
            name="ck_slo_evaluation_objective_range",
        ),
    )
    op.create_index(
        "ix_slo_evaluations_definition_time",
        "slo_evaluations",
        ["slo_definition_id", sa.text("evaluated_for DESC")],
    )


def _silence_table() -> None:
    op.create_table(
        "silence_requests",
        _uuid_pk(),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "alert_instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_instances.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # BACKEND-produced exact matchers, from resolved values. There is no
        # request field through which a caller supplies one, and the check
        # below refuses a regex matcher even if code ever tried to write it.
        sa.Column("matchers", postgresql.JSONB(), nullable=False),
        sa.Column("requested_seconds", sa.Integer(), nullable=False),
        # Required, bounded, and from a reviewed vocabulary — a silence with
        # no stated reason is how an alert quietly disappears forever.
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("reason_note", sa.Text(), nullable=True),
        sa.Column(
            "actor_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("starts_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ends_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Assigned by Alertmanager, and only after it accepted the silence.
        sa.Column("provider_silence_id", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        # A bounded code. A provider's own message can carry its URL, its
        # configuration, or its own secrets.
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'expired', 'failed', 'cancel_pending', 'cancelled')",
            name="ck_silence_state",
        ),
        # A silence is a bounded pause, not an off switch. The ceiling is
        # enforced here as well as in settings so no code path can exceed it.
        sa.CheckConstraint(
            "requested_seconds BETWEEN 60 AND 604800", name="ck_silence_duration_range"
        ),
        sa.CheckConstraint("jsonb_typeof(matchers) = 'array'", name="ck_silence_matchers_array"),
        sa.CheckConstraint(
            "jsonb_array_length(matchers) BETWEEN 1 AND 8", name="ck_silence_matchers_count"
        ),
        # Exact match only, enforced in the database. A regex matcher is how
        # "silence this alert" becomes "silence this environment". Written as
        # a containment test because a CHECK may not contain a subquery — the
        # property is the same: no element may carry `isRegex: true`.
        sa.CheckConstraint(
            "NOT (matchers @> '[{\"isRegex\": true}]'::jsonb) "
            "AND matchers @> '[{\"isRegex\": false}]'::jsonb",
            name="ck_silence_matchers_exact",
        ),
        sa.CheckConstraint("pg_column_size(matchers) <= 2048", name="ck_silence_matchers_size"),
        sa.CheckConstraint(f"reason_code {_KEY_SHAPE}", name="ck_silence_reason_shape"),
        sa.CheckConstraint(
            "reason_note IS NULL OR length(reason_note) <= 280", name="ck_silence_note_length"
        ),
        sa.CheckConstraint(
            "provider_silence_id IS NULL OR length(provider_silence_id) <= 128",
            name="ck_silence_provider_id",
        ),
        # A silence is only active once the provider says so. Without this,
        # `active` could be written with nothing behind it.
        sa.CheckConstraint(
            "state <> 'active' OR provider_silence_id IS NOT NULL",
            name="ck_silence_active_requires_provider",
        ),
        sa.CheckConstraint("version >= 1", name="ck_silence_version"),
    )
    op.create_index(
        "ix_silence_requests_project_state", "silence_requests", ["project_id", "state"]
    )
    op.create_index("ix_silence_requests_alert", "silence_requests", ["alert_instance_id"])
    op.create_index(
        "ix_silence_requests_due",
        "silence_requests",
        ["next_attempt_at"],
        postgresql_where=sa.text("state IN ('pending', 'cancel_pending')"),
    )


def upgrade() -> None:
    _incident_extensions()
    # Deliveries first: alert_events references them.
    _delivery_table()
    _alert_tables()
    _slo_tables()
    _silence_table()


def downgrade() -> None:
    op.drop_table("silence_requests")
    op.drop_table("slo_evaluations")
    op.drop_table("slo_definitions")
    op.drop_table("alert_incident_links")
    op.drop_table("alert_events")
    op.drop_table("alert_instances")
    op.drop_table("alertmanager_deliveries")

    op.drop_constraint("ck_np_severities_allowed", "notification_policies", type_="check")
    op.create_check_constraint(
        "ck_np_severities_allowed",
        "notification_policies",
        "severities <@ '[\"critical\"]'::jsonb",
    )
    op.drop_constraint("ck_incident_event_type", "incident_events", type_="check")
    op.create_check_constraint(
        "ck_incident_event_type",
        "incident_events",
        "event_type IN ('opened', 'acknowledged', 'recovery_started', "
        "'recovery_interrupted', 'auto_resolved')",
    )
    op.drop_index("ix_incidents_source_state", table_name="incidents")
    op.drop_index("uq_incident_active_per_correlation", table_name="incidents")
    op.drop_index("uq_incident_active_per_binding", table_name="incidents")
    op.create_index(
        "uq_incident_active_per_binding",
        "incidents",
        ["binding_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'acknowledged')"),
    )
    for name in (
        "ck_incident_source",
        "ck_incident_priority",
        "ck_incident_correlation_key_shape",
        "ck_incident_owner_team_shape",
        "ck_incident_dedup_identity",
        "ck_incident_assignment_consistency",
    ):
        op.drop_constraint(name, "incidents", type_="check")
    op.drop_constraint("ck_incident_resolution_source", "incidents", type_="check")
    op.create_check_constraint(
        "ck_incident_resolution_source",
        "incidents",
        "resolution_source IS NULL OR resolution_source IN ('health_recovered')",
    )
    op.drop_constraint("ck_incident_severity", "incidents", type_="check")
    op.create_check_constraint("ck_incident_severity", "incidents", "severity IN ('critical')")
    for column in ("binding_id", "environment_service_id", "service_id"):
        op.alter_column("incidents", column, nullable=False)
    for column in (
        "mitigated_at",
        "assigned_by",
        "assigned_at",
        "assigned_identity_id",
        "owner_team",
        "priority",
        "correlation_key",
        "source",
    ):
        op.drop_column("incidents", column)
