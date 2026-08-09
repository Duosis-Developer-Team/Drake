"""Secure project onboarding: discovery, plan, approval, apply, GitOps.

The source-of-truth boundary this suite defends, stated once:

    Drake catalog       authoritative runtime projection
    .drake/project.yaml versioned repository INTENT
    GitHub discovery    evidence

A manifest says what a repository WANTS. It does not get to be right, it
cannot choose infrastructure, and it cannot grant anyone anything. Each
scenario below is one place that boundary could be crossed — or one place
an unreviewed change could reach the catalog.

Every provider interaction here is a local fake. Nothing in this file
contacts GitHub, and nothing touches the real Datalake repository.
"""

import json
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import catalog as repo_catalog
from drake_api.github_app.onboarding_service import OnboardingError
from drake_api.github_app.webhook import SUPPORTED_EVENTS, default_branch_push
from drake_api.onboarding import gitops, service
from drake_api.onboarding import repository as onboarding_repo
from drake_api.onboarding.model import (
    Action,
    CatalogSnapshot,
    EntityKind,
    build_plan,
)
from drake_api.settings import Settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import (
    DATALAKE_ID,
    HERMES_ID,
    INSTALLATION_ID,
    _seed_admin,
    deliver,
    github_harness,
    installation_payload,
)
from test_github_onboarding_integration import (
    HEAD_SHA,
    NEXT_SHA,
    OWNER,
    _register_cluster,
    _row_id,
)

pytestmark = pytest.mark.integration

CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts"
GOLDEN = CONTRACTS / "onboarding" / "datalake.golden-path.v1.json"
EXAMPLE_MANIFEST = CONTRACTS / "onboarding" / "datalake.project.example.yaml"


# ===========================================================================
# the sanitized Datalake fixture
# ===========================================================================


def golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text())


def datalake_manifest(repository_name: str = "Hermes") -> str:
    """The sanitized example, retargeted at the fixture repository.

    Retargeted because the REAL Datalake repository is closed by a manual
    security gate and must never be scanned. The golden path proves the
    mechanism; it does not touch that repository.
    """
    return (
        EXAMPLE_MANIFEST.read_text()
        .replace("name: Datalake-Platform-GUI", f"name: {repository_name}")
        .replace("clusterRef: drake-local", "clusterRef: cluster-a")
    )


def golden_tree(manifest: str | None = None) -> dict[str, str]:
    """A file tree in the shape of the golden-path fixture.

    Every `refused` entry from the contract is present on purpose: a test
    that only supplies safe files proves nothing about what the importer
    refuses to read.
    """
    tree: dict[str, str] = {
        ".drake/project.yaml": manifest if manifest is not None else datalake_manifest(),
        "package.json": '{"name": "datalake-gui", "dependencies": {"next": "15.0.0"}}',
        "pyproject.toml": '[project]\ndependencies = ["fastapi"]\n',
        "Dockerfile": "FROM python:3.13-slim\n",
        "README.md": "# Datalake\n",
        ".github/workflows/ci.yaml": "name: ci\non: [push]\n",
    }
    for entry in golden()["tree"]:
        if entry["kind"] in ("refused", "never_executed"):
            # Deliberately credential-SHAPED but not a credential: the point
            # is that these paths are never read, so the value never matters.
            tree[entry["path"]] = "PLACEHOLDER=not-a-real-value\n"
    return tree


# ===========================================================================
# the planner (pure)
# ===========================================================================


def manifest_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "apiVersion": "drake.duosis.com/v1alpha1",
        "kind": "ProjectObservability",
        "metadata": {"name": "datalake", "displayName": "Datalake"},
        "spec": {
            "repository": {"provider": "github", "owner": OWNER, "name": "Hermes"},
            "owners": [{"team": "data-platform", "role": "primary"}],
            "environments": [
                {
                    "name": "dev",
                    "runtime": "kubernetes",
                    "branch": "main",
                    "clusterRef": "cluster-a",
                    "namespace": "datalake-dev",
                    "criticality": "medium",
                }
            ],
            "services": [
                {
                    "name": "datalake-api",
                    "component": "api",
                    "runtime": "python",
                    "metricsProfile": "fastapi-v1",
                },
                {
                    "name": "datalake-gui",
                    "component": "web",
                    "runtime": "node",
                    "metricsProfile": "nextjs-v1",
                },
            ],
            "tenantModel": {"mode": "none"},
        },
    }
    document["spec"].update(overrides)
    return document


