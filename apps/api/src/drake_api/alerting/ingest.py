"""Accepting an Alertmanager notification and projecting it.

The chain this module implements, and nothing beyond it:

    authenticated webhook → alert projection → incident correlation

What keeps it safe to expose:

**The integration decides the project, not the payload.** A request
authenticates with an opaque key registered against exactly one project. An
alert's own `project` label is checked AGAINST that registration, never
trusted instead of it — so a rule author in one project cannot file
incidents in another.

**Every alert in a group is normalized on its own.** The delivery's status
summarises a batch; a batch marked `resolved` can still carry a firing
alert, and letting the group win would close an incident for a service that
is still down. Two services in one group become two alerts and, if they
warrant it, two incidents.

**Replay changes nothing.** The delivery digest is unique per integration
and each alert event carries a dedupe key derived from immutable alert
facts. Alertmanager retries as a matter of course; a retry must not grow a
timeline, open a second incident, or plan a second notification.

**An older event never wins.** Projection updates are guarded on
`source_event_at`, so a redelivered `resolved` from an hour ago cannot drag
a currently firing alert back to resolved.

**An unmapped alert is kept, not placed.** Evidence Drake cannot resolve
into the catalog is recorded as integration evidence and shown to platform
operators. It opens no incident, because an incident filed against a guess
is worse than one not filed at all.

Nothing here stores the raw body, the authorization header, `generatorURL`,
`externalURL`, or any annotation URL. There are no columns for them.
"""

import hmac
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.alerting.model import (
    AlertStatus,
    IngestRejectedError,
    MappingState,
    NormalizedAlert,
    NormalizedDelivery,
    correlation_key,
    incident_severity,
    incident_title,
    opens_incident,
    protection_correlation_key,
)
from drake_api.settings import AlertmanagerIntegration, Settings

logger = logging.getLogger("drake_api.alerting.ingest")

INTEGRATION_TYPE = "alertmanager"


@dataclass
class AlertOutcome:
    """What one alert changed. All-zero is a normal, correct outcome."""

    fingerprint: str
    mapping_state: str
    event_recorded: bool = False
    incident_opened: uuid.UUID | None = None
    incident_updated: uuid.UUID | None = None
    incident_correlated: uuid.UUID | None = None
    reopened: bool = False
    stale: bool = False


@dataclass
class DeliveryOutcome:
    delivery_id: uuid.UUID | None = None
    duplicate: bool = False
    accepted: int = 0
    rejected: int = 0
    unmapped: int = 0
    alerts: list[AlertOutcome] = field(default_factory=list)


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


def resolve_integration(settings: Settings, key: str | None) -> tuple[str, AlertmanagerIntegration]:
    """Server-side resolution. A payload never names its own integration."""
    if not key:
        raise IngestRejectedError("integration_missing")
    integration = settings.alertmanager_integrations.get(key)
    if integration is None:
        raise IngestRejectedError("integration_unknown")
    return key, integration


def load_token(reference: str) -> bytes | None:
    """Read a credential from its file reference, or None.

    The material is held for the length of one comparison and never
    returned, logged, or stored.
    """
    if not reference:
        return None
    try:
        material = Path(reference).read_bytes().strip()
    except OSError:
        logger.warning("alertmanager credential reference could not be read")
        return None
    return material or None


def verify_bearer(integration: AlertmanagerIntegration, header: str | None) -> None:
    """Alertmanager's native webhook auth: a bearer token, nothing more.

    Native Alertmanager does not sign its body, so there is no HMAC to
    verify. Pretending otherwise — accepting a `X-Signature` header that
    Alertmanager never produces — would be inventing a guarantee, and a
    forged header is worse than an honest bearer token over TLS. A signing
    proxy in front of Alertmanager would be a separate, versioned auth mode.
    """
    secret = load_token(integration.webhook_token_file)
    if secret is None:
        # An integration whose credential cannot be read cannot authenticate
        # anyone. Refusing beats accepting on the strength of a missing file.
        raise IngestRejectedError("integration_unconfigured")
    if not header:
        raise IngestRejectedError("authorization_missing")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise IngestRejectedError("authorization_malformed")
    # Constant time: a fast reject leaks the prefix that matched.
    if not hmac.compare_digest(presented.strip().encode(), secret):
        raise IngestRejectedError("authorization_invalid")


