"""The recording provider must not exist in a production process.

`RecordingProvider` is a test double, and it returns a pull-request NUMBER.
A production runtime holding one would report an open pull request that does
not exist, against a branch nobody created — and it would look exactly like
a working feature. Nothing downstream could tell the difference: the request
row says `active`, the API says `active`, the screen says `active`.

There is no real provider yet; Sprint 12B builds it. So the honest state for
production is "no provider at all", and the two GitOps flags cannot be
turned on there.

Two independent guards, on purpose:

- settings validation refuses the flags outside local/test, so the process
  does not start at all;
- the startup wiring constructs no provider outside local/test, so even a
  caller that got past the flags cannot end up holding a fake.

Neither of these adds a real provider, opens a flag, or contacts GitHub.
"""

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from drake_api.onboarding.gitops import RecordingProvider
from drake_api.settings import Settings

pytestmark = pytest.mark.anyio


def _app_material() -> dict[str, Any]:
    """A fully configured GitHub App, as production would have."""
    return {
        "github_app_enabled": True,
        "github_app_client_id": "Iv1.example",
        "github_app_private_key_file": "/run/secrets/key.pem",
        "github_webhook_secret_file": "/run/secrets/webhook",
    }


def _settings(**overrides: Any) -> Settings:
    """Production-shaped configuration, valid apart from what a test sets."""
    base: dict[str, Any] = {
        "env": "production",
        "public_origin": "https://drake.example.test",
        "auth_mode": "oidc",
        "oidc_issuer": "https://issuer.example.test",
        "oidc_client_id": "drake",
        "oidc_redirect_url": "https://drake.example.test/v1/auth/callback",
        "trusted_proxy_count": 1,
        "allowed_web_origins": ["https://drake.example.test"],
        "database_url": "postgresql+psycopg://drake:drake@db/drake",
        "redis_url": "redis://redis:6379/0",
        "session_secret": "x" * 64,
    }
    base.update(overrides)
    return Settings(**base)


def test_a_production_process_with_the_write_path_off_starts() -> None:
    """The default, and the only production configuration that is valid."""
    settings = _settings()
    assert settings.github_gitops_pr_enabled is False
    assert settings.gitops_worker_enabled is False
    # Does not raise: nothing about the write path is enabled.
    settings.validate_runtime_security()


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        # One flag on its own is a half-enabled write path: requests
        # accepted that nothing delivers, or a worker nothing can reach.
        ({"github_gitops_pr_enabled": True}, "must be set together"),
        ({"gitops_worker_enabled": True}, "must be set together"),
    ],
)
def test_production_refuses_half_of_the_write_path(flags: dict[str, Any], expected: str) -> None:
    """Both flags or neither. They are one decision."""
    settings = _settings(**_app_material(), **flags)
    with pytest.raises(RuntimeError) as refused:
        settings.validate_runtime_security()
    message = str(refused.value)
    assert expected in message
    assert "github_gitops_pr_enabled" in message
    assert "gitops_worker_enabled" in message


@pytest.mark.parametrize(
    ("expected", "overrides"),
    [
        # Each case is caught by whichever validator owns it. The App ones
        # were already there and are more specific, which is the right
        # order: a missing private key is a GitHub App problem, not a GitOps
        # one.
        ("require the GitHub App", {"github_app_enabled": False}),
        ("client id (or app id)", {"github_app_client_id": "", "github_app_id": ""}),
        ("private key and webhook secret", {"github_app_private_key_file": ""}),
        ("private key and webhook secret", {"github_webhook_secret_file": ""}),
    ],
)
def test_production_refuses_the_write_path_without_a_real_app(
    expected: str, overrides: dict[str, Any]
) -> None:
    """A write path needs something real behind it.

    The recording double is never constructed outside local/test, so
    without a fully configured App the write path would have nothing behind
    it at all — and that is a startup failure, not a runtime surprise.
    """
    material = _app_material()
    material.update(overrides)
    settings = _settings(**material, github_gitops_pr_enabled=True, gitops_worker_enabled=True)
    with pytest.raises(RuntimeError) as refused:
        settings.validate_runtime_security()
    assert expected in str(refused.value)


