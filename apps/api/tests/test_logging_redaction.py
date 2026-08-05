"""Redaction filter: credential-shaped content never reaches log output."""

import json
import logging

from drake_api.logging import REDACTED, JsonFormatter, RedactionFilter, redact


def format_with_pipeline(message: str) -> str:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    RedactionFilter().filter(record)
    return JsonFormatter().format(record)


def test_connection_string_password_is_masked() -> None:
    out = format_with_pipeline("connect failed: postgresql://drake:supersecretpw@dbhost:5432/x")
    assert "supersecretpw" not in out
    assert REDACTED in out


def test_password_assignment_is_masked() -> None:
    out = format_with_pipeline("retry with password=hunter2 next time")
    assert "hunter2" not in out


def test_bearer_token_is_masked() -> None:
    out = format_with_pipeline("upstream sent Authorization: Bearer abc.def-ghi_jkl012345")
    assert "abc.def-ghi_jkl012345" not in out


def test_plain_messages_are_untouched() -> None:
    out = json.loads(format_with_pipeline("inventory reconciled in 42ms"))
    assert out["message"] == "inventory reconciled in 42ms"
    assert out["level"] == "INFO"


def test_redact_is_idempotent_and_preserves_structure() -> None:
    once = redact("postgresql://u:pw@h/db")
    assert redact(once) == once
    assert once.startswith("postgresql://u:")
    assert once.endswith("@h/db")