async def ensure_integration_row(
    engine: AsyncEngine, config_ref: str, project_key: str
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """The `integrations` row this Alertmanager reports through.

    Returns `(integration_id, project_id)`, or None when the registered
    project is not in the catalog — a registration naming a project that
    does not exist is a configuration error, not a reason to invent one.
    """
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text("SELECT id, scope_id FROM projects WHERE project_key = :key"),
                {"key": project_key},
            )
        ).first()
        if row is None:
            return None
        project_id, scope_id = row[0], row[1]
        await connection.execute(
            text(
                """
                INSERT INTO integrations
                    (integration_type, scope_id, configuration_state, config_ref)
                VALUES (:type, :scope, 'configured', :ref)
                ON CONFLICT (integration_type, scope_id) DO UPDATE
                SET configuration_state = 'configured',
                    config_ref = EXCLUDED.config_ref,
                    updated_at = now()
                """
            ),
            {"type": INTEGRATION_TYPE, "scope": scope_id, "ref": config_ref},
        )
        integration_id = (
            await connection.execute(
                text(
                    "SELECT id FROM integrations WHERE integration_type = :type "
                    "AND scope_id = :scope"
                ),
                {"type": INTEGRATION_TYPE, "scope": scope_id},
            )
        ).scalar_one()
    return uuid.UUID(str(integration_id)), uuid.UUID(str(project_id))


# ---------------------------------------------------------------------------
# catalog resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogBinding:
    """Where an alert belongs, as far as the catalog can prove."""

    state: str
    project_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    environment_service_id: uuid.UUID | None = None
    cluster_id: uuid.UUID | None = None
    namespace: str | None = None
    error_code: str | None = None


async def resolve_catalog(
    connection: AsyncConnection,
    project_id: uuid.UUID,
    project_key: str,
    labels: dict[str, str],
) -> CatalogBinding:
    """Alert labels → catalog identifiers, fail-closed.

    The integration already fixes the project. A `project` label is
    therefore a claim to CHECK, not a selector to honour: a mismatch is
    refused rather than resolved, because an alert asking to be filed
    somewhere else is precisely what must not work.

    Every other label must resolve exactly or the alert is quarantined.
    Attaching an alert to a service that merely looks similar produces an
    incident for the wrong team, which is worse than an unmapped one.
    """
    claimed = labels.get("project")
    if claimed and claimed != project_key:
        return CatalogBinding(str(MappingState.UNMAPPED), error_code="project_mismatch")

    environment_id: uuid.UUID | None = None
    environment_key = labels.get("environment")
    if environment_key:
        found = (
            await connection.execute(
                text(
                    "SELECT id FROM environments "
                    "WHERE project_id = :project AND environment_key = :key"
                ),
                {"project": project_id, "key": environment_key},
            )
        ).first()
        if found is None:
            return CatalogBinding(str(MappingState.UNMAPPED), error_code="environment_unknown")
        environment_id = uuid.UUID(str(found[0]))

    service_id: uuid.UUID | None = None
    environment_service_id: uuid.UUID | None = None
    service_key = labels.get("service")
    if service_key:
        found = (
            await connection.execute(
                text(
                    "SELECT id FROM service_definitions "
                    "WHERE project_id = :project AND service_key = :key"
                ),
                {"project": project_id, "key": service_key},
            )
        ).first()
        if found is None:
            return CatalogBinding(str(MappingState.UNMAPPED), error_code="service_unknown")
        service_id = uuid.UUID(str(found[0]))

        rows = (
            await connection.execute(
                text(
                    """
                    SELECT es.id, es.environment_id FROM environment_services es
                    WHERE es.project_id = :project AND es.service_id = :service
                      AND (CAST(:environment AS uuid) IS NULL OR es.environment_id = :environment)
                    """
                ),
                {
                    "project": project_id,
                    "service": service_id,
                    "environment": environment_id,
                },
            )
        ).all()
        if len(rows) > 1:
            # One service, several environments, and no environment label to
            # choose between them. Picking one would file the incident in
            # whichever environment sorted first.
            return CatalogBinding(str(MappingState.AMBIGUOUS), error_code="environment_ambiguous")
        if len(rows) == 1:
            environment_service_id = uuid.UUID(str(rows[0][0]))
            environment_id = environment_id or uuid.UUID(str(rows[0][1]))

    cluster_id: uuid.UUID | None = None
    cluster_ref = labels.get("cluster")
    if cluster_ref:
        found = (
            await connection.execute(
                text("SELECT id FROM clusters WHERE cluster_ref = :ref"), {"ref": cluster_ref}
            )
        ).first()
        if found is None:
            return CatalogBinding(str(MappingState.UNMAPPED), error_code="cluster_unknown")
        cluster_id = uuid.UUID(str(found[0]))

    return CatalogBinding(
        state=str(MappingState.MAPPED),
        project_id=project_id,
        environment_id=environment_id,
        service_id=service_id,
        environment_service_id=environment_service_id,
        cluster_id=cluster_id,
        namespace=labels.get("namespace"),
    )


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------


