"""Worker redaction: credential shapes are masked and detected."""

from drake_worker.redaction import REDACTED, contains_credential_shape, redact


def test_url_credentials_are_masked() -> None:
    out = redact("redis://user:fakepw@cache:6379/0")
    assert "fakepw" not in out
    assert REDACTED in out


def test_assignments_are_masked() -> None:
    assert "hunter2" not in redact("token=hunter2")
    assert "hunter2" not in redact("api_key: hunter2")


def test_detection_matches_masking() -> None:
    assert contains_credential_shape("password=abc") is True
    assert contains_credential_shape("job completed in 42ms") is False
