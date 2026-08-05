"""Audit writer validation: enums, safe metadata, correlation propagation."""

import pytest
from drake_api.audit.service import AuditEventData, validate_event
from drake_api.correlation import correlation_id_var


def event(**overrides: object) -> AuditEventData:
    values: dict[str, object] = {
        "actor_type": "user",
        "actor_id": "subject-1",
        "action": "project.view",
        "result": "success",
    }
    values.update(overrides)
    return AuditEventData(**values)  # type: ignore[arg-type]


def test_valid_event_produces_insertable_values() -> None:
    values = validate_event(event(scope_type="project", scope_id="alpha"))
    assert values["actor_type"] == "user"
    assert values["action"] == "project.view"
    assert values["schema_version"] == 1
    assert values["correlation_id"]


def test_invalid_actor_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="actor_type"):
        validate_event(event(actor_type="robot"))


def test_invalid_result_is_rejected() -> None:
    with pytest.raises(ValueError, match="result"):
        validate_event(event(result="maybe"))


def test_missing_action_is_rejected() -> None:
    with pytest.raises(ValueError, match="required"):
        validate_event(event(action=""))


def test_credential_shaped_metadata_is_rejected_without_echoing_it() -> None:
    with pytest.raises(ValueError) as excinfo:
        validate_event(event(metadata={"note": "password=fake-not-real"}))
    assert "fake-not-real" not in str(excinfo.value)


def test_connection_string_metadata_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_event(event(metadata={"dsn": "postgresql://u:fakepw@host/db"}))


def test_oversized_metadata_is_rejected() -> None:
    with pytest.raises(ValueError, match="size"):
        validate_event(event(metadata={"blob": "x" * 10_000}))


def test_correlation_id_comes_from_context_when_absent() -> None:
    token = correlation_id_var.set("ctx-corr-1234")
    try:
        values = validate_event(event())
    finally:
        correlation_id_var.reset(token)
    assert values["correlation_id"] == "ctx-corr-1234"


def test_explicit_correlation_id_wins() -> None:
    values = validate_event(event(correlation_id="explicit-000001"))
    assert values["correlation_id"] == "explicit-000001"