async def _record_delivery(
    connection: AsyncConnection, integration_id: uuid.UUID, delivery: NormalizedDelivery
) -> uuid.UUID | None:
    """Claim this exact payload. `None` means it was already processed."""
    row = (
        await connection.execute(
            text(
                """
                INSERT INTO alertmanager_deliveries
                    (integration_id, delivery_digest, receiver, group_key_digest, status,
                     truncated_alerts, alert_count, rejected_count, payload_version, outcome)
                VALUES (:integration, :digest, :receiver, :group, :status,
                        :truncated, :count, :rejected, :version, 'accepted')
                ON CONFLICT (integration_id, delivery_digest) DO NOTHING
                RETURNING id
                """
            ),
            {
                "integration": integration_id,
                "digest": delivery.digest,
                "receiver": delivery.receiver,
                "group": delivery.group_key_digest,
                "status": delivery.status,
                "truncated": delivery.truncated_alerts,
                "count": len(delivery.alerts) + delivery.rejected,
                "rejected": delivery.rejected,
                "version": delivery.payload_version,
            },
        )
    ).first()
    return None if row is None else uuid.UUID(str(row[0]))


async def _upsert_alert(
    connection: AsyncConnection,
    integration_id: uuid.UUID,
    alert: NormalizedAlert,
    binding: CatalogBinding,
    labels_json: str,
    annotations_json: str,
    now: datetime,
) -> tuple[dict[str, Any] | None, bool, int]:
    """Insert or advance one alert projection.

    Returns `(row, reopened, occurrence)`. A `None` row means the event was
    older than what is already recorded and changed nothing — the
    out-of-order guard doing its job.
    """
    existing = (
        await connection.execute(
            text(
                """
                SELECT id, status, occurrence, source_event_at, incident_id, version
                FROM alert_instances
                WHERE integration_id = :integration AND fingerprint = :fingerprint
                FOR UPDATE
                """
            ),
            {"integration": integration_id, "fingerprint": alert.fingerprint},
        )
    ).first()

    reopened = False
    occurrence = 1
    if existing is not None:
        if alert.source_event_at < existing[3]:
            # A replayed older event. It must not drag the projection back.
            return None, False, int(existing[2])
        occurrence = int(existing[2])
        if existing[1] == AlertStatus.RESOLVED and alert.status == AlertStatus.FIRING:
            # A new firing episode for the same alert identity.
            occurrence += 1
            reopened = True

    row = (
        await connection.execute(
            text(
                """
                INSERT INTO alert_instances
                    (integration_id, fingerprint, alert_name, status, severity, priority,
                     project_id, environment_id, service_id, environment_service_id,
                     cluster_id, namespace, owner_team, slo_key, runbook_key,
                     mapping_state, mapping_error_code, starts_at, ends_at, last_seen_at,
                     source_event_at, resolved_at, labels, annotations, occurrence)
                VALUES
                    (:integration, :fingerprint, :name, :status, :severity, :priority,
                     :project, :environment, :service, :es, :cluster, :namespace,
                     :owner_team, :slo_key, :runbook_key, :mapping_state, :mapping_error,
                     :starts_at, :ends_at, :now, :source_at, :resolved_at,
                     CAST(:labels AS jsonb), CAST(:annotations AS jsonb), :occurrence)
                ON CONFLICT (integration_id, fingerprint) DO UPDATE
                SET status = EXCLUDED.status,
                    severity = EXCLUDED.severity,
                    priority = EXCLUDED.priority,
                    alert_name = EXCLUDED.alert_name,
                    project_id = EXCLUDED.project_id,
                    environment_id = EXCLUDED.environment_id,
                    service_id = EXCLUDED.service_id,
                    environment_service_id = EXCLUDED.environment_service_id,
                    cluster_id = EXCLUDED.cluster_id,
                    namespace = EXCLUDED.namespace,
                    owner_team = EXCLUDED.owner_team,
                    slo_key = EXCLUDED.slo_key,
                    runbook_key = EXCLUDED.runbook_key,
                    mapping_state = EXCLUDED.mapping_state,
                    mapping_error_code = EXCLUDED.mapping_error_code,
                    starts_at = EXCLUDED.starts_at,
                    ends_at = EXCLUDED.ends_at,
                    last_seen_at = EXCLUDED.last_seen_at,
                    source_event_at = EXCLUDED.source_event_at,
                    resolved_at = EXCLUDED.resolved_at,
                    labels = EXCLUDED.labels,
                    annotations = EXCLUDED.annotations,
                    occurrence = EXCLUDED.occurrence,
                    version = alert_instances.version + 1,
                    updated_at = now()
                -- The out-of-order guard, in the database rather than only
                -- in the branch above: two concurrent deliveries cannot
                -- both pass an application check.
                WHERE alert_instances.source_event_at <= EXCLUDED.source_event_at
                RETURNING id, incident_id, status, occurrence
                """
            ),
            {
                "integration": integration_id,
                "fingerprint": alert.fingerprint,
                "name": alert.alert_name,
                "status": alert.status,
                "severity": alert.severity,
                "priority": alert.priority,
                "project": binding.project_id,
                "environment": binding.environment_id,
                "service": binding.service_id,
                "es": binding.environment_service_id,
                "cluster": binding.cluster_id,
                "namespace": binding.namespace,
                "owner_team": alert.labels.get("owner_team") or alert.labels.get("team"),
                "slo_key": alert.labels.get("slo_key") or alert.labels.get("slo"),
                "runbook_key": alert.labels.get("runbook"),
                "mapping_state": binding.state,
                "mapping_error": binding.error_code,
                "starts_at": alert.starts_at,
                "ends_at": alert.ends_at,
                "now": now,
                "source_at": alert.source_event_at,
                "resolved_at": (alert.ends_at if alert.status == AlertStatus.RESOLVED else None),
                "labels": labels_json,
                "annotations": annotations_json,
                "occurrence": occurrence,
            },
        )
    ).first()
    if row is None:
        return None, False, occurrence
    return (
        {"id": uuid.UUID(str(row[0])), "incident_id": row[1], "status": row[2]},
        reopened,
        occurrence,
    )