def snapshot(**overrides: Any) -> CatalogSnapshot:
    base: dict[str, Any] = {
        "clusters": {"cluster-a": "cluster-id"},
        "owner_teams": {"data-platform": "data-platform"},
        "metric_profiles": frozenset({"fastapi-v1", "nextjs-v1"}),
        "slo_profiles": frozenset({"availability", "latency"}),
    }
    base.update(overrides)
    return CatalogSnapshot(**base)


def actions_for(plan: Any, kind: str) -> dict[str, str]:
    """Items of one entity kind."""
    return {item.item_key: item.action for item in plan.items if item.entity_kind == kind}


def bindings_for(plan: Any) -> dict[str, str]:
    return {
        item.item_key: item.action
        for item in plan.items
        if item.entity_kind == str(EntityKind.WORKLOAD_BINDING)
    }


def test_a_clean_manifest_plans_creates_and_blocks_nothing() -> None:
    plan = build_plan(manifest_document(), snapshot(), repository_row_id="repo-1")
    assert plan.blocking_items == 0
    assert plan.state == "ready"
    services = actions_for(plan, str(EntityKind.SERVICE))
    # Two runtimes were evidenced, so two services are proposed. Drake does
    # not collapse them into one and does not invent a third.
    assert services == {
        "service:datalake-api": str(Action.CREATE),
        "service:datalake-gui": str(Action.CREATE),
    }


def test_an_unknown_cluster_is_unmapped_and_never_created() -> None:
    """Clusters are operator-registered infrastructure."""
    plan = build_plan(manifest_document(), snapshot(clusters={}), repository_row_id="repo-1")
    bindings = actions_for(plan, str(EntityKind.CLUSTER_BINDING))
    assert bindings == {"cluster_binding:dev": str(Action.UNMAPPED)}
    assert plan.blocking_items >= 1
    assert plan.state == "needs_review"


def test_an_unknown_metric_profile_is_unmapped() -> None:
    plan = build_plan(
        manifest_document(),
        snapshot(metric_profiles=frozenset({"fastapi-v1"})),
        repository_row_id="repo-1",
    )
    profiles = actions_for(plan, str(EntityKind.METRIC_PROFILE))
    assert profiles["metric_profile:datalake-gui"] == str(Action.UNMAPPED)
    assert plan.blocking_items >= 1


def test_a_project_key_owned_by_another_repository_is_a_conflict() -> None:
    """Taking over a key silently is not something Drake does."""
    plan = build_plan(
        manifest_document(),
        snapshot(
            projects={"datalake": "project-1"},
            project_repository={"datalake": "some-other-repo"},
        ),
        repository_row_id="repo-1",
    )
    project = actions_for(plan, str(EntityKind.PROJECT))
    assert project["project:datalake"] == str(Action.CONFLICT)
    assert plan.state == "needs_review"


def test_a_project_key_with_no_repository_is_offered_as_a_link() -> None:
    plan = build_plan(
        manifest_document(),
        snapshot(projects={"datalake": "project-1"}),
        repository_row_id="repo-1",
    )
    assert actions_for(plan, str(EntityKind.PROJECT))["project:datalake"] == str(Action.LINK)
    assert plan.blocking_items == 0


def test_a_service_the_manifest_dropped_is_reported_and_never_deleted() -> None:
    """A manifest edit is not evidence that a running service stopped."""
    plan = build_plan(
        manifest_document(),
        snapshot(
            projects={"datalake": "project-1"},
            project_repository={"datalake": "repo-1"},
            catalog_only_services=("datalake-worker",),
        ),
        repository_row_id="repo-1",
    )
    orphan = next(
        item for item in plan.items if item.item_key == "service_catalog_only:datalake-worker"
    )
    assert orphan.action == str(Action.UNMAPPED)
    assert orphan.reason_code == "catalog_only"
    # And there is no `delete` action anywhere in the vocabulary.
    assert "delete" not in {str(action) for action in Action}


