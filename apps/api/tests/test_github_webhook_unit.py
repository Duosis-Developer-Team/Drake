"""Webhook signature and replay-identity unit tests.

The first test is GitHub's own published example, so a regression in the
HMAC path is caught against the vendor's vector rather than our own.
"""

import hashlib
import hmac

import pytest
from drake_api.github_app.webhook import (
    SUPPORTED_EVENTS,
    WebhookRejectedError,
    build_envelope,
    payload_digest,
    validate_delivery_id,
    validate_event_name,
    verify_signature,
)

# GitHub's documented validation example (docs: "Validating webhook
# deliveries"). Neither value is a real credential.
OFFICIAL_SECRET = "It's a Secret to Everybody"
OFFICIAL_PAYLOAD = b"Hello, World!"
OFFICIAL_SIGNATURE = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"

VALID_DELIVERY = "72d3162e-cc78-11e3-81ab-4c9367dc0958"


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_official_github_test_vector() -> None:
    verify_signature(OFFICIAL_PAYLOAD, OFFICIAL_SECRET, OFFICIAL_SIGNATURE)


def test_unicode_body_is_hashed_over_raw_bytes() -> None:
    """A UTF-8 body must hash over the bytes GitHub actually sent."""
    body = '{"repository":{"name":"crème-brûlée","emoji":"🍓"}}'.encode()
    signature = _sign(OFFICIAL_SECRET, body)
    verify_signature(body, OFFICIAL_SECRET, signature)
    # Re-encoding through a different normalization must NOT verify.
    with pytest.raises(WebhookRejectedError):
        verify_signature(body.decode().replace("🍓", "x").encode(), OFFICIAL_SECRET, signature)


def test_body_mutation_breaks_the_signature() -> None:
    body = b'{"action":"created"}'
    signature = _sign(OFFICIAL_SECRET, body)
    verify_signature(body, OFFICIAL_SECRET, signature)
    with pytest.raises(WebhookRejectedError) as refusal:
        verify_signature(b'{"action":"deleted"}', OFFICIAL_SECRET, signature)
    assert refusal.value.reason == "signature_mismatch"


def test_wrong_secret_is_refused() -> None:
    body = b'{"action":"created"}'
    with pytest.raises(WebhookRejectedError):
        verify_signature(body, "another-secret", _sign(OFFICIAL_SECRET, body))


@pytest.mark.parametrize(
    "provided",
    [
        None,
        "",
        "sha1=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17",
        "757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17",
        "sha256=",
        "sha256=deadbeef",
        "sha256=" + "z" * 64,
    ],
)
def test_missing_or_malformed_signatures_are_refused(provided: str | None) -> None:
    with pytest.raises(WebhookRejectedError):
        verify_signature(OFFICIAL_PAYLOAD, OFFICIAL_SECRET, provided)


def test_missing_secret_fails_closed() -> None:
    with pytest.raises(WebhookRejectedError) as refusal:
        verify_signature(OFFICIAL_PAYLOAD, "", OFFICIAL_SIGNATURE)
    assert refusal.value.reason == "webhook_secret_missing"


def test_uppercase_hex_signature_still_verifies() -> None:
    body = b'{"action":"created"}'
    signature = _sign(OFFICIAL_SECRET, body).upper().replace("SHA256=", "sha256=")
    verify_signature(body, OFFICIAL_SECRET, signature)


