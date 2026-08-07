"""Webhook trust boundary (ADR-0019 §4/§5).

The endpoint carries no session and no CSRF token. Trust comes from one
place: an HMAC-SHA256 over the RAW request bytes, compared in constant
time. Nothing downstream — no JSON parse, no queue write, no domain
mutation — may run before that comparison succeeds.
"""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from drake_api.github_app.catalog import ORGANIZATION

SIGNATURE_HEADER = "X-Hub-Signature-256"
DELIVERY_HEADER = "X-GitHub-Delivery"
EVENT_HEADER = "X-GitHub-Event"
SIGNATURE_PREFIX = "sha256="

# The one organization this deployment accepts deliveries for.
EXPECTED_ORGANIZATION = ORGANIZATION

# Only these lifecycle events are processed. Adding one requires a
# consumer and a permission-matrix update (ADR-0019 §6).
SUPPORTED_EVENTS = frozenset({"installation", "installation_repositories", "repository"})
# `ping` is answered but carries no domain work.
ACKNOWLEDGED_EVENTS = frozenset({"ping"})

# GitHub delivery ids are UUIDs; accept that shape only.
_DELIVERY_ID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class WebhookRejectedError(Exception):
    """A webhook refusal. `reason` is a bounded, secret-free code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def payload_digest(raw_body: bytes) -> str:
    """Stable digest of the raw body — the replay identity of a delivery."""
    return hashlib.sha256(raw_body).hexdigest()


def verify_signature(raw_body: bytes, secret: str, provided: str | None) -> None:
    """Constant-time HMAC-SHA256 verification over the RAW bytes.

    Every failure raises the same shaped refusal so the endpoint reveals
    nothing about which part was wrong.
    """
    if not secret:
        raise WebhookRejectedError("webhook_secret_missing")
    if not provided or not provided.startswith(SIGNATURE_PREFIX):
        raise WebhookRejectedError("signature_malformed")
    supplied = provided[len(SIGNATURE_PREFIX) :]
    if len(supplied) != 64 or not all(c in "0123456789abcdefABCDEF" for c in supplied):
        raise WebhookRejectedError("signature_malformed")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # compare_digest is the constant-time path; a plain == would leak
    # timing information about the correct prefix length.
    if not hmac.compare_digest(expected, supplied.lower()):
        raise WebhookRejectedError("signature_mismatch")


def validate_delivery_id(value: str | None) -> str:
    if not value or not _DELIVERY_ID.match(value):
        raise WebhookRejectedError("delivery_id_invalid")
    return value


def validate_event_name(value: str | None) -> str:
    if not value or not _EVENT_NAME.match(value):
        raise WebhookRejectedError("event_invalid")
    if value not in SUPPORTED_EVENTS and value not in ACKNOWLEDGED_EVENTS:
        raise WebhookRejectedError("event_unsupported")
    return value


@dataclass(frozen=True)
class WebhookEnvelope:
    """The bounded, explicitly chosen fields Drake keeps from a payload.

    The raw payload is never stored: this envelope plus the digest is the
    entire persistent record of a delivery.
    """

    event: str
    action: str
    installation_external_id: int | None
    account_login: str
    repositories: tuple[dict[str, Any], ...]
    # How many repositories the payload actually carried. When this exceeds
    # `len(repositories)` the list was cut to fit the persisted byte budget.
    observed_repository_count: int = 0
    truncated: bool = False

    @property
    def reconciliation_required(self) -> bool:
        """A truncated list is not a complete statement of membership.

        Silently storing the first N would look like the whole truth to
        every later reader, so the envelope says outright that the full
        set has to come from an installation-level reconciliation.
        """
        return self.truncated

    def as_json(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "action": self.action,
            "installation_external_id": self.installation_external_id,
            "account_login": self.account_login,
            "repositories": [dict(item) for item in self.repositories],
            "observed_repository_count": self.observed_repository_count,
            "truncated": self.truncated,
            "reconciliation_required": self.reconciliation_required,
        }


_MAX_ENVELOPE_REPOSITORIES = 100
# The persisted envelope must fit the `pg_column_size(envelope) <= 8192`
# constraint on github_webhook_deliveries. jsonb storage is close to, but
# not identical with, the serialized JSON length, so the application budget
# sits below the database ceiling and an integration test measures the real
# `pg_column_size` of the largest envelope this builder can produce.
ENVELOPE_BYTE_BUDGET = 6144


def _bounded_text(value: Any, limit: int = 255) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _repository_summary(item: Any) -> dict[str, Any] | None:
    """Keep only identity-bearing repository fields, all bounded.

    `name` and `owner_login` are derived from `full_name` rather than
    stored: two extra bounded strings per entry is most of the envelope
    budget, and neither carries information `full_name` does not.
    """
    if not isinstance(item, dict):
        return None
    external_id = item.get("id")
    if not isinstance(external_id, int) or isinstance(external_id, bool):
        return None
    full_name = _bounded_text(item.get("full_name"))
    if not full_name:
        # An entry with no full name has no owner we can verify, so it
        # cannot be checked against the expected organization.
        return None
    summary: dict[str, Any] = {
        "external_id": external_id,
        "node_id": _bounded_text(item.get("node_id"), 128),
        "full_name": full_name,
    }
    # Only recorded when the payload actually said so. Defaulting it here
    # would make "the message did not mention visibility" indistinguishable
    # from "the repository is private", and the difference matters when the
    # value is about to be written over what we already know.
    if isinstance(item.get("private"), bool):
        summary["private"] = bool(item["private"])
    return summary


def summary_name(summary: dict[str, Any]) -> str:
    full_name = str(summary.get("full_name") or "")
    return full_name.split("/")[-1]


def summary_owner(summary: dict[str, Any]) -> str:
    full_name = str(summary.get("full_name") or "")
    return full_name.split("/")[0] if "/" in full_name else ""


def build_envelope(event: str, payload: dict[str, Any]) -> WebhookEnvelope:
    """Extract the bounded envelope. Unknown/oversized content is dropped.

    The repository list is fitted to `ENVELOPE_BYTE_BUDGET` so what the
    application produces always fits what the database accepts. A large but
    entirely legitimate installation webhook must not become a 500, and it
    must not quietly persist a partial list as though it were complete —
    so an over-budget payload is recorded as truncated, with the observed
    count, which flags it for installation-level reconciliation.
    """
    installation = payload.get("installation")
    installation_id = None
    account_login = ""
    if isinstance(installation, dict):
        candidate = installation.get("id")
        installation_id = (
            candidate if isinstance(candidate, int) and not isinstance(candidate, bool) else None
        )
        account = installation.get("account")
        if isinstance(account, dict):
            account_login = _bounded_text(account.get("login"))

    observed = 0
    candidates: list[dict[str, Any]] = []
    for key in ("repositories", "repositories_added", "repositories_removed"):
        entries = payload.get(key)
        if not isinstance(entries, list):
            continue
        observed += len(entries)
        for item in entries[:_MAX_ENVELOPE_REPOSITORIES]:
            summary = _repository_summary(item)
            if summary is not None:
                summary["membership"] = "removed" if key.endswith("removed") else "present"
                candidates.append(summary)
    single = _repository_summary(payload.get("repository"))
    if single is not None:
        observed += 1
        single["membership"] = "present"
        candidates.append(single)

    kept, truncated = _fit_to_budget(candidates)
    if len(kept) < min(len(candidates), _MAX_ENVELOPE_REPOSITORIES):
        truncated = True
    if observed > len(kept):
        truncated = True

    return WebhookEnvelope(
        event=event,
        action=_bounded_text(payload.get("action"), 64),
        installation_external_id=installation_id,
        account_login=account_login,
        repositories=tuple(kept),
        observed_repository_count=observed,
        truncated=truncated,
    )


def _fit_to_budget(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Keep as many repository entries as the byte budget allows."""
    kept: list[dict[str, Any]] = []
    # Everything except the repository list: event, action, account, counters.
    used = 320
    for summary in candidates[:_MAX_ENVELOPE_REPOSITORIES]:
        entry_size = len(json.dumps(summary, separators=(",", ":")).encode("utf-8")) + 1
        if used + entry_size > ENVELOPE_BYTE_BUDGET:
            return kept, True
        used += entry_size
        kept.append(summary)
    return kept, False