async def _record_alert_event(
    connection: AsyncConnection,
    alert_row_id: uuid.UUID,
    alert: NormalizedAlert,
    *,
    event_type: str,
    occurrence: int,
    delivery_id: uuid.UUID | None,
) -> bool:
    """Append one transition. `False` means it was already recorded.

    The unique constraint is the arbiter. An application pre-check would
    lose to a concurrent redelivery, which is exactly the case that produces
    a doubled timeline.
    """
    savepoint = await connection.begin_nested()
    try:
        await connection.execute(
            text(
                """
                INSERT INTO alert_events
                    (alert_instance_id, event_type, status, occurrence, source_event_at,
                     dedupe_key, delivery_id, detail)
                VALUES (:alert, :type, :status, :occurrence, :at, :key, :delivery,
                        CAST(:detail AS jsonb))
                """
            ),
            {
                "alert": alert_row_id,
                "type": event_type,
                "status": alert.status,
                "occurrence": occurrence,
                "at": alert.source_event_at,
                "key": alert.dedupe_key(occurrence),
                "delivery": delivery_id,
                "detail": json.dumps({"severity": alert.severity, "priority": alert.priority}),
            },
        )
    except IntegrityError:
        await savepoint.rollback()
        return False
    await savepoint.commit()
    return True


