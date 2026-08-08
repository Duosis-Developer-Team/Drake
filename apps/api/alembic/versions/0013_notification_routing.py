"""Notification routing, the in-app inbox, and a reliable delivery outbox.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-08

Sprint 6 produced an immutable incident timeline. Nothing read it, so an
incident opened at 3am was only visible to whoever happened to be looking
at the screen.

This turns those events into deliveries. The shape is an outbox, because
the alternative — calling a webhook inside the incident transaction —
means a slow receiver can hold a database lock, and a failing one can roll
back the incident that caused it. Instead: the incident commits, and a
planner later reads the committed events and writes delivery rows in its
own transaction.

The tables:

- `notification_policies` — who wants to hear about what, scoped to a
  project. Filters are ids and enum allowlists; there is no matcher
  language, so a policy cannot become a query.
- `notification_destinations` — a Drake user, or an opaque key into the
  operator's runtime webhook registry. **No URL, token or header is stored
  here**; the key resolves to a target in settings, at send time.
- `notification_policy_destinations` — the many-to-many, with the pair
  unique so a destination cannot be attached to a policy twice.
- `notification_event_plans` — one row per incident event, marking it
  planned. This is what makes the planner idempotent and what stops an
  event with no matching policy from being rescanned forever.
- `in_app_notifications` — the inbox. Unique per (event, destination) so
  two policies matching the same person produce one notification.
- `webhook_deliveries` — the outbox, with a stable idempotency key and a
  payload snapshot frozen at plan time, so editing a policy never rewrites
  a delivery that was already scheduled.
- `webhook_delivery_attempts` — append-only audit. Outcome codes, HTTP
  status and duration; never a response body, a URL, or a secret.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEY_SHAPE = "~ '^[a-z0-9][a-z0-9_.-]{0,63}$'"

# The incident events a policy may subscribe to. `recovery_started` and
# `recovery_interrupted` are deliberately absent: they are internal
# progress markers, and notifying on them would page someone twice for one
# recovery.
_NOTIFIABLE_EVENTS = '"opened", "acknowledged", "auto_resolved"'


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
    # --- policies -------------------------------------------------------
    op.create_table(
        "notification_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("display_name", sa.Text(), nullable=False),
        # A policy always belongs to a project. Environment and service are
        # optional narrowings, and both are checked to live inside that
        # project before a policy can reference them.
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
        sa.Column(
            "service_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_definitions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # Allowlists, not expressions. Every value is checked against a
        # fixed vocabulary before insert.
        sa.Column("event_types", postgresql.JSONB(), nullable=False),
        sa.Column(
            "severities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[\"critical\"]'::jsonb"),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        *_timestamps(),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_np_name_length"),
        sa.CheckConstraint("version >= 1", name="ck_np_version"),
        sa.CheckConstraint(
            "jsonb_typeof(event_types) = 'array' AND jsonb_array_length(event_types) > 0",
            name="ck_np_event_types_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(severities) = 'array' AND jsonb_array_length(severities) > 0",
            name="ck_np_severities_array",
        ),
        # The vocabulary is enforced here too, not only in the service: a
        # future caller cannot subscribe a policy to an event Drake does
        # not emit. Containment (`<@`) rather than a subquery, which check
        # constraints do not allow.
        sa.CheckConstraint(
            f"event_types <@ '[{_NOTIFIABLE_EVENTS}]'::jsonb",
            name="ck_np_event_types_allowed",
        ),
        sa.CheckConstraint(
            "severities <@ '[\"critical\"]'::jsonb",
            name="ck_np_severities_allowed",
        ),
    )
    op.create_index("ix_notification_policies_project", "notification_policies", ["project_id"])

    # --- destinations ---------------------------------------------------
    op.create_table(
        "notification_destinations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("destination_type", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        # Destinations are scoped like policies, so a project owner cannot
        # attach a user or webhook from outside their scope.
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # in_app_user: the recipient, selected from the central identity
        # directory — never typed in as an address.
        sa.Column(
            "identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        # webhook: an OPAQUE KEY into the operator's runtime registry.
        # There is no url, token, or header column here, and there never
        # should be — the target is resolved from settings at send time.
        sa.Column("destination_key", sa.Text(), nullable=True),
        sa.Column(
            "payload_schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "destination_type IN ('in_app_user', 'webhook')", name="ck_nd_type"
        ),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_nd_name_length"),
        # Each type carries exactly its own field and not the other's, so a
        # webhook row can never smuggle a recipient and vice versa.
        sa.CheckConstraint(
            "(destination_type = 'in_app_user') = (identity_id IS NOT NULL)",
            name="ck_nd_in_app_shape",
        ),
        sa.CheckConstraint(
            "(destination_type = 'webhook') = (destination_key IS NOT NULL)",
            name="ck_nd_webhook_shape",
        ),
        sa.CheckConstraint(
            f"destination_key IS NULL OR destination_key {_KEY_SHAPE}",
            name="ck_nd_key_shape",
        ),
        sa.CheckConstraint("version >= 1", name="ck_nd_version"),
        # One destination row per recipient per project, and one per
        # webhook key per project: duplicates would only produce duplicate
        # notifications.
        sa.UniqueConstraint("project_id", "identity_id", name="uq_nd_project_identity"),
        sa.UniqueConstraint("project_id", "destination_key", name="uq_nd_project_key"),
    )
    op.create_index(
        "ix_notification_destinations_project", "notification_destinations", ["project_id"]
    )

    op.create_table(
        "notification_policy_destinations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notification_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notification_destinations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("policy_id", "destination_id", name="uq_npd_pair"),
    )

    # --- planning state --------------------------------------------------
    op.create_table(
        "notification_event_plans",
        sa.Column(
            "incident_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incident_events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # `planned` covers "matched nothing" too. An event with no policy
        # is a finished decision, not an open one — without this it would
        # be rescanned on every cycle forever.
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'planned'")),
        sa.Column(
            "planned_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("planner_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("1")),
        # Bounded, server-owned classification. Never an exception message.
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("matched_destinations", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("state IN ('planned', 'failed')", name="ck_nep_state"),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'", name="ck_nep_error_code"
        ),
        sa.CheckConstraint("attempts >= 1", name="ck_nep_attempts"),
    )
    op.create_index(
        "ix_notification_event_plans_state", "notification_event_plans", ["state", "planned_at"]
    )

    # --- in-app inbox -----------------------------------------------------
    op.create_table(
        "in_app_notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "recipient_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "incident_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incident_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notification_destinations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Which policies matched, for auditability. A bounded array of ids,
        # not a rule snapshot: the text below is already frozen.
        sa.Column(
            "matched_policy_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        # Composed by the server from reason codes and catalog keys. There
        # is no endpoint through which a person can write either of these.
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # A relative in-app path, frozen at creation, so a notification
        # from last week still goes somewhere sensible.
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column(
            "metadata_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("read_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Two policies naming the same person produce ONE notification.
        sa.UniqueConstraint(
            "incident_event_id", "destination_id", name="uq_in_app_event_destination"
        ),
        sa.CheckConstraint("length(title) <= 200", name="ck_in_app_title_length"),
        sa.CheckConstraint("length(body) <= 1000", name="ck_in_app_body_length"),
        # A relative path only: an absolute URL here would make the inbox a
        # place to plant a link to somewhere else.
        sa.CheckConstraint(
            "target_path ~ '^/[A-Za-z0-9/_-]{0,200}$'", name="ck_in_app_target_path"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_snapshot) = 'object'", name="ck_in_app_metadata_object"
        ),
        sa.CheckConstraint(
            "pg_column_size(metadata_snapshot) <= 4096", name="ck_in_app_metadata_size"
        ),
    )
    op.create_index(
        "ix_in_app_recipient_unread",
        "in_app_notifications",
        ["recipient_identity_id", sa.text("created_at DESC")],
    )

    # --- webhook outbox ---------------------------------------------------
    op.create_table(
        "webhook_deliveries",
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
        sa.Column(
            "incident_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incident_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("notification_destinations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Snapshot of the key this delivery was planned against. If the
        # destination is later re-pointed, in-flight deliveries still go
        # where they were scheduled to go.
        sa.Column("destination_key", sa.Text(), nullable=False),
        sa.Column("payload_schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "matched_policy_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Frozen at plan time. Retries send exactly the same bytes, which is
        # what makes the idempotency key meaningful to the receiver.
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_attempt_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        *_timestamps(),
        # One delivery per event per destination, whatever matched it.
        sa.UniqueConstraint(
            "incident_event_id", "destination_id", name="uq_webhook_event_destination"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_webhook_idempotency_key"),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'retrying', 'delivered', "
            "'dead_letter', 'suppressed')",
            name="ck_webhook_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_webhook_attempts"),
        sa.CheckConstraint(
            "last_http_status IS NULL OR last_http_status BETWEEN 100 AND 599",
            name="ck_webhook_http_status",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code ~ '^[a-z0-9_]{1,64}$'",
            name="ck_webhook_error_code",
        ),
        sa.CheckConstraint("(state = 'delivered') = (delivered_at IS NOT NULL)",
                           name="ck_webhook_delivered_consistency"),
        sa.CheckConstraint("pg_column_size(payload) <= 16384", name="ck_webhook_payload_size"),
        sa.CheckConstraint(f"destination_key {_KEY_SHAPE}", name="ck_webhook_key_shape"),
    )
    # The claim index: due work, oldest first.
    op.create_index(
        "ix_webhook_deliveries_due",
        "webhook_deliveries",
        ["state", "next_attempt_at"],
    )
    op.create_index("ix_webhook_deliveries_incident", "webhook_deliveries", ["incident_id"])
    op.create_index("ix_webhook_deliveries_project", "webhook_deliveries", ["project_id"])

    op.create_table(
        "webhook_delivery_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "delivery_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("webhook_deliveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("retry_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("delivery_id", "attempt_number", name="uq_attempt_number"),
        sa.CheckConstraint(
            "outcome IN ('delivered', 'retryable', 'terminal', 'refused')",
            name="ck_attempt_outcome",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_attempt_http_status",
        ),
        # Bounded classification codes only. There is deliberately no
        # column for a response body, a URL, or an exception.
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z0-9_]{1,64}$'", name="ck_attempt_error_code"
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_attempt_number"),
    )
    op.create_index(
        "ix_webhook_attempts_delivery",
        "webhook_delivery_attempts",
        ["delivery_id", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_table("webhook_delivery_attempts")
    op.drop_table("webhook_deliveries")
    op.drop_table("in_app_notifications")
    op.drop_table("notification_event_plans")
    op.drop_table("notification_policy_destinations")
    op.drop_table("notification_destinations")
    op.drop_table("notification_policies")