def check_ownership(envelope: WebhookEnvelope) -> None:
    """Fail-closed installation and owner identity (ADR-0019 §4 step 7).

    Absent evidence is a refusal, not a pass. An earlier version only
    compared the account login *when one was present*, so a payload that
    simply omitted it sailed through the ownership check entirely — the
    one case where the check matters most.
    """
    if envelope.installation_external_id is None:
        raise WebhookRejectedError("installation_missing")
    if not envelope.account_login:
        raise WebhookRejectedError("account_missing")
    if envelope.account_login.lower() != EXPECTED_ORGANIZATION.lower():
        raise WebhookRejectedError("owner_mismatch")

    for summary in envelope.repositories:
        if not summary_owner(summary):
            raise WebhookRejectedError("repository_owner_missing")


def foreign_repositories(envelope: WebhookEnvelope) -> list[int]:
    """Announced repositories whose owner is not the expected organization.

    A foreign owner means two different things. For a repository Drake
    already tracks by permanent id it is evidence the repository LEFT —
    refusing the delivery would strand it as accessible with stale
    metadata, which is the worst of both worlds. For a repository we have
    never seen it is an attempt to onboard something that is not ours, and
    that is refused. The caller resolves which case applies, because only
    it can consult what we already know.
    """
    expected = EXPECTED_ORGANIZATION.lower()
    return [
        int(summary["external_id"])
        for summary in envelope.repositories
        if summary_owner(summary).lower() != expected
    ]
