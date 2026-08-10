"""What an Alertmanager notification means, decided in one place.

Pure and deterministic: payload in, typed facts out. No I/O, no clock
beyond the one it is handed, and no database.

The responsibility line this module keeps:

    PrometheusRule = decides when a condition is true
    Alertmanager   = grouping, dedupe, inhibition, silence, base notification
    Drake          = business context, ownership, timeline, controlled ops

So nothing here re-evaluates a condition, re-groups anything, or decides
whether a receiver should have been called. It reads what Alertmanager
already decided and gives it a shape Drake's incident, notification and
catalog layers can use.

Three rules do most of the work:

**A group is not an identity.** Alertmanager's `groupKey` names a
notification batch whose membership changes between deliveries. Keying an
incident on it would merge unrelated services and split related ones, so
identity is `(integration, fingerprint)` and nothing else.

**Every alert in a group is normalized separately.** The group's `status`
is a summary of the batch, and a batch marked `resolved` can still contain
a firing alert. Letting the group status win would close an incident for a
service that is still down.

**Not every warning is a page.** Severity maps to a priority, and only P1
and P2 open an incident. An unknown severity does not silently become P1:
it becomes P3 with a stated reason, because guessing upward pages someone
for a label typo and guessing downward hides an outage.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

# --- bounded ingest ---------------------------------------------------------

# The only labels Drake keeps. Everything else is dropped at the boundary:
# an alert label is attacker-influenceable (anyone who can write a recording
# rule can set one), and a store that accepts arbitrary keys becomes a place
# where a request id, an email or a URL quietly lands.
ALLOWED_LABELS: frozenset[str] = frozenset(
    {
        "alertname",
        "severity",
        "project",
        "environment",
        "service",
        "cluster",
        "namespace",
        "team",
        "owner_team",
        "slo",
        "slo_key",
        "runbook",
        "component",
        "signal",
        "tenant_key",
        "long_window",
        "short_window",
        "burn_level",
    }
)

# Annotations are prose written by whoever authored the rule. Two short,
# reviewed fields are kept; `runbook_url`, `dashboard_url` and friends are
# deliberately absent — see `SAFE_ANNOTATION_MAX` and the URL rejection
# below.
ALLOWED_ANNOTATIONS: frozenset[str] = frozenset({"summary", "description", "impact"})

# Labels that must never be accepted even if someone adds them to a rule.
# The list mirrors the telemetry registry's denylist for the same reason:
# these are the fields that turn an observability store into a personal
# data store.
FORBIDDEN_LABELS: frozenset[str] = frozenset(
    {
        "email",
        "user",
        "user_id",
        "username",
        "full_name",
        "display_name",
        "tenant_name",
        "customer",
        "request_id",
        "trace_id",
        "span_id",
        "url",
        "raw_path",
        "path",
        "query",
        "query_string",
        "sql",
        "ip",
        "client_ip",
        "error_message",
        "instance",
        "pod",
        "generatorURL",
        "externalURL",
    }
)

MAX_LABELS = 24
MAX_LABEL_VALUE = 128
MAX_ANNOTATIONS = 3
SAFE_ANNOTATION_MAX = 400
MAX_ALERTS_PER_DELIVERY = 200

# A value carrying a scheme is a link, whatever field it arrived in. Drake
# composes its own links from server-controlled references, so an inbound
# one is never needed and never safe.
_URL_LIKE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://")
_KEY_SHAPE = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,127}$")


class AlertStatus(StrEnum):
    FIRING = "firing"
    RESOLVED = "resolved"


class MappingState(StrEnum):
    MAPPED = "mapped"
    UNMAPPED = "unmapped"
    AMBIGUOUS = "ambiguous"


class Priority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


# Severity → priority. A named, reviewed table rather than a guess at call
# sites, so "what does high mean" has exactly one answer.
SEVERITY_PRIORITY: dict[str, Priority] = {
    "critical": Priority.P1,
    "high": Priority.P2,
    "medium": Priority.P3,
    "info": Priority.P4,
}

# What an unrecognised severity becomes. NOT P1: a label typo must not page
# someone at 3am, and it must not vanish either.
UNKNOWN_SEVERITY_PRIORITY = Priority.P3

# Only these open an incident. A P3 is recorded, shown, and filterable; it
# is not a reason to wake anyone.
INCIDENT_PRIORITIES: frozenset[str] = frozenset({Priority.P1, Priority.P2})

# Incident severity carried alongside the priority, so existing
# notification policies keep routing on a vocabulary they already know.
PRIORITY_SEVERITY: dict[str, str] = {
    Priority.P1: "critical",
    Priority.P2: "high",
    Priority.P3: "high",
    Priority.P4: "high",
}


class IngestRejectedError(ValueError):
    """A bounded rejection code — never a provider message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NormalizedAlert:
    """One alert from one delivery, reduced to what Drake may store."""

    fingerprint: str
    alert_name: str
    status: str
    severity: str
    priority: str
    starts_at: datetime
    ends_at: datetime | None
    labels: dict[str, str]
    annotations: dict[str, str]
    severity_recognised: bool = True

    @property
    def source_event_at(self) -> datetime:
        """When the provider says this state began or ended.

        For a resolved alert that is `endsAt`; for a firing one `startsAt`.
        Drake's own receipt time is recorded separately, so a late delivery
        is visible as late rather than as a late outage.
        """
        if self.status == AlertStatus.RESOLVED and self.ends_at is not None:
            return self.ends_at
        return self.starts_at

    def dedupe_key(self, occurrence: int) -> str:
        """Stable across retries, distinct across episodes.

        Derived from the immutable facts of one alert STATE — never from the
        delivery, the group, or the moment Drake received it, any of which
        would change on a retry and turn one transition into several.
        `occurrence` separates a genuine reopen from a replay of the first
        firing, which share a `startsAt` only when Alertmanager reuses it.
        """
        material = (
            f"{self.fingerprint}:{self.status}:{occurrence}:"
            f"{self.starts_at.isoformat()}:{self.ends_at.isoformat() if self.ends_at else ''}"
        )
        return hashlib.sha256(material.encode()).hexdigest()[:64]


