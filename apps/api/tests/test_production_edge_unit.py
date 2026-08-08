"""The production edge contract (ADR-0021).

One public origin, https only, and every externally visible URL derived
from it rather than from the request — because a forged Host or
X-Forwarded-Host must not be able to decide where Drake sends a user.
"""

import pytest
from drake_api.origin import (
    GITHUB_WEBHOOK_PATH,
    OIDC_CALLBACK_PATH,
    InvalidOriginError,
    github_webhook_url,
    oidc_redirect_url,
    parse_public_origin,
)
from drake_api.testing import TEST_PUBLIC_ORIGIN, make_settings


def test_a_valid_production_origin_is_accepted() -> None:
    origin = parse_public_origin("https://drake.example.test")
    assert str(origin) == "https://drake.example.test"
    assert origin.scheme == "https"
    assert origin.host == "drake.example.test"


def test_a_port_is_preserved() -> None:
    assert str(parse_public_origin("https://drake.example.test:8443")) == (
        "https://drake.example.test:8443"
    )


@pytest.mark.parametrize(
    ("value", "because"),
    [
        ("", "empty"),
        ("http://drake.example.test", "plaintext"),
        ("https://localhost", "localhost"),
        ("https://127.0.0.1", "loopback ip"),
        ("https://[::1]", "loopback ipv6"),
        ("https://203.0.113.10", "bare ip"),
        ("https://user:pass@drake.example.test", "embedded credentials"),
        ("https://drake.example.test/base", "path"),
        ("https://drake.example.test/?a=b", "query"),
        ("https://drake.example.test/#frag", "fragment"),
        ("https://*.example.test", "wildcard"),
        ("https://drake", "not fully qualified"),
        ("https://REPLACE_ME.example.test", "placeholder"),
        ("https://drake_.example.test", "malformed label"),
        ("ftp://drake.example.test", "wrong scheme"),
    ],
)
def test_production_origins_that_must_be_refused(value: str, because: str) -> None:
    with pytest.raises(InvalidOriginError):
        parse_public_origin(value)


def test_local_development_may_use_loopback() -> None:
    """Production rules are not relaxed to keep development working."""
    origin = parse_public_origin("http://127.0.0.1:3000", require_https=False)
    assert str(origin) == "http://127.0.0.1:3000"
    with pytest.raises(InvalidOriginError):
        parse_public_origin("http://127.0.0.1:3000", require_https=True)


def test_external_urls_derive_from_the_one_origin() -> None:
    origin = parse_public_origin("https://drake.example.test")
    assert oidc_redirect_url(origin) == f"https://drake.example.test{OIDC_CALLBACK_PATH}"
    assert github_webhook_url(origin) == f"https://drake.example.test{GITHUB_WEBHOOK_PATH}"


def test_the_documented_paths_are_the_ones_the_app_serves() -> None:
    """The operator pastes these into GitHub and the OIDC provider.

    They are asserted against the real routers so the runbook cannot drift
    from what the application actually mounts.
    """
    from drake_api.auth.router import router as auth_router
    from drake_api.github_app.router_webhook import router as webhook_router

    auth_paths = {route.path for route in auth_router.routes}  # type: ignore[attr-defined]
    webhook_paths = {route.path for route in webhook_router.routes}  # type: ignore[attr-defined]
    assert OIDC_CALLBACK_PATH in auth_paths
    assert GITHUB_WEBHOOK_PATH in webhook_paths


# --- settings validation --------------------------------------------------


def _production(**overrides: object):  # type: ignore[no-untyped-def]
    return make_settings(env="prod", oidc_issuer="https://issuer.example.test", **overrides)


def test_a_valid_production_configuration_passes() -> None:
    _production().validate_runtime_security()


def test_production_refuses_a_plaintext_public_origin() -> None:
    settings = _production(public_origin="http://drake.example.test")
    with pytest.raises(InvalidOriginError):
        settings.validate_runtime_security()


def test_production_requires_a_trusted_proxy_hop() -> None:
    """Zero hops means forwarded headers would come straight from a client."""
    settings = _production(trusted_proxy_count=0)
    with pytest.raises(RuntimeError, match="trusted_proxy_count"):
        settings.validate_runtime_security()


def test_production_refuses_a_web_origin_other_than_the_canonical_one() -> None:
    settings = _production(allowed_web_origins=["https://someone-else.example.test"])
    with pytest.raises(RuntimeError, match="canonical public origin"):
        settings.validate_runtime_security()


def test_production_refuses_cors() -> None:
    """Single origin means there is nothing to allow cross-origin."""
    settings = _production(cors_origins=["https://drake.example.test"])
    with pytest.raises(RuntimeError, match="no CORS"):
        settings.validate_runtime_security()


def test_production_requires_the_redirect_url_to_match_the_origin() -> None:
    settings = _production(oidc_redirect_url="https://elsewhere.example.test/v1/auth/callback")
    with pytest.raises(RuntimeError, match="canonical public origin"):
        settings.validate_runtime_security()


def test_local_keeps_its_existing_ergonomics() -> None:
    make_settings(env="local").validate_runtime_security()


def test_the_resolved_origin_is_the_configured_one() -> None:
    assert str(_production().resolved_public_origin()) == TEST_PUBLIC_ORIGIN
