"""The authoritative onboarding path, in the shape production actually runs.

"Production-shaped" here means the configuration a real deployment has, not
`env=production` literally: the GitOps write path OFF, no pull-request
provider, and every other guarantee live. That is the state Sprint 12A
ships in, and this is the proof it works end to end without ever touching a
repository.

Nothing here uses the network, a real token, or a real repository. The
GitOps dispatcher is exercised only where a `RecordingProvider` is passed
in explicitly — which is the only way it can be reached now.
"""

import re
import uuid as uuidlib
from pathlib import Path

import pytest
from drake_api.onboarding import gitops, service
from drake_api.rbac.service import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import github_harness
from test_onboarding_integration import (
    _bootstrap,
    _identity,
    _principal,
    golden_tree,
)

pytestmark = pytest.mark.integration


async def _counts(engine: AsyncEngine, session_id: uuidlib.UUID) -> dict[str, int]:
    async with engine.connect() as connection:
        result: dict[str, int] = {}
        for name, query in (
            ("projects", "SELECT count(*) FROM projects"),
            ("environments", "SELECT count(*) FROM environments"),
            ("services", "SELECT count(*) FROM service_definitions"),
            ("slos", "SELECT count(*) FROM slo_definitions"),
            ("bindings", "SELECT count(*) FROM service_workload_bindings"),
            ("projections", "SELECT count(*) FROM github_repository_projects"),
        ):
            result[name] = int((await connection.execute(text(query))).scalar_one())
        result["receipts"] = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM onboarding_applies WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
        result["apply_audits"] = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events WHERE action = 'onboarding.apply' "
                        "AND target_id = :t"
                    ),
                    {"t": str(session_id)},
                )
            ).scalar_one()
        )
        result["gitops_requests"] = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM gitops_requests WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    return result


