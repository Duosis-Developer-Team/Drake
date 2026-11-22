"""Security gates: none is open on a real repository; one is configuration."""

import pytest
from drake_api.github_app import catalog
from drake_api.main import create_app
from drake_api.settings import Settings
from drake_api.testing import make_settings


def test_datalake_is_read_like_any_other_repository() -> None:
    """The owner closed its `.env` gate on 2026-11-22."""
    full_name = "Duosis-Developer-Team/Datalake-Platform-GUI"
    assert catalog.security_gate_for(full_name) is None
    assert catalog.gate_reason_for(full_name) == ""
    assert catalog.is_dry_run_eligible(full_name)
    assert all(entry.security_gate is None for entry in catalog.INITIAL_CATALOG)


def test_a_gate_is_configuration_and_matches_the_normalized_name() -> None:
    create_app(make_settings(github_gated_repositories=["Acme/Secret-Repo"]))
    assert catalog.security_gate_for("  acme/secret-repo ") == catalog.CONFIGURED_GATE
    assert catalog.gate_reason_for("ACME/Secret-Repo") == catalog.CONFIGURED_GATE_REASON
    assert not catalog.is_dry_run_eligible("acme/secret-repo")
    assert catalog.security_gate_for("acme/other") is None


def test_gated_repositories_are_owner_slash_name() -> None:
    settings = Settings(github_gated_repositories=[" Acme/widget.web "])
    assert settings.github_gated_repositories == ["Acme/widget.web"]
    for bad in ("widget", "acme/", "/widget", "acme/wid get"):
        with pytest.raises(ValueError):
            Settings(github_gated_repositories=[bad])
    assert Settings().github_gated_repositories == []