def test_a_namespace_another_environment_already_occupies_is_a_conflict() -> None:
    plan = build_plan(
        manifest_document(),
        snapshot(namespace_bindings={("cluster-a", "datalake-dev"): "staging"}),
        repository_row_id="repo-1",
    )
    bindings = actions_for(plan, str(EntityKind.NAMESPACE_BINDING))
    assert bindings["namespace_binding:dev"] == str(Action.CONFLICT)


def test_a_truncated_analysis_blocks_the_plan() -> None:
    """An incomplete picture is never a green light."""
    plan = build_plan(manifest_document(), snapshot(), repository_row_id="repo-1", truncated=True)
    assert plan.state == "needs_review"
    assert any(item.item_key == "analysis:truncated" for item in plan.items)


def test_the_plan_digest_ignores_item_order_but_not_content() -> None:
    first = build_plan(manifest_document(), snapshot(), repository_row_id="repo-1")
    second = build_plan(manifest_document(), snapshot(), repository_row_id="repo-1")
    second.items.reverse()
    assert first.digest() == second.digest()

    different = build_plan(manifest_document(), snapshot(clusters={}), repository_row_id="repo-1")
    assert different.digest() != first.digest()


# ===========================================================================
# the fixture itself
# ===========================================================================


def test_the_golden_fixture_carries_no_secret_and_no_url() -> None:
    document = golden()
    serialized = json.dumps(document)
    assert "://" not in serialized
    for token in ("BEGIN", "PRIVATE KEY", "ghp_", "AKIA", "password", "token="):
        assert token not in serialized

    manifest_text = EXAMPLE_MANIFEST.read_text()
    body = "\n".join(
        line for line in manifest_text.splitlines() if not line.strip().startswith("#")
    )
    assert "://" not in body
    for forbidden in ("promql", "kubeconfig", "Authorization", "-----BEGIN"):
        assert forbidden not in body


def test_the_example_manifest_passes_schema_and_policy() -> None:
    from drake_api.github_app.manifest import validate_content

    result = validate_content(EXAMPLE_MANIFEST.read_text())
    assert result.valid, [finding.rule for finding in result.findings]


def test_the_real_datalake_repository_stays_closed_by_its_security_gate() -> None:
    """Sprint 5A gated it over a tracked `.env`. Sprint 11 does not open it.

    Onboarding the real repository would mean scanning it, and the gate is
    exactly the mechanism that says not yet. The golden path is proven
    against Drake's own sanitized fixture instead.
    """
    gate = repo_catalog.security_gate_for(f"{OWNER}/Datalake-Platform-GUI")
    assert gate == "manual_env_review"


# ===========================================================================
# manifest refusals
# ===========================================================================


def test_a_manifest_carrying_a_credential_or_a_url_is_refused() -> None:
    from drake_api.github_app.manifest import validate_content

    base = datalake_manifest()
    # A URL where the schema allows only a dashboard KEY.
    linked = validate_content(base + "  dashboards:\n    - https://grafana.internal/d/abc\n")
    assert not linked.valid
    assert {finding.rule for finding in linked.findings} & {"schema", "plaintext-endpoint"}

    # An unknown field carrying a credential, refused by the schema before
    # the value is even considered.
    keyed = validate_content(
        base.replace(
            "  tenantModel:\n    mode: none",
            '  tenantModel:\n    mode: none\n    apiKey: "AKIA0000000000000000"',
        )
    )
    assert not keyed.valid
    assert any(finding.rule == "schema" for finding in keyed.findings)

    # And a credential in a field the schema DOES allow is caught by policy.
    policed = validate_content(
        base.replace("displayName: Datalake Platform", "displayName: 'password: hunter2xyz'")
    )
    assert not policed.valid
    assert any(finding.rule == "credential-assignment" for finding in policed.findings)