@pytest.mark.anyio
async def test_the_whole_authoritative_path_runs_with_the_write_path_off(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Projection → session → analyse → plan → approve → apply → catalog.

    With `github_gitops_pr_enabled` off, which is the only configuration
    production may run in. Every provider call in this test is a READ: the
    write path is not merely unused, it is unreachable.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    client = harness.app.state.github_client
    actor = await _identity(engine)

    assert settings.github_gitops_pr_enabled is False, "the shipped default"
    assert settings.gitops_worker_enabled is False

    created = await service.create_session(
        engine,
        settings,
        repository_row_id=row_id,
        actor_identity_id=actor,
        principal=await _principal(harness, engine),
    )
    session_id = uuidlib.UUID(created["session_id"])
    assert created["created"] is True

    analysis = await service.analyze(engine, settings, client, session_id=session_id)
    assert analysis["state"] == "ready"
    assert int(analysis["blocking_items"]) == 0

    async with engine.connect() as connection:
        version = int(
            (
                await connection.execute(
                    text("SELECT version FROM onboarding_sessions WHERE id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    await service.approve(
        engine,
        session_id=session_id,
        plan_version=int(analysis["plan_version"]),
        expected_version=version,
        actor_identity_id=actor,
    )

    applied = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=int(analysis["plan_version"]),
        idempotency_key="release-candidate-01",
        actor_identity_id=actor,
    )
    assert applied.outcome == "applied"
    assert applied.project_id is not None

    counts = await _counts(engine, session_id)
    assert counts["projects"] == 1
    assert counts["environments"] >= 1
    assert counts["services"] >= 1
    assert counts["projections"] == 1
    # One apply, one receipt, one audit row.
    assert counts["receipts"] == 1
    assert counts["apply_audits"] == 1
    # And nothing ever tried to write to the repository.
    assert counts["gitops_requests"] == 0

    async with engine.connect() as connection:
        state, project_key = (
            await connection.execute(
                text(
                    "SELECT s.state, p.project_key FROM onboarding_sessions s "
                    "JOIN projects p ON p.id = s.imported_project_id WHERE s.id = :s"
                ),
                {"s": session_id},
            )
        ).one()
    assert state == "imported"
    assert project_key == "datalake"


@pytest.mark.anyio
async def test_the_write_path_refuses_and_calls_nothing_while_it_is_disabled(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The disabled state is a refusal, not a queue that never drains.

    A request accepted now and delivered "later" would be a promise Drake
    cannot keep: there is no provider to deliver it with.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)
    created = await service.create_session(
        engine,
        settings,
        repository_row_id=row_id,
        actor_identity_id=actor,
        principal=await _principal(harness, engine),
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(engine, settings, harness.app.state.github_client, session_id=session_id)

    fake.calls.clear()
    with pytest.raises(service.OnboardingError) as refused:
        await gitops.request_pull_request(
            engine, settings, session_id=session_id, actor_identity_id=actor
        )
    assert refused.value.code == "gitops_disabled"

    # No token minted, no provider call, no row to deliver later.
    assert fake.calls == [], fake.calls
    counts = await _counts(engine, session_id)
    assert counts["gitops_requests"] == 0

    # And the worker claims nothing even if one is asked to run.
    provider = gitops.RecordingProvider(number=1)
    assert await gitops.process_pending(engine, settings, provider) == 0
    assert provider.calls == []

    # The status the browser reads says so plainly.
    async with harness.api_client() as api:
        await harness.login(api, "user-owner")
        status = (await api.get("/v1/onboarding/github/status")).json()
    assert status["gitops_pr_enabled"] is False


@pytest.mark.anyio
async def test_a_manifest_draft_is_produced_without_touching_the_repository(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The operator's way to get a manifest, with the write path off.

    Built from the session's stored analysis, so it costs no provider call
    and describes the commit that was actually reviewed.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)
    created = await service.create_session(
        engine,
        settings,
        repository_row_id=row_id,
        actor_identity_id=actor,
        principal=await _principal(harness, engine),
    )
    session_id = created["session_id"]
    await service.analyze(
        engine,
        settings,
        harness.app.state.github_client,
        session_id=uuidlib.UUID(session_id),
    )

    fake.calls.clear()
    async with harness.api_client() as api:
        await harness.login(api, "user-owner")
        draft = await api.get(f"/v1/onboarding/sessions/{session_id}/manifest-draft")

    assert draft.status_code == 200, draft.text
    assert draft.headers["content-type"].startswith("application/yaml")
    assert "attachment" in draft.headers["content-disposition"]
    assert draft.headers["cache-control"] == "no-store"
    assert "apiVersion: drake.duosis.com" in draft.text
    # Stored evidence only: no read, no token, and nothing written anywhere.
    assert fake.calls == [], fake.calls
    async with engine.connect() as connection:
        requests = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM gitops_requests WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    assert requests == 0


@pytest.mark.anyio
async def test_the_dispatcher_only_runs_against_a_provider_a_test_passes_in(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The one path that reaches a provider, and what it takes to reach it.

    Two explicit things: settings with the flags on — which only local/test
    may have — and a `RecordingProvider` handed to `process_pending`
    directly. Neither is available to a production process.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)
    created = await service.create_session(
        engine,
        settings,
        repository_row_id=row_id,
        actor_identity_id=actor,
        principal=await _principal(harness, engine),
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(engine, settings, harness.app.state.github_client, session_id=session_id)

    enabled = settings.model_copy(update={"github_gitops_pr_enabled": True})
    requested = await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )
    assert requested["created"] is True
    assert requested["state"] == "pending"

    provider = gitops.RecordingProvider(number=7)
    assert await gitops.process_pending(engine, enabled, provider) == 1
    assert len(provider.calls) == 1
    assert provider.calls[0]["file_path"] == ".drake/project.yaml"
    assert provider.calls[0]["head_branch"].startswith("drake/onboarding/")

    # The status the API reports comes from the stored row, not the fake.
    async with harness.api_client() as api:
        await harness.login(api, "user-owner")
        session = (await api.get(f"/v1/onboarding/sessions/{session_id}")).json()
    entries = session["gitops_requests"]
    assert len(entries) == 1
    assert entries[0]["state"] == "active"
    assert entries[0]["file_path"] == ".drake/project.yaml"

    # Exactly ONE url may appear, and only because Sprint 12B composes it
    # from values Drake already holds — the repository projection and the
    # pull request number. A provider-supplied `html_url` is never used, so
    # nothing in this response can point wherever a response said.
    body = str(session)
    urls = re.findall(r"https?://[^'\"\s]+", body)
    assert urls == [entries[0]["pull_request_url"]], urls
    assert urls[0].startswith("https://github.com/"), urls
    for forbidden in ("ghs_", "ghp_", "BEGIN", "Authorization"):
        assert forbidden not in body, forbidden


@pytest.mark.anyio
async def test_no_scope_or_gate_guarantee_was_relaxed_to_get_here(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The closeout must not have loosened anything on its way through.

    A release-candidate proof that quietly widened access would be worse
    than no proof, so the two boundaries most likely to be relaxed for
    convenience are re-asserted here.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings

    # A principal with no grants at all cannot open a session, and gets the
    # answer an unknown repository gets.
    stranger = Principal(identity_id=uuidlib.uuid4(), issuer=harness.provider.issuer)
    fake.calls.clear()
    with pytest.raises(service.OnboardingError) as refused:
        await service.create_session(
            engine,
            settings,
            repository_row_id=row_id,
            actor_identity_id=await _identity(engine),
            principal=stranger,
        )
    assert refused.value.code == "repository_not_found"
    assert refused.value.status == 404
    assert fake.calls == []

    # The Datalake security gate is still closed, and still refuses before
    # any provider call.
    from drake_api.github_app import catalog as repo_catalog

    assert repo_catalog.security_gate_for("Duosis-Developer-Team/Datalake-Platform-GUI") == (
        "manual_env_review"
    )


def test_the_recording_provider_is_never_the_production_default() -> None:
    """Stated once more where the onboarding suite will notice it moving."""
    from drake_api.main import create_app
    from drake_api.settings import Settings

    app = create_app(Settings(env="local"))
    assert isinstance(app.state.gitops_provider, gitops.RecordingProvider)

    production = Settings(
        env="production",
        public_origin="https://drake.example.test",
        allowed_web_origins=["https://drake.example.test"],
        auth_mode="oidc",
        oidc_issuer="https://issuer.example.test",
        oidc_client_id="drake",
        oidc_redirect_url="https://drake.example.test/v1/auth/callback",
        trusted_proxy_count=1,
        session_secret="x" * 64,
    )
    assert create_app(production).state.gitops_provider is None
