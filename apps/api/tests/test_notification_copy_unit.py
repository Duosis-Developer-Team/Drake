"""The sentence a recipient reads says what kind of incident this is.

A rule's recipient used to read "two consecutive trustworthy critical
evaluations" — the health engine's mechanism, which never ran for a rule.
"""

from drake_api.notifications.email import compose_message
from drake_api.notifications.model import event_sentence, incident_kind


def test_the_kind_follows_the_opening_reason() -> None:
    assert incident_kind("rule_firing") == "rule"
    assert incident_kind("alert_firing") == "alert"
    assert incident_kind("backup_overdue") == "protection"
    assert incident_kind("no_ready_replicas") == "health"
    assert incident_kind("anything_else") == "health"


def test_a_rule_incident_never_mentions_consecutive_evaluations() -> None:
    for event in ("opened", "auto_resolved"):
        sentence = event_sentence(event, "rule_firing")
        assert "consecutive" not in sentence
        assert "rule" in sentence
    assert "consecutive" in event_sentence("opened", "crash_loop")


def test_the_email_body_uses_the_same_sentence() -> None:
    message = compose_message(
        {
            "event_type": "opened",
            "incident": {
                "title": "Errors high · api",
                "severity": "critical",
                "primary_reason": "rule_firing",
            },
            "service": {"project_key": "hermes", "environment_key": "prod", "service_key": "api"},
        }
    )
    assert event_sentence("opened", "rule_firing") in message.body
    assert "consecutive" not in message.body