def test_a_manifest_with_a_duplicate_key_or_a_future_version_is_refused() -> None:
    from drake_api.github_app.manifest import validate_content

    future = datalake_manifest().replace(
        "apiVersion: drake.duosis.com/v1alpha1", "apiVersion: drake.duosis.com/v2"
    )
    result = validate_content(future)
    assert not result.valid
    # An unsupported future version is refused, never processed as if it
    # were the contract Drake knows.
    assert any(finding.rule == "schema" for finding in result.findings)

    duplicate = datalake_manifest() + "metadata:\n  name: other\n"
    duplicated = validate_content(duplicate)
    assert not duplicated.valid


def test_a_manifest_cannot_choose_a_tenant_a_cluster_it_owns_or_a_permission() -> None:
    """Schema `additionalProperties: false` is what makes this true."""
    from drake_api.github_app.manifest import validate_content

    for injected in (
        "  permissions:\n    - rbac.manage\n",
        "  tenants:\n    - id: acme\n",
        "  commands:\n    - kubectl apply -f deploy/\n",
    ):
        result = validate_content(datalake_manifest() + injected)
        assert not result.valid


# ===========================================================================
# the webhook boundary
# ===========================================================================


def test_push_is_supported_and_pull_request_is_not() -> None:
    """An event with no consumer is a parser to keep safe for no reason."""
    assert "push" in SUPPORTED_EVENTS
    assert "pull_request" not in SUPPORTED_EVENTS


def test_only_a_default_branch_push_moves_anything() -> None:
    repository = {"id": 42, "default_branch": "main"}
    assert default_branch_push(
        {"ref": "refs/heads/main", "after": "a" * 40, "repository": repository}
    ) == (42, "a" * 40)

    for payload in (
        {"ref": "refs/heads/feature", "after": "a" * 40, "repository": repository},
        {"ref": "refs/tags/v1", "after": "a" * 40, "repository": repository},
        {"ref": "refs/heads/main", "after": "0" * 40, "repository": repository},
        {"ref": "refs/heads/main", "after": "a" * 40, "deleted": True, "repository": repository},
        {"ref": "refs/heads/main", "after": "not-a-sha", "repository": repository},
        {"ref": "refs/heads/main", "after": "a" * 40},
    ):
        assert default_branch_push(payload) is None


# ===========================================================================
# not configured
# ===========================================================================


def test_a_deployment_with_no_github_app_refuses_before_touching_anything() -> None:
    settings = Settings(env="test")
    with pytest.raises(OnboardingError) as refusal:
        service.require_configured(settings)
    assert refusal.value.code == "github_not_configured"


@pytest.mark.anyio
async def test_the_status_endpoint_reports_not_configured_and_mints_no_token(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    from harness_s1 import build_harness, grant_platform_owner, require_it_settings

    harness = build_harness(require_it_settings())
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        status = await client.get("/v1/onboarding/github/status")
        sessions = await client.get("/v1/onboarding/sessions")

    body = status.json()
    assert body["configuration_state"] == "not_configured"
    assert "feature_disabled" in body["missing_operator_inputs"]
    # No reference NAME and no reference value ever leaves the API.
    assert all("/" not in item for item in body["missing_operator_inputs"])
    # And the session list is honestly empty rather than invented.
    assert sessions.json()["items"] == []
    assert sessions.json()["total"] == 0


# ===========================================================================
# the golden path, end to end, against the local fake
# ===========================================================================


async def _bootstrap(
    harness: Any, engine: AsyncEngine, fake: Any, tree: dict[str, str]
) -> uuidlib.UUID:
    from drake_api.github_app import service as github_service

    fake.branch_heads["Hermes"] = HEAD_SHA
    fake.trees["Hermes"] = tree
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
    row_id = await _row_id(engine, HERMES_ID)
    reconciler = github_service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_repository(row_id, INSTALLATION_ID, f"{OWNER}/Hermes", HERMES_ID)
    return row_id


async def _identity(engine: AsyncEngine, subject: str = "user-owner") -> uuidlib.UUID:
    async with engine.connect() as connection:
        return uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM identities WHERE subject = :s"), {"s": subject}
                    )
                ).scalar_one()
            )
        )


