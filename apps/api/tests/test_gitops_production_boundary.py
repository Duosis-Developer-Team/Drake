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
    ("flags", "why"),
    [
        ({"github_gitops_pr_enabled": True, "github_app_enabled": True}, "pull requests"),
        ({"gitops_worker_enabled": True}, "worker"),
        (
            {
                "github_gitops_pr_enabled": True,
                "gitops_worker_enabled": True,
                "github_app_enabled": True,
            },
            "both",
        ),
    ],
)
def test_a_production_process_refuses_to_start_with_the_write_path_on(
    flags: dict[str, Any], why: str
) -> None:
    """Fail closed at startup, not at the first request.

    A worker running against a fake, or requests accepted and never
    deliverable, are both half-enabled states — and a half-enabled write
    path is worse than a disabled one, because it looks like it works.
    """
    settings = _settings(
        github_app_private_key_file="/run/secrets/key.pem",
        github_webhook_secret_file="/run/secrets/webhook",
        github_app_client_id="Iv1.example",
        **flags,
    )
    with pytest.raises(RuntimeError) as refused:
        settings.validate_runtime_security()
    message = str(refused.value)
    assert "no real pull-request provider" in message, why
    # Names what to turn off. A refusal nobody can act on is a puzzle.
    assert "github_gitops_pr_enabled" in message
    assert "gitops_worker_enabled" in message


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
    assert app.state.gitops_provider is None, "a production process holds no fake provider"
    assert app.state.gitops_worker is None, "and runs no worker that could call one"


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