# ---------------------------------------------------------------------------
# incident correlation
# ---------------------------------------------------------------------------


async def _protection_incident(
    connection: AsyncConnection, binding: CatalogBinding
) -> uuid.UUID | None:
    """An open protection incident this alert is about the same problem as.

    An Alertmanager rule that fires on a backup being overdue and Drake's
    own protection evaluator are two witnesses to one fact. Opening a second
    incident would page the same person twice for the same problem, so the
    alert links to the existing one instead.
    """
    if binding.project_id is None:
        return None
    row = (
        await connection.execute(
            text(
                """
                SELECT id FROM incidents
                WHERE source = 'protection' AND state IN ('open', 'acknowledged')
                  AND project_id = :project
                  AND (CAST(:environment AS uuid) IS NULL OR environment_id = :environment)
                ORDER BY opened_at
                LIMIT 1
                """
            ),
            {"project": binding.project_id, "environment": binding.environment_id},
        )
    ).first()
    return None if row is None else uuid.UUID(str(row[0]))


async def _link(
    connection: AsyncConnection,
    alert_row_id: uuid.UUID,
    incident_id: uuid.UUID,
    link_type: str,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO alert_incident_links (alert_instance_id, incident_id, link_type)
            VALUES (:alert, :incident, :type)
            ON CONFLICT (alert_instance_id, incident_id) DO NOTHING
            """
        ),
        {"alert": alert_row_id, "incident": incident_id, "type": link_type},
    )


async def _incident_event(
    connection: AsyncConnection,
    incident_id: uuid.UUID,
    event_type: str,
    occurred_at: datetime,
    detail: dict[str, Any],
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO incident_events (incident_id, event_type, occurred_at, detail)
            VALUES (:incident, :type, :at, CAST(:detail AS jsonb))
            """
        ),
        {
            "incident": incident_id,
            "type": event_type,
            "at": occurred_at,
            "detail": json.dumps(detail),
        },
    )