@pytest.mark.anyio
async def test_the_golden_path_produces_a_reviewable_plan_and_an_import(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The whole sprint, end to end, against the sanitized fixture."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    client = harness.app.state.github_client
    actor = await _identity(engine)

    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    assert created["created"] is True

    analysis = await service.analyze(engine, settings, client, session_id=session_id)
    assert analysis["state"] == "ready"
    assert analysis["blocking_items"] == 0
    assert analysis["truncated"] is False

    async with engine.connect() as connection:
        from drake_api.rbac.service import Principal

        principal = Principal(identity_id=actor, issuer=harness.provider.issuer)
        plan = await onboarding_repo.session_plan(connection, principal, session_id)
    assert plan is not None
    assert plan["plan"]["applicable"] is True
    assert plan["plan"]["commit_sha"] == HEAD_SHA
    kinds = {item["entity_kind"] for item in plan["items"]}
    # The plan is reviewable at every level the sprint asks for.
    assert {"project", "environment", "service", "owner_team", "repository"} <= kinds
    assert {"cluster_binding", "namespace_binding", "metric_profile"} <= kinds

    async with engine.connect() as connection:
        session = await onboarding_repo.get_session(connection, principal, session_id)
    assert session is not None

    await service.approve(
        engine,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        expected_version=session["version"],
        actor_identity_id=actor,
    )
    outcome = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="apply-key-0001",
        actor_identity_id=actor,
    )
    assert outcome.outcome == "applied"
    assert outcome.project_id is not None

    # The imported catalog identity is the one every other module uses.
    async with engine.connect() as connection:
        project_key = (
            await connection.execute(
                text("SELECT project_key FROM projects WHERE id = :id"),
                {"id": outcome.project_id},
            )
        ).scalar_one()
        services = sorted(
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT service_key FROM service_definitions WHERE project_id = :p"),
                    {"p": outcome.project_id},
                )
            ).all()
        )
        integrations = sorted(
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT integration_type FROM integrations i "
                        "JOIN projects p ON p.scope_id = i.scope_id WHERE p.id = :p"
                    ),
                    {"p": outcome.project_id},
                )
            ).all()
        )
    assert project_key == "datalake"
    assert services == ["datalake-api", "datalake-gui"]
    # Honest placeholders: registered, and reporting nothing until wired.
    assert "prometheus" in integrations and "cluster-agent" in integrations


@pytest.mark.anyio
async def test_nothing_in_the_repository_is_executed_and_secrets_are_never_read(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The importer reads metadata. It does not run the thing it reads."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)

    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(engine, settings, harness.app.state.github_client, session_id=session_id)

    # Not one provider request names a refused path.
    refused = [entry["path"] for entry in golden()["tree"] if entry["kind"] == "refused"]
    for path in refused:
        assert not any(path in call for call in fake.calls), path

    async with engine.connect() as connection:
        paths = [
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT f.safe_path FROM onboarding_findings f "
                        "JOIN onboarding_analyses a ON a.id = f.analysis_id "
                        "WHERE a.session_id = :s"
                    ),
                    {"s": session_id},
                )
            ).all()
        ]
    for path in [*refused, "scripts/bootstrap.sh"]:
        assert path not in paths, path
    # And no finding carries file content, only paths and codes.
    assert all("PLACEHOLDER" not in path for path in paths)


@pytest.mark.anyio
async def test_the_same_commit_and_analyzer_produce_one_analysis(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)
    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])

    first = await service.analyze(
        engine, settings, harness.app.state.github_client, session_id=session_id
    )
    second = await service.analyze(
        engine, settings, harness.app.state.github_client, session_id=session_id
    )
    assert first["reused"] is False
    assert second["reused"] is True
    assert first["analysis_id"] == second["analysis_id"]

    async with engine.connect() as connection:
        analyses = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM onboarding_analyses WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    assert analyses == 1