def test_constant_time_comparison_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison must go through hmac.compare_digest, not `==`."""
    calls: list[tuple[str, str]] = []
    original = hmac.compare_digest

    def spy(left, right):  # type: ignore[no-untyped-def]
        calls.append((str(left), str(right)))
        return original(left, right)

    monkeypatch.setattr("drake_api.github_app.webhook.hmac.compare_digest", spy)
    verify_signature(OFFICIAL_PAYLOAD, OFFICIAL_SECRET, OFFICIAL_SIGNATURE)
    assert calls, "signature comparison must use hmac.compare_digest"


@pytest.mark.parametrize(
    "value", [None, "", "not-a-uuid", "72d3162e", "72d3162e-cc78-11e3-81ab-4c9367dc0958x"]
)
def test_invalid_delivery_ids_are_refused(value: str | None) -> None:
    with pytest.raises(WebhookRejectedError) as refusal:
        validate_delivery_id(value)
    assert refusal.value.reason == "delivery_id_invalid"


def test_valid_delivery_id_passes() -> None:
    assert validate_delivery_id(VALID_DELIVERY) == VALID_DELIVERY


@pytest.mark.parametrize("event", sorted(SUPPORTED_EVENTS))
def test_supported_events_pass(event: str) -> None:
    assert validate_event_name(event) == event


# `push` moved into SUPPORTED_EVENTS in Sprint 11 because it acquired a
# real consumer: a default-branch push marks onboarding plans stale. These
# three still have none, and an event nothing acts on is a parser to keep
# safe for no reason.
@pytest.mark.parametrize("event", ["pull_request", "workflow_run", "check_suite"])
def test_unsupported_events_are_refused(event: str) -> None:
    with pytest.raises(WebhookRejectedError) as refusal:
        validate_event_name(event)
    assert refusal.value.reason == "event_unsupported"


@pytest.mark.parametrize("event", [None, "", "Installation", "in stallation", "x" * 90])
def test_malformed_event_names_are_refused(event: str | None) -> None:
    with pytest.raises(WebhookRejectedError):
        validate_event_name(event)


def test_ping_is_acknowledged_without_being_supported() -> None:
    assert validate_event_name("ping") == "ping"
    assert "ping" not in SUPPORTED_EVENTS


def test_payload_digest_is_stable_and_content_bound() -> None:
    assert payload_digest(b"a") == payload_digest(b"a")
    assert payload_digest(b"a") != payload_digest(b"b")
    assert len(payload_digest(b"a")) == 64


def test_envelope_keeps_only_bounded_identity_fields() -> None:
    payload = {
        "action": "added",
        "installation": {"id": 42, "account": {"login": "Duosis-Developer-Team"}},
        "repositories_added": [
            {
                "id": 101,
                "name": "Hermes",
                "full_name": "Duosis-Developer-Team/Hermes",
                "private": True,
                "node_id": "R_kgDO",
            },
        ],
        # Anything not explicitly chosen must be dropped.
        "sender": {"login": "someone", "email": "someone@example.test"},
        "secret_field": "should-never-be-kept",
    }
    envelope = build_envelope("installation_repositories", payload)
    serialized = str(envelope.as_json())
    assert "should-never-be-kept" not in serialized
    assert "someone@example.test" not in serialized
    assert envelope.installation_external_id == 42
    assert envelope.account_login == "Duosis-Developer-Team"
    assert envelope.repositories[0]["external_id"] == 101
    assert envelope.repositories[0]["membership"] == "present"


def test_envelope_marks_removed_repositories() -> None:
    payload = {
        "action": "removed",
        "installation": {"id": 7, "account": {"login": "Duosis-Developer-Team"}},
        "repositories_removed": [
            {"id": 202, "full_name": "Duosis-Developer-Team/logislot", "private": True}
        ],
    }
    envelope = build_envelope("installation_repositories", payload)
    assert envelope.repositories[0]["membership"] == "removed"


def test_envelope_is_bounded_against_a_hostile_payload() -> None:
    payload = {
        "action": "x" * 5_000,
        "installation": {"id": 1, "account": {"login": "y" * 5_000}},
        "repositories": [
            {"id": index, "full_name": f"o/r{index}", "private": True} for index in range(500)
        ],
    }
    envelope = build_envelope("installation_repositories", payload)
    assert len(envelope.action) <= 64
    assert len(envelope.account_login) <= 255
    assert len(envelope.repositories) <= 100


def test_envelope_ignores_malformed_repository_entries() -> None:
    payload = {
        "installation": {"id": 1},
        "repositories": ["not-an-object", {"no_id": True}, {"id": "not-an-int"}],
    }
    assert build_envelope("installation_repositories", payload).repositories == ()