async def _open_incident(
    connection: AsyncConnection,
    alert: NormalizedAlert,
    binding: CatalogBinding,
    key: str,
    service_key: str | None,
) -> uuid.UUID | None:
    """Open one, or discover that a concurrent delivery already did.

    The `IntegrityError` path is the partial unique index doing its job.
    Losing that race means an incident already exists for this alert, which
    is the outcome we wanted.
    """
    savepoint = await connection.begin_nested()
    try:
        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO incidents
                        (source, correlation_key, binding_id, environment_service_id,
                         project_id, environment_id, service_id, state, severity, priority,
                         owner_team, title, primary_reason, opening_reasons,
                         binding_revision, opened_at, last_critical_at)
                    VALUES ('alert', :key, NULL, :es, :project, :environment, :service,
                            'open', :severity, :priority, :owner_team, :title, :reason,
                            CAST(:reasons AS jsonb), 1, :at, :at)
                    RETURNING id
                    """
                ),
                {
                    "key": key,
                    "es": binding.environment_service_id,
                    "project": binding.project_id,
                    "environment": binding.environment_id,
                    "service": binding.service_id,
                    "severity": incident_severity(alert.priority),
                    "priority": alert.priority,
                    "owner_team": alert.labels.get("owner_team") or alert.labels.get("team"),
                    "title": incident_title(alert, service_key),
                    "reason": "alert_firing",
                    "reasons": json.dumps(["alert_firing"]),
                    "at": alert.source_event_at,
                },
            )
        ).first()
    except IntegrityError:
        await savepoint.rollback()
        return None
    await savepoint.commit()
    if row is None:
        return None
    incident_id = uuid.UUID(str(row[0]))
    await _incident_event(
        connection,
        incident_id,
        "opened",
        alert.source_event_at,
        {
            "source": "alert",
            "alert_name": alert.alert_name,
            "priority": alert.priority,
            "severity": alert.severity,
        },
    )
    return incident_id


async def _sync_incident(
    connection: AsyncConnection,
    alert: NormalizedAlert,
    binding: CatalogBinding,
    alert_row_id: uuid.UUID,
    *,
    integration_id: uuid.UUID,
    reopened: bool,
    outcome: AlertOutcome,
) -> None:
    """Bring the incident side in line with what the alert now says.

    Two decisions worth stating:

    **A resolved alert does not close its incident.** Alertmanager stopping
    is evidence the CONDITION cleared, not that the problem was handled or
    that anyone looked. The incident is marked mitigated and stays open for
    the existing lifecycle to close.

    **A reopen after closure gets a new incident, with lineage.** Sprint 6
    made a resolved incident immutable, and reviving one would rewrite a
    timeline that people have already read. The alert's link rows carry the
    lineage instead.
    """
    key = correlation_key(integration_id, alert.fingerprint)
    service_key = alert.labels.get("service")

    existing = (
        await connection.execute(
            text(
                """
                SELECT id, state FROM incidents
                WHERE correlation_key = :key AND state IN ('open', 'acknowledged')
                FOR UPDATE
                """
            ),
            {"key": key},
        )
    ).first()

    if alert.status == AlertStatus.RESOLVED:
        if existing is not None:
            incident_id = uuid.UUID(str(existing[0]))
            await connection.execute(
                text(
                    """
                    UPDATE incidents
                    SET mitigated_at = :at, updated_at = now()
                    WHERE id = :id AND mitigated_at IS NULL
                    """
                ),
                {"id": incident_id, "at": alert.source_event_at},
            )
            await _incident_event(
                connection,
                incident_id,
                "alert_resolved",
                alert.source_event_at,
                {"alert_name": alert.alert_name},
            )
            await _link(connection, alert_row_id, incident_id, "primary")
            outcome.incident_updated = incident_id
        return

    if not opens_incident(alert, binding.state):
        # Firing, but P3/P4 or unmapped. Recorded and visible; not a page.
        return

    if existing is not None:
        incident_id = uuid.UUID(str(existing[0]))
        await connection.execute(
            text(
                """
                UPDATE incidents
                SET last_critical_at = :at, mitigated_at = NULL, updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": incident_id, "at": alert.source_event_at},
        )
        if reopened:
            await _incident_event(
                connection,
                incident_id,
                "alert_reopened",
                alert.source_event_at,
                {"alert_name": alert.alert_name},
            )
            outcome.reopened = True
        await _link(connection, alert_row_id, incident_id, "primary")
        outcome.incident_updated = incident_id
        await _attach(connection, alert_row_id, incident_id)
        return

    # The same problem the protection evaluator may already have filed.
    if alert.labels.get("signal") == "protection":
        protection = await _protection_incident(connection, binding)
        if protection is not None:
            await _link(connection, alert_row_id, protection, "correlated")
            await _incident_event(
                connection,
                protection,
                "correlated",
                alert.source_event_at,
                {"source": "alert", "alert_name": alert.alert_name},
            )
            outcome.incident_correlated = protection
            await _attach(connection, alert_row_id, protection)
            return

    opened = await _open_incident(connection, alert, binding, key, service_key)
    if opened is None:
        # Lost the race; the winner's incident is the one to attach to.
        winner = (
            await connection.execute(
                text(
                    "SELECT id FROM incidents WHERE correlation_key = :key "
                    "AND state IN ('open', 'acknowledged')"
                ),
                {"key": key},
            )
        ).first()
        if winner is not None:
            opened = uuid.UUID(str(winner[0]))
            outcome.incident_updated = opened
    else:
        outcome.incident_opened = opened
        if reopened:
            # A new incident for a returning problem. The link rows below
            # keep the lineage back to the earlier one.
            await _incident_event(
                connection,
                opened,
                "alert_reopened",
                alert.source_event_at,
                {"alert_name": alert.alert_name, "previous_incident": "linked"},
            )
            outcome.reopened = True
    if opened is not None:
        await _link(connection, alert_row_id, opened, "primary")
        await _attach(connection, alert_row_id, opened)