@pytest.mark.anyio
async def test_a_moved_commit_makes_the_plan_stale_and_unappliable(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A review of a commit is not a review of its successor."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    client = harness.app.state.github_client
    actor = await _identity(engine)

    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    analysis = await service.analyze(engine, settings, client, session_id=session_id)

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
        plan_version=analysis["plan_version"],
        expected_version=version,
        actor_identity_id=actor,
    )

    # Someone pushes to the default branch between review and apply.
    fake.branch_heads["Hermes"] = NEXT_SHA
    with pytest.raises(OnboardingError) as refusal:
        await service.apply(
            engine,
            settings,
            client,
            session_id=session_id,
            plan_version=analysis["plan_version"],
            idempotency_key="apply-key-stale",
            actor_identity_id=actor,
        )
    assert refusal.value.code == "plan_stale"

    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT state FROM onboarding_plans WHERE session_id = :s"),
                {"s": session_id},
            )
        ).scalar_one()
        projects = int(
            (await connection.execute(text("SELECT count(*) FROM projects"))).scalar_one()
        )
    assert state == "stale"
    # Nothing was written.
    assert projects == 0


@pytest.mark.anyio
async def test_a_push_marks_a_reviewed_plan_stale(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)
    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(engine, settings, harness.app.state.github_client, session_id=session_id)

    marked = await service.mark_stale_for_commit(
        engine, repository_row_id=row_id, new_commit_sha=NEXT_SHA
    )
    assert marked == 1

    async with engine.connect() as connection:
        plan_state, session_state = (
            await connection.execute(
                text(
                    "SELECT p.state, s.state FROM onboarding_plans p "
                    "JOIN onboarding_sessions s ON s.id = p.session_id "
                    "WHERE p.session_id = :s"
                ),
                {"s": session_id},
            )
        ).one()
    assert plan_state == "stale"
    assert session_state == "stale"


@pytest.mark.anyio
async def test_applying_twice_creates_one_project_and_one_audit(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    client = harness.app.state.github_client
    actor = await _identity(engine)

    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    analysis = await service.analyze(engine, settings, client, session_id=session_id)
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
        plan_version=analysis["plan_version"],
        expected_version=version,
        actor_identity_id=actor,
    )

    first = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="apply-key-retry",
        actor_identity_id=actor,
    )
    second = await service.apply(
        engine,
        settings,
        client,
        session_id=session_id,
        plan_version=analysis["plan_version"],
        idempotency_key="apply-key-retry",
        actor_identity_id=actor,
    )
    assert first.outcome == "applied"
    # A client that lost the response repeats the call and gets the recorded
    # answer rather than a second project — the WHOLE recorded answer,
    # outcome word included. A replay is not a different operation.
    assert second.outcome == "applied"
    assert second.project_id == first.project_id

    async with engine.connect() as connection:
        counts = {
            table: int(
                (
                    await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                ).scalar_one()
            )
            for table in (
                "projects",
                "environments",
                "service_definitions",
                "onboarding_applies",
                "github_repository_projects",
            )
        }
    assert counts["projects"] == 1
    assert counts["environments"] == 1
    assert counts["service_definitions"] == 2
    assert counts["onboarding_applies"] == 1
    assert counts["github_repository_projects"] == 1


