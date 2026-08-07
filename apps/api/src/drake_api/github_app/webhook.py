"""Webhook trust boundary (ADR-0019 §4/§5).

The endpoint carries no session and no CSRF token. Trust comes from one
place: an HMAC-SHA256 over the RAW request bytes, compared in constant
time. Nothing downstream — no JSON parse, no queue write, no domain
mutation — may run before that comparison succeeds.
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

SIGNATURE_HEADER = "X-Hub-Signature-256"
DELIVERY_HEADER = "X-GitHub-Delivery"
EVENT_HEADER = "X-GitHub-Event"
SIGNATURE_PREFIX = "sha256="

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

    def as_json(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "action": self.action,
            "installation_external_id": self.installation_external_id,
            "account_login": self.account_login,
            "repositories": [dict(item) for item in self.repositories],
        }


_MAX_ENVELOPE_REPOSITORIES = 100


def _bounded_text(value: Any, limit: int = 255) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _repository_summary(item: Any) -> dict[str, Any] | None:
    """Keep only identity-bearing repository fields, all bounded."""
    if not isinstance(item, dict):
        return None
    external_id = item.get("id")
    if not isinstance(external_id, int):
        return None
    full_name = _bounded_text(item.get("full_name"))
    name = _bounded_text(item.get("name")) or full_name.split("/")[-1]
    owner = full_name.split("/")[0] if "/" in full_name else ""
    return {
        "external_id": external_id,
        "node_id": _bounded_text(item.get("node_id"), 128),
        "name": name,
        "full_name": full_name,
        "owner_login": owner,
        "private": bool(item.get("private", True)),
    }


def build_envelope(event: str, payload: dict[str, Any]) -> WebhookEnvelope:
    """Extract the bounded envelope. Unknown/oversized content is dropped."""
    installation = payload.get("installation")
    installation_id = None
    account_login = ""
    if isinstance(installation, dict):
        candidate = installation.get("id")
        installation_id = candidate if isinstance(candidate, int) else None
        account = installation.get("account")
        if isinstance(account, dict):
            account_login = _bounded_text(account.get("login"))

    repositories: list[dict[str, Any]] = []
    for key in ("repositories", "repositories_added", "repositories_removed"):
        entries = payload.get(key)
        if not isinstance(entries, list):
            continue
        for item in entries[:_MAX_ENVELOPE_REPOSITORIES]:
            summary = _repository_summary(item)
            if summary is not None:
                summary["membership"] = "removed" if key.endswith("removed") else "present"
                repositories.append(summary)
    single = _repository_summary(payload.get("repository"))
    if single is not None:
        single["membership"] = "present"
        repositories.append(single)

    return WebhookEnvelope(
        event=event,
        action=_bounded_text(payload.get("action"), 64),
        installation_external_id=installation_id,
        account_login=account_login,
        repositories=tuple(repositories[:_MAX_ENVELOPE_REPOSITORIES]),
    )