@dataclass(frozen=True)
class NormalizedDelivery:
    """One Alertmanager webhook call, normalized and bounded."""

    digest: str
    receiver: str | None
    group_key_digest: str | None
    status: str
    truncated_alerts: int
    payload_version: int | None
    alerts: list[NormalizedAlert] = field(default_factory=list)
    rejected: int = 0


def _clean_value(value: Any, limit: int) -> str | None:
    """A bounded scalar, or nothing. A link is never a value."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit:
        return None
    if _URL_LIKE.search(text):
        # `generatorURL` in an annotation, a dashboard link, a
        # webhook — whatever it is, Drake does not store or render it.
        return None
    return text


def safe_labels(raw: Any) -> dict[str, str]:
    """Allowlisted, bounded labels. Everything else is dropped silently.

    Silently on purpose: a rule author adding a label should not be able to
    make ingest fail, and the alert itself still carries real information.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in sorted(raw):
        if len(out) >= MAX_LABELS:
            break
        if key not in ALLOWED_LABELS or key in FORBIDDEN_LABELS:
            continue
        value = _clean_value(raw[key], MAX_LABEL_VALUE)
        if value is not None:
            out[key] = value
    return out


def safe_annotations(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in sorted(raw):
        if len(out) >= MAX_ANNOTATIONS:
            break
        if key not in ALLOWED_ANNOTATIONS:
            continue
        value = _clean_value(raw[key], SAFE_ANNOTATION_MAX)
        if value is not None:
            out[key] = value
    return out


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # An ambiguous alert time is worse than a missing one.
        return None
    # Alertmanager writes the zero time for an alert that has not ended.
    if parsed.year <= 1:
        return None
    return parsed


def _fingerprint(raw: Any, labels: dict[str, str], alert_name: str) -> str:
    """The provider's fingerprint, or a deterministic one from the labels.

    Alertmanager always sends `fingerprint`; the fallback exists so a
    slightly older version does not silently produce alerts that all share
    one identity — which is the failure mode that would merge every alert
    in an estate into a single incident.
    """
    candidate = raw if isinstance(raw, str) else ""
    candidate = candidate.strip().lower()
    if candidate and re.fullmatch(r"[0-9a-f]{8,64}", candidate):
        return candidate
    material = alert_name + "|" + "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return hashlib.sha256(material.encode()).hexdigest()[:40]


def normalize_alert(entry: Any) -> NormalizedAlert:
    """One alert from the `alerts[]` array. Raises on anything unusable."""
    if not isinstance(entry, dict):
        raise IngestRejectedError("alert_malformed")

    labels = safe_labels(entry.get("labels"))
    alert_name = labels.get("alertname") or _clean_value(entry.get("alertname"), 200) or ""
    if not alert_name:
        raise IngestRejectedError("alert_name_missing")

    status = str(entry.get("status") or "").strip().lower()
    if status not in (AlertStatus.FIRING, AlertStatus.RESOLVED):
        raise IngestRejectedError("alert_status_unknown")

    starts_at = _timestamp(entry.get("startsAt"))
    if starts_at is None:
        raise IngestRejectedError("alert_starts_at_invalid")
    ends_at = _timestamp(entry.get("endsAt"))
    if status == AlertStatus.RESOLVED and ends_at is None:
        # A resolved alert with no end time cannot be placed on a timeline,
        # and inventing one would make the history a fiction.
        raise IngestRejectedError("alert_ends_at_invalid")

    raw_severity = (labels.get("severity") or "").strip().lower()
    recognised = raw_severity in SEVERITY_PRIORITY
    severity = raw_severity if recognised else "unknown"
    priority = SEVERITY_PRIORITY.get(raw_severity, UNKNOWN_SEVERITY_PRIORITY)

    return NormalizedAlert(
        fingerprint=_fingerprint(entry.get("fingerprint"), labels, alert_name),
        alert_name=alert_name[:200],
        status=status,
        severity=severity,
        priority=str(priority),
        starts_at=starts_at,
        ends_at=ends_at,
        labels=labels,
        annotations=safe_annotations(entry.get("annotations")),
        severity_recognised=recognised,
    )


def normalize_delivery(payload: Any, body: bytes) -> NormalizedDelivery:
    """The whole webhook body, bounded and typed.

    A malformed alert costs its own alert and nothing else: the rest of the
    batch is still recorded, because dropping nineteen good alerts because
    the twentieth had a bad timestamp is not safety.
    """
    if not isinstance(payload, dict):
        raise IngestRejectedError("payload_malformed")

    raw_alerts = payload.get("alerts")
    if not isinstance(raw_alerts, list) or not raw_alerts:
        raise IngestRejectedError("payload_no_alerts")
    if len(raw_alerts) > MAX_ALERTS_PER_DELIVERY:
        raise IngestRejectedError("payload_too_many_alerts")

    alerts: list[NormalizedAlert] = []
    rejected = 0
    for entry in raw_alerts:
        try:
            alerts.append(normalize_alert(entry))
        except IngestRejectedError:
            rejected += 1
    if not alerts:
        raise IngestRejectedError("payload_no_usable_alerts")

    group_key = payload.get("groupKey")
    truncated = payload.get("truncatedAlerts")
    version = payload.get("version")

    return NormalizedDelivery(
        # A digest of the exact bytes. Recognising a retried notification
        # without keeping the body it carried.
        digest=hashlib.sha256(body).hexdigest()[:64],
        receiver=_clean_value(payload.get("receiver"), 128),
        # Hashed, not stored: a groupKey embeds the grouping label VALUES,
        # which are exactly the strings this module refuses to keep.
        group_key_digest=(
            hashlib.sha256(str(group_key).encode()).hexdigest()[:32]
            if isinstance(group_key, str) and group_key
            else None
        ),
        # The group's own status. Recorded for diagnostics, never applied to
        # an individual alert — see the module docstring.
        status=str(payload.get("status") or "").strip().lower() or "unknown",
        truncated_alerts=int(truncated) if isinstance(truncated, int) and truncated > 0 else 0,
        payload_version=(
            int(version)
            if isinstance(version, int)
            else (int(version) if isinstance(version, str) and version.isdigit() else None)
        ),
        alerts=alerts,
        rejected=rejected,
    )


# --- incident policy --------------------------------------------------------


def opens_incident(alert: NormalizedAlert, mapping_state: str) -> bool:
    """Whether this alert may open an incident.

    Two conditions, both necessary. It must be firing at P1 or P2 — every
    warning is not a page. And it must be MAPPED: an alert Drake could not
    place in the catalog has no project to file an incident against, and
    filing it against a guess is worse than filing nothing.
    """
    if alert.status != AlertStatus.FIRING:
        return False
    if mapping_state != MappingState.MAPPED:
        return False
    return alert.priority in INCIDENT_PRIORITIES


def incident_severity(priority: str) -> str:
    return PRIORITY_SEVERITY.get(priority, "high")


def correlation_key(integration_id: Any, fingerprint: str) -> str:
    """The dedup identity of an alert-sourced incident.

    Per integration and per fingerprint, so two Alertmanagers reporting the
    same rule stay separate, and a repeated firing for one alert updates its
    incident instead of opening another.
    """
    return f"alert:{integration_id}:{fingerprint}"[:128]


def protection_correlation_key(policy_id: Any) -> str:
    """The dedup identity a protection problem already uses.

    An Alertmanager rule that fires on the same protection problem adopts
    this key rather than minting its own, so the alert path and the
    protection evaluator link to one incident instead of opening two for the
    same fact.
    """
    return f"protection:{policy_id}"[:128]


def incident_title(alert: NormalizedAlert, service_key: str | None) -> str:
    """A name composed from server-controlled parts only.

    The alert name comes from a PrometheusRule, which is reviewed code in
    someone's repository, and it is bounded and stripped of links above.
    No free text from a request reaches this.
    """
    where = service_key or alert.labels.get("service") or alert.labels.get("environment") or ""
    prefix = f"{where}: " if where else ""
    return f"{prefix}{alert.alert_name}"[:200]


def is_key(value: str | None) -> bool:
    return bool(value) and bool(_KEY_SHAPE.fullmatch(value or ""))