@pytest.mark.anyio
async def test_an_unapproved_or_blocked_plan_cannot_be_applied(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    # A manifest naming a cluster Drake does not have.
    manifest = datalake_manifest().replace("clusterRef: cluster-a", "clusterRef: ghost-cluster")
    row_id = await _bootstrap(harness, engine, fake, golden_tree(manifest))
    settings = harness.app.state.settings
    client = harness.app.state.github_client
    actor = await _identity(engine)

    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    analysis = await service.analyze(engine, settings, client, session_id=session_id)
    assert analysis["state"] == "needs_review"
    assert analysis["blocking_items"] >= 1

    async with engine.connect() as connection:
        version = int(
            (
                await connection.execute(
                    text("SELECT version FROM onboarding_sessions WHERE id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    with pytest.raises(OnboardingError) as blocked:
        await service.approve(
            engine,
            session_id=session_id,
            plan_version=analysis["plan_version"],
            expected_version=version,
            actor_identity_id=actor,
        )
    assert blocked.value.code == "plan_blocked"

    # And apply refuses on its own, without relying on approve having.
    with pytest.raises(OnboardingError) as unapproved:
        await service.apply(
            engine,
            settings,
            client,
            session_id=session_id,
            plan_version=analysis["plan_version"],
            idempotency_key="apply-key-blocked",
            actor_identity_id=actor,
        )
    assert unapproved.value.code == "not_approved"


@pytest.mark.anyio
async def test_a_gated_repository_produces_no_session_and_no_provider_call(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The Datalake gate, enforced before any network path."""
    harness, fake = github_harness(tmp_path)
    fake.branch_heads["Datalake-Platform-GUI"] = HEAD_SHA
    fake.trees["Datalake-Platform-GUI"] = golden_tree()
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(
                repositories=[
                    {
                        "id": DATALAKE_ID,
                        "node_id": "R_datalake",
                        "name": "Datalake-Platform-GUI",
                        "full_name": f"{OWNER}/Datalake-Platform-GUI",
                        "private": True,
                    }
                ]
            ),
            str(uuidlib.uuid4()),
        )
    row_id = await _row_id(engine, DATALAKE_ID)
    actor = await _identity(engine)

    before = len(fake.calls)
    with pytest.raises(OnboardingError) as refusal:
        await service.create_session(
            engine,
            harness.app.state.settings,
            repository_row_id=row_id,
            actor_identity_id=actor,
        )
    assert refusal.value.code == "security_gate_open"
    # Not one call, and not one token mint.
    assert len(fake.calls) == before
    assert not fake.token_requests

    async with engine.connect() as connection:
        sessions = int(
            (
                await connection.execute(text("SELECT count(*) FROM onboarding_sessions"))
            ).scalar_one()
        )
    assert sessions == 0


# ===========================================================================
# GitOps
# ===========================================================================


@pytest.mark.anyio
async def test_gitops_disabled_makes_no_request_and_no_provider_call(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)
    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(engine, settings, harness.app.state.github_client, session_id=session_id)

    assert settings.github_gitops_pr_enabled is False
    with pytest.raises(OnboardingError) as refusal:
        await gitops.request_pull_request(
            engine, settings, session_id=session_id, actor_identity_id=actor
        )
    assert refusal.value.code == "gitops_disabled"

    provider = gitops.RecordingProvider()
    assert await gitops.process_pending(engine, settings, provider) == 0
    assert provider.calls == []

    async with engine.connect() as connection:
        requests = int(
            (await connection.execute(text("SELECT count(*) FROM gitops_requests"))).scalar_one()
        )
    assert requests == 0


@pytest.mark.anyio
async def test_a_gitops_request_targets_only_the_allowlisted_path_and_branch(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings.model_copy(update={"github_gitops_pr_enabled": True})
    actor = await _identity(engine)
    created = await service.create_session(
        engine, harness.app.state.settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
    )

    first = await gitops.request_pull_request(
        engine, settings, session_id=session_id, actor_identity_id=actor
    )
    repeat = await gitops.request_pull_request(
        engine, settings, session_id=session_id, actor_identity_id=actor
    )
    assert first["created"] is True
    # A retried request does not open a second pull request.
    assert repeat["created"] is False and repeat["id"] == first["id"]

    provider = gitops.RecordingProvider(number=7)
    assert await gitops.process_pending(engine, settings, provider) == 1
    call = provider.calls[0]
    assert call["file_path"] == ".drake/project.yaml"
    assert call["head_branch"].startswith("drake/onboarding/")
    assert call["base_commit_sha"] == HEAD_SHA
    # The body is bounded, server-composed and credential-free.
    assert "://" not in call["body"]
    assert len(call["body"]) <= 2000

    async with engine.connect() as connection:
        state, number = (
            await connection.execute(text("SELECT state, provider_pr_number FROM gitops_requests"))
        ).one()
    assert state == "active"
    assert number == 7


@pytest.mark.anyio
async def test_a_provider_failure_never_shows_as_an_open_pull_request(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    base = harness.app.state.settings
    settings = base.model_copy(update={"github_gitops_pr_enabled": True})
    actor = await _identity(engine)
    created = await service.create_session(
        engine, base, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(engine, base, harness.app.state.github_client, session_id=session_id)
    await gitops.request_pull_request(
        engine, settings, session_id=session_id, actor_identity_id=actor
    )

    provider = gitops.RecordingProvider(fail_with="http_403")
    await gitops.process_pending(engine, settings, provider)

    async with engine.connect() as connection:
        state, number, error = (
            await connection.execute(
                text("SELECT state, provider_pr_number, error_code FROM gitops_requests")
            )
        ).one()
    assert state == "failed"
    assert number is None
    assert error == "http_403"


# ===========================================================================
# scope and authority
# ===========================================================================


@pytest.mark.anyio
async def test_a_caller_outside_scope_sees_no_session_and_no_count(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)
    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])

    from drake_api.rbac.service import Principal

    async with engine.begin() as connection:
        stranger = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO identities (issuer, subject, identity_type, "
                            "display_name, email) VALUES ('urn:test', :s, 'user', 'S', '') "
                            "RETURNING id"
                        ),
                        {"s": uuidlib.uuid4().hex},
                    )
                ).scalar_one()
            )
        )
    principal = Principal(identity_id=stranger, issuer="urn:test")

    async with engine.connect() as connection:
        listing = await onboarding_repo.list_sessions(connection, principal)
        detail = await onboarding_repo.get_session(connection, principal, session_id)
        findings = await onboarding_repo.session_findings(connection, principal, session_id)
        plan = await onboarding_repo.session_plan(connection, principal, session_id)
        evidence = await onboarding_repo.integration_evidence(connection, principal)

    # Not merely an empty list: the totals are zero too.
    assert listing["items"] == [] and listing["total"] == 0
    assert detail is None and findings is None and plan is None
    assert evidence["sessions"] == 0


@pytest.mark.anyio
async def test_the_permissions_are_separate_rights(engine: AsyncEngine, tmp_path: Path) -> None:
    """Viewing is not managing; approving is not applying; applying is not
    proposing a change to somebody's repository."""
    from drake_api.rbac.catalog import PERMISSIONS

    for permission in (
        "onboarding.view",
        "onboarding.manage",
        "onboarding.apply",
        "onboarding.gitops",
    ):
        assert permission in PERMISSIONS

    harness, fake = github_harness(tmp_path)
    await _bootstrap(harness, engine, fake, golden_tree())

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        # A session-holder with no grants sees no sessions and cannot create
        # one; the create answers 404 rather than confirming anything.
        listing = await client.get("/v1/onboarding/sessions")
        assert listing.status_code == 200
        assert listing.json()["total"] == 0
        me = await client.get("/v1/me")
        created = await client.post(
            "/v1/onboarding/sessions",
            json={"repository_id": str(uuidlib.uuid4())},
            headers={"X-CSRF-Token": me.json()["csrf_token"], "Origin": harness.client_base_url},
        )
    assert created.status_code == 404


@pytest.mark.anyio
async def test_no_read_response_carries_repository_content_or_a_url(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    settings = harness.app.state.settings
    actor = await _identity(engine)
    created = await service.create_session(
        engine, settings, repository_row_id=row_id, actor_identity_id=actor
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(engine, settings, harness.app.state.github_client, session_id=session_id)

    from harness_s1 import grant_platform_owner

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        await grant_platform_owner(engine, harness.provider.issuer, "user-owner")
        payloads = [
            (await client.get("/v1/onboarding/sessions")).text,
            (await client.get(f"/v1/onboarding/sessions/{session_id}")).text,
            (await client.get(f"/v1/onboarding/sessions/{session_id}/findings")).text,
            (await client.get(f"/v1/onboarding/sessions/{session_id}/plan")).text,
            (await client.get("/v1/onboarding/github/status")).text,
        ]

    for body in payloads:
        assert "://" not in body
        assert "PLACEHOLDER" not in body
        assert "ghs_" not in body
        assert "-----BEGIN" not in body
        assert "apiVersion" not in body
        lowered = body.lower()
        assert "authorization" not in lowered
        assert "private_key" not in lowered