async def _attach(
    connection: AsyncConnection, alert_row_id: uuid.UUID, incident_id: uuid.UUID
) -> None:
    await connection.execute(
        text("UPDATE alert_instances SET incident_id = :incident WHERE id = :id"),
        {"id": alert_row_id, "incident": incident_id},
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


async def apply_delivery(
    engine: AsyncEngine,
    *,
    integration_id: uuid.UUID,
    project_id: uuid.UUID,
    project_key: str,
    delivery: NormalizedDelivery,
    now: datetime | None = None,
) -> DeliveryOutcome:
    """Project one authenticated Alertmanager notification.

    Everything commits together: an incident is never created without its
    opening event, and an alert event never points at an alert that was not
    written.
    """
    moment = now or datetime.now(UTC)
    outcome = DeliveryOutcome()

    async with engine.begin() as connection:
        delivery_id = await _record_delivery(connection, integration_id, delivery)
        if delivery_id is None:
            # This exact payload has already been processed. Alertmanager
            # retries routinely; a retry is not new evidence of anything.
            return DeliveryOutcome(duplicate=True, rejected=delivery.rejected)
        outcome.delivery_id = delivery_id

        for alert in delivery.alerts:
            binding = await resolve_catalog(connection, project_id, project_key, alert.labels)
            alert_outcome = AlertOutcome(fingerprint=alert.fingerprint, mapping_state=binding.state)
            row, reopened, occurrence = await _upsert_alert(
                connection,
                integration_id,
                alert,
                binding,
                json.dumps(alert.labels, sort_keys=True),
                json.dumps(alert.annotations, sort_keys=True),
                moment,
            )
            if row is None:
                alert_outcome.stale = True
                outcome.alerts.append(alert_outcome)
                continue

            event_type = (
                "reopened"
                if reopened
                else ("resolved" if alert.status == AlertStatus.RESOLVED else "firing")
            )
            alert_outcome.event_recorded = await _record_alert_event(
                connection,
                row["id"],
                alert,
                event_type=event_type,
                occurrence=occurrence,
                delivery_id=delivery_id,
            )
            if alert_outcome.event_recorded:
                await _sync_incident(
                    connection,
                    alert,
                    binding,
                    row["id"],
                    integration_id=integration_id,
                    reopened=reopened,
                    outcome=alert_outcome,
                )
            outcome.accepted += 1
            if binding.state != MappingState.MAPPED:
                outcome.unmapped += 1
            outcome.alerts.append(alert_outcome)

        outcome.rejected = delivery.rejected
        await connection.execute(
            text(
                """
                UPDATE alertmanager_deliveries
                SET accepted_count = :accepted, unmapped_count = :unmapped,
                    outcome = :outcome
                WHERE id = :id
                """
            ),
            {
                "id": delivery_id,
                "accepted": outcome.accepted,
                "unmapped": outcome.unmapped,
                "outcome": "partial" if (outcome.rejected or outcome.unmapped) else "accepted",
            },
        )
    return outcome


async def integration_seen_at(
    connection: AsyncConnection, integration_id: uuid.UUID
) -> datetime | None:
    """When this Alertmanager last delivered anything Drake accepted."""
    return (
        await connection.execute(
            text(
                "SELECT max(received_at) FROM alertmanager_deliveries "
                "WHERE integration_id = :id AND outcome <> 'rejected'"
            ),
            {"id": integration_id},
        )
    ).scalar_one_or_none()


__all__ = [
    "INTEGRATION_TYPE",
    "AlertOutcome",
    "CatalogBinding",
    "DeliveryOutcome",
    "apply_delivery",
    "ensure_integration_row",
    "integration_seen_at",
    "load_token",
    "protection_correlation_key",
    "resolve_catalog",
    "resolve_integration",
    "verify_bearer",
]
