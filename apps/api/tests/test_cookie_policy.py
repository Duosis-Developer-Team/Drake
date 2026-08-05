"""Session cookie policy unit tests (no network, no stack)."""

from drake_api.auth.router import _set_session_cookie
from drake_api.testing import make_settings
from fastapi.responses import JSONResponse


def cookie_header_for(env: str) -> str:
    response = JSONResponse({})
    _set_session_cookie(response, make_settings(env=env), "session-id-value")
    return response.headers["set-cookie"]


def test_cookie_is_httponly_lax_everywhere() -> None:
    header = cookie_header_for("local")
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower()
    assert "Path=/" in header


def test_cookie_secure_flag_off_in_local_and_test() -> None:
    assert "Secure" not in cookie_header_for("local")
    assert "Secure" not in cookie_header_for("test")


def test_cookie_secure_flag_on_outside_local_test() -> None:
    assert "Secure" in cookie_header_for("dev")
    assert "Secure" in cookie_header_for("prod")


def test_production_guard_rejects_plaintext_issuer() -> None:
    import pytest

    settings = make_settings(env="prod", oidc_issuer="http://fake-oidc.test")
    with pytest.raises(RuntimeError, match="plaintext OIDC issuer"):
        settings.validate_runtime_security()

    settings_ok = make_settings(env="local", oidc_issuer="http://fake-oidc.test")
    settings_ok.validate_runtime_security()  # local/test may use the fake provider