def test_production_refuses_a_write_path_pointed_somewhere_other_than_github() -> None:
    """A configurable API origin plus a write credential is an exfiltrator.

    Point it elsewhere and Drake pushes a repository's manifest — and its
    installation token — to whoever is listening.
    """
    settings = _settings(
        **_app_material(),
        github_gitops_pr_enabled=True,
        gitops_worker_enabled=True,
        github_api_base_url="https://api.github.example.test",
    )
    with pytest.raises(RuntimeError) as refused:
        settings.validate_runtime_security()
    assert "only target https://api.github.com" in str(refused.value)


def test_production_accepts_a_fully_configured_write_path() -> None:
    """The guard has to let the real thing through, or it is just an outage."""
    settings = _settings(
        **_app_material(), github_gitops_pr_enabled=True, gitops_worker_enabled=True
    )
    settings.validate_runtime_security()


def test_local_and_test_environments_may_still_enable_the_fake_path() -> None:
    """The harness has to keep working, or the feature cannot be tested."""
    for env in ("local", "test"):
        settings = Settings(
            env=env,
            github_app_enabled=True,
            github_gitops_pr_enabled=True,
            gitops_worker_enabled=True,
        )
        settings.validate_runtime_security()
        assert settings.github_gitops_pr_enabled is True


def test_a_production_app_holds_no_provider_and_no_worker() -> None:
    """The second guard, asserted on the real application.

    Built through `create_app`, not by re-evaluating the wiring expression
    here: a test that reimplements the thing it checks passes when the code
    it is meant to guard changes underneath it.
    """
    from drake_api.main import create_app

    app = create_app(_settings())
    assert app.state.gitops_provider is None, "no App configured, so nothing to write with"
    assert app.state.gitops_worker is None, "and no worker that could call one"


def test_a_production_app_with_a_real_app_holds_the_real_provider(tmp_path: Path) -> None:
    """And never the double — the environment decides, not a flag."""
    from drake_api.main import create_app
    from drake_api.onboarding.github_provider import GitHubPullRequestProvider

    key = tmp_path / "app-key.pem"
    key.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    secret = tmp_path / "webhook-secret"
    secret.write_text("not-a-real-secret")
    material = _app_material()
    material["github_app_private_key_file"] = str(key)
    material["github_webhook_secret_file"] = str(secret)

    app = create_app(_settings(**material))
    assert isinstance(app.state.gitops_provider, GitHubPullRequestProvider)
    assert not isinstance(app.state.gitops_provider, RecordingProvider)
    # The flags are off, so there is still no worker.
    assert app.state.gitops_worker is None


def test_a_local_app_wires_the_recording_provider() -> None:
    """The harness keeps its test double — explicitly, and only here."""
    from drake_api.main import create_app

    app = create_app(Settings(env="local"))
    assert isinstance(app.state.gitops_provider, RecordingProvider)
    # Constructed, and never used until something turns the flags on.
    assert app.state.gitops_provider.calls == []
    assert app.state.gitops_worker is None, "the flags are off, so there is no worker"


def test_a_local_app_with_the_flags_on_runs_the_worker_against_the_fake(
    tmp_path: Path,
) -> None:
    """The configuration the E2E and integration suites rely on.

    Real App material is written to a temp directory because the client
    validates that the references point at files — a check worth keeping,
    so the test satisfies it rather than routing around it.
    """
    from drake_api.main import create_app

    key = tmp_path / "app-key.pem"
    key.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    secret = tmp_path / "webhook-secret"
    secret.write_text("local-only-not-a-real-secret")

    app = create_app(
        Settings(
            env="local",
            github_app_enabled=True,
            github_app_client_id="Iv1.local",
            github_app_private_key_file=str(key),
            github_webhook_secret_file=str(secret),
            github_gitops_pr_enabled=True,
            gitops_worker_enabled=True,
        )
    )
    assert isinstance(app.state.gitops_provider, RecordingProvider)
    assert app.state.gitops_worker is not None
