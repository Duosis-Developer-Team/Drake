"""What a notification says, and which events may produce one.

Every string a recipient sees is composed here, from reason codes and
catalog identifiers. There is no endpoint that accepts a title, a body, or
a message template, so the whole vocabulary of Drake's notifications is
the dictionary below — which means it can be reviewed, and cannot be used
to deliver someone else's text to someone else's inbox.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from drake_api.incidents.model import INCIDENT_TITLES
from drake_api.service_health.policy import REASON_TEXT, ReasonCode

# The incident events a policy may subscribe to. `recovery_started` and
# `recovery_interrupted` are internal progress markers: notifying on them
# would tell someone twice about one recovery, so they are excluded here
# and in the database.
NOTIFIABLE_EVENTS: tuple[str, ...] = ("opened", "acknowledged", "auto_resolved")

# `opened` and `auto_resolved` are the default subscription because they
# are the two facts an operator needs. `acknowledged` is available but
# opt-in: on a large team it is mostly noise.
DEFAULT_EVENT_TYPES: tuple[str, ...] = ("opened", "auto_resolved")

# `high` joins the vocabulary with Sprint 10's P2 incidents. Existing
# policies are untouched — widening what may be subscribed to is not the
# same as widening a subscription someone already wrote.
SEVERITIES: tuple[str, ...] = ("critical", "high")

DESTINATION_TYPES: tuple[str, ...] = ("in_app_user", "webhook")

PAYLOAD_SCHEMA_VERSION = 1
PLANNER_VERSION = 1

# One sentence per event. The recipient reads this, so it says what
# happened and what it means — not which table changed.
_EVENT_HEADLINE: dict[str, str] = {
    "opened": "Incident opened",
    "acknowledged": "Incident acknowledged",
    "auto_resolved": "Incident resolved",
}

_EVENT_SENTENCE: dict[str, str] = {
    "opened": (
        "Drake opened an incident after two consecutive trustworthy critical "
        "evaluations."
    ),
    "acknowledged": "A responder acknowledged this incident. Monitoring continues.",
    "auto_resolved": (
        "The service reported healthy twice in a row, so Drake resolved this "
        "incident automatically."
    ),
}


@dataclass(frozen=True)
class NotifiableEvent:
    """One committed incident event, with the context a message needs."""

    incident_event_id: uuid.UUID
    incident_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    #: When Drake WROTE the event down — immutable, and the basis for
    #: deciding which policy revision applies. `occurred_at` carries the
    #: evaluation's own timestamp, which a replayed reading can backdate.
    recorded_at: datetime
    incident_state: str
    severity: str
    title: str
    primary_reason: str
    opened_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    project_id: uuid.UUID
    environment_id: uuid.UUID
    service_id: uuid.UUID
    project_key: str
    environment_key: str
    service_key: str
    #: Absent for an incident that is genuinely not about one workload —
    #: a protection policy or a project-level alert.
    binding_id: uuid.UUID | None
    namespace: str
    workload_name: str
    cluster_ref: str

    def headline(self) -> str:
        return f"{_EVENT_HEADLINE.get(self.event_type, 'Incident update')}: {self.title}"[:200]

    def sentence(self) -> str:
        reason = INCIDENT_TITLES.get(self.primary_reason) or REASON_TEXT.get(
            ReasonCode(self.primary_reason) if _is_reason(self.primary_reason) else None,  # type: ignore[arg-type]
            "",
        )
        where = f"{self.project_key}/{self.environment_key}/{self.service_key}"
        body = _EVENT_SENTENCE.get(self.event_type, "This incident changed state.")
        return f"{where} — {reason}. {body}"[:1000]

    def target_path(self) -> str:
        """A relative in-app path, frozen when the notification is created."""
        return f"/incidents/{self.incident_id}"


def _is_reason(value: str) -> bool:
    try:
        ReasonCode(value)
    except ValueError:
        return False
    return True


def in_app_metadata(event: NotifiableEvent) -> dict[str, Any]:
    """The bounded snapshot an inbox row carries.

    Identifiers and codes only. No metric values, no labels, and nothing
    that would let the inbox become a second, unauthorized copy of data the
    recipient may no longer be allowed to see.
    """
    return {
        "event_type": event.event_type,
        "severity": event.severity,
        "primary_reason": event.primary_reason,
        "project_key": event.project_key,
        "environment_key": event.environment_key,
        "service_key": event.service_key,
        "incident_state": event.incident_state,
    }


def webhook_payload(
    event: NotifiableEvent, *, delivery_id: uuid.UUID, idempotency_key: str, base_url: str
) -> dict[str, Any]:
    """The body Drake sends. Server-composed, and deliberately small.

    What is NOT here is the point: no PromQL, no query response, no metric
    labels, no credential, no internal exception, no inventory payload and
    no session material. A receiver learns that an incident happened, to
    which service, and where to look — nothing that would make the webhook
    a data-exfiltration path.
    """
    link = f"{base_url.rstrip('/')}{event.target_path()}" if base_url else event.target_path()
    return {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "delivery_id": str(delivery_id),
        "idempotency_key": idempotency_key,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "incident": {
            "id": str(event.incident_id),
            "state": event.incident_state,
            "severity": event.severity,
            "title": event.title,
            "primary_reason": event.primary_reason,
            "opened_at": event.opened_at.isoformat(),
            "acknowledged_at": (
                event.acknowledged_at.isoformat() if event.acknowledged_at else None
            ),
            "resolved_at": event.resolved_at.isoformat() if event.resolved_at else None,
            "url": link,
        },
        "service": {
            "project_key": event.project_key,
            "environment_key": event.environment_key,
            "service_key": event.service_key,
            "cluster_ref": event.cluster_ref,
            "namespace": event.namespace,
            "workload_name": event.workload_name,
        },
    }


def idempotency_key(event_id: uuid.UUID, channel: str, canonical_target: str) -> str:
    """Stable across retries, planner runs, and duplicate destination rows.

    Derived from the event, the channel and the CANONICAL target — the
    runtime destination key, not the database row that referenced it. Two
    destination rows pointing at one operator endpoint therefore produce
    one key, which is what makes the guarantee the receiver experiences
    ("one call per event") true rather than approximately true.
    """
    material = f"{event_id}:{channel}:{canonical_target}"
    return hashlib.sha256(material.encode()).hexdigest()[:48]
