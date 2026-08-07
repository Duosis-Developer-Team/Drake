"""Catalog onboarding: static scan, manifest review, atomic import.

Sprint 5A's `onboarding_state` describes the GitHub projection. This module
owns the separate question of whether a repository has been imported into
Drake's project catalog, because the two fail for different reasons and
conflating them would make "we cannot see the repository" and "nobody has
onboarded it yet" indistinguishable.

ADR-0007 is the rule that shapes everything here: the repository is the
source of intent. A manifest Drake generated, or one edited in a browser,
can be validated and downloaded — but only a manifest that exists in the
repository at a known commit may be imported.
"""

import datetime as dt
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.catalog.service import CatalogService
from drake_api.github_app import catalog, manifest, scanner
from drake_api.github_app.auth import missing_permissions
from drake_api.github_app.client import GitHubClient, GitHubError, error_code

logger = logging.getLogger(__name__)

MANIFEST_PATH = manifest.MANIFEST_PATH
SOURCE_KIND = "manifest"

# Only what the scan actually needs. Contents:read is the Sprint 5B
# addition and the only reason this token can read files at all.
SCAN_PERMISSIONS: dict[str, str] = {"metadata": "read", "contents": "read"}


class OnboardingError(RuntimeError):
    """A refusal with a stable, secret-free code."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class RepositoryContext:
    """Everything a scan or import must check BEFORE touching the network."""

    row_id: uuid.UUID
    scope_id: uuid.UUID
    external_id: int
    installation_external_id: int
    owner: str
    name: str
    full_name: str
    default_branch: str
    security_gate: str | None
    access_state: str
    onboarding_state: str
    reconciliation_state: str
    node_id: str


async def load_repository_context(
    connection: AsyncConnection, repository_row_id: uuid.UUID
) -> RepositoryContext:
    row = (
        await connection.execute(
            text(
                "SELECT r.id, r.scope_id, r.external_id, i.external_id, r.owner_login, r.name, "
                "r.full_name, r.default_branch, r.security_gate, r.access_state, "
                "r.onboarding_state, r.reconciliation_state, r.node_id "
                "FROM github_repositories r "
                "JOIN github_installations i ON i.id = r.installation_id "
                "WHERE r.id = :id"
            ),
            {"id": repository_row_id},
        )
    ).one()
    return RepositoryContext(
        row_id=uuid.UUID(str(row[0])),
        scope_id=uuid.UUID(str(row[1])),
        external_id=int(row[2]),
        installation_external_id=int(row[3]),
        owner=str(row[4]),
        name=str(row[5]),
        full_name=str(row[6]),
        default_branch=str(row[7]),
        security_gate=str(row[8]) if row[8] else None,
        access_state=str(row[9]),
        onboarding_state=str(row[10]),
        reconciliation_state=str(row[11] or "never"),
        node_id=str(row[12] or ""),
    )


def assert_scannable(context: RepositoryContext) -> None:
    """Every reason to refuse, checked before a token is even looked up.

    Order matters: the security gate is first because a gated repository
    must produce zero provider calls and zero token mints, and anything
    that ran before this check would already have broken that.
    """
    if context.security_gate:
        raise OnboardingError(
            "security_gate_open",
            "This repository is closed by a manual security gate.",
        )
    if context.access_state != "accessible":
        raise OnboardingError(
            "repository_not_accessible",
            "Drake does not currently have access to this repository.",
        )
    if context.reconciliation_state != "complete":
        raise OnboardingError(
            "reconciliation_incomplete",
            "The repository projection is not complete; reconcile it first.",
        )


# --- draft persistence ---------------------------------------------------


async def load_draft(
    connection: AsyncConnection, repository_row_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await connection.execute(
            text(
                "SELECT id, state, commit_sha, manifest_source, manifest_digest, discovery, "
                "findings, draft_manifest, reason_code, accepted_project_id, accepted_at, "
                "scanned_at, revision FROM github_onboarding_drafts WHERE repository_id = :id"
            ),
            {"id": repository_row_id},
        )
    ).first()
    if row is None:
        return None
    discovery = row[5] if isinstance(row[5], dict) else json.loads(str(row[5] or "{}"))
    findings = row[6] if isinstance(row[6], list) else json.loads(str(row[6] or "[]"))
    return {
        "id": str(row[0]),
        "state": str(row[1]),
        "commit_sha": str(row[2] or ""),
        "manifest_source": str(row[3]),
        "manifest_digest": str(row[4] or ""),
        "discovery": discovery,
        "findings": findings,
        "draft_manifest": row[7],
        "reason_code": row[8],
        "accepted_project_id": str(row[9]) if row[9] else None,
        "accepted_at": row[10].isoformat() if row[10] else None,
        "scanned_at": row[11].isoformat() if row[11] else None,
        "revision": int(row[12]),
    }


async def save_draft(
    connection: AsyncConnection,
    *,
    repository_row_id: uuid.UUID,
    scope_id: uuid.UUID,
    state: str,
    commit_sha: str,
    manifest_source: str,
    manifest_digest: str,
    discovery: dict[str, Any],
    findings: list[dict[str, str]],
    draft_manifest: str | None,
    reason_code: str | None,
) -> uuid.UUID:
    """Write the single live draft for a repository.

    A rescan replaces what it found. Keeping every historical scan would
    accumulate rows nobody reads while making "what does Drake currently
    believe" ambiguous.
    """
    row = (
        await connection.execute(
            text(
                """
                INSERT INTO github_onboarding_drafts
                    (repository_id, scope_id, state, commit_sha, manifest_source,
                     manifest_digest, discovery, findings, draft_manifest, reason_code,
                     scanned_at)
                VALUES (:repository_id, :scope_id, :state, :commit_sha, :manifest_source,
                        :manifest_digest, CAST(:discovery AS jsonb), CAST(:findings AS jsonb),
                        :draft_manifest, :reason_code, now())
                ON CONFLICT (repository_id) DO UPDATE SET
                    state = EXCLUDED.state,
                    commit_sha = EXCLUDED.commit_sha,
                    manifest_source = EXCLUDED.manifest_source,
                    manifest_digest = EXCLUDED.manifest_digest,
                    discovery = EXCLUDED.discovery,
                    findings = EXCLUDED.findings,
                    draft_manifest = EXCLUDED.draft_manifest,
                    reason_code = EXCLUDED.reason_code,
                    scanned_at = now(),
                    revision = github_onboarding_drafts.revision + 1,
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "repository_id": repository_row_id,
                "scope_id": scope_id,
                "state": state,
                "commit_sha": commit_sha,
                "manifest_source": manifest_source,
                "manifest_digest": manifest_digest,
                "discovery": json.dumps(discovery),
                "findings": json.dumps(findings[:50]),
                "draft_manifest": draft_manifest,
                "reason_code": reason_code,
            },
        )
    ).one()
    return uuid.UUID(str(row[0]))


def manifest_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# --- scanning ------------------------------------------------------------


class OnboardingScanner:
    """Drives one static scan and records its bounded result."""

    def __init__(self, engine: AsyncEngine, client: GitHubClient) -> None:
        self._engine = engine
        self._client = client

    async def scan(self, repository_row_id: uuid.UUID) -> dict[str, Any]:
        async with self._engine.connect() as connection:
            context = await load_repository_context(connection, repository_row_id)
        assert_scannable(context)

        # Belt and braces: the catalogue gate is derived from the CURRENT
        # name too, so a repository renamed into a gated name is refused
        # here as well as by its persisted gate.
        if catalog.security_gate_for(context.full_name):
            raise OnboardingError(
                "security_gate_open", "This repository is closed by a manual security gate."
            )

        token = await self._client.installation_token(
            context.installation_external_id,
            repository_ids=[context.external_id],
            permissions=dict(SCAN_PERMISSIONS),
        )
        shortfall = missing_permissions(token.permissions, dict(SCAN_PERMISSIONS))
        if shortfall:
            raise OnboardingError(
                "permission_missing",
                "The installation has not granted the read permissions a scan needs: "
                + ", ".join(shortfall),
            )

        result = await scanner.RepositoryScanner(self._client).scan(
            token, context.owner, context.name, context.default_branch
        )
        return await self._record(context, result)

    async def _record(
        self, context: RepositoryContext, result: scanner.ScanResult
    ) -> dict[str, Any]:
        findings: list[dict[str, str]] = list(result.warnings)
        draft_text: str | None = None
        digest = ""

        if result.manifest_found and result.manifest_text is not None:
            validation = manifest.validate_content(result.manifest_text)
            findings.extend(finding.as_json() for finding in validation.findings)
            if validation.valid and validation.document is not None:
                identity = manifest.check_repository_identity(
                    validation.document, context.owner, context.name, context.default_branch
                )
                findings.extend(finding.as_json() for finding in identity)
                if identity:
                    state, source, reason = "invalid", "repository", "repository_identity"
                else:
                    state, source, reason = "ready_to_import", "repository", None
                    digest = manifest_digest(result.manifest_text)
            else:
                # An invalid manifest body is NOT stored: it came from a
                # repository and may contain the very credential the policy
                # rejected it for. The findings say what was wrong.
                state, source, reason = "invalid", "repository", "manifest_invalid"
        else:
            draft_text = scanner.generate_draft_manifest(
                context.owner, context.name, context.default_branch, result
            )
            state, source, reason = "needs_input", "operator_draft", "manifest_absent"

        if result.truncated:
            # An incomplete scan is never a green light.
            state = "needs_input" if state != "invalid" else state
            reason = reason or "scan_truncated"

        async with self._engine.begin() as connection:
            await save_draft(
                connection,
                repository_row_id=context.row_id,
                scope_id=context.scope_id,
                state=state,
                commit_sha=result.commit_sha,
                manifest_source=source,
                manifest_digest=digest,
                discovery=result.as_json(),
                findings=findings,
                draft_manifest=draft_text,
                reason_code=reason,
            )
            draft = await load_draft(connection, context.row_id)
        assert draft is not None
        return draft


# --- import --------------------------------------------------------------


@dataclass(frozen=True)
class ImportOutcome:
    project_id: uuid.UUID
    project_key: str
    created: bool


class CatalogImporter:
    """Turns one reviewed repository manifest into catalog rows, atomically."""

    def __init__(self, engine: AsyncEngine, client: GitHubClient) -> None:
        self._engine = engine
        self._client = client

    async def import_repository(
        self, repository_row_id: uuid.UUID, actor_identity_id: uuid.UUID
    ) -> ImportOutcome:
        async with self._engine.connect() as connection:
            context = await load_repository_context(connection, repository_row_id)
            draft = await load_draft(connection, repository_row_id)
            existing = await self._existing_binding(connection, repository_row_id)

        if existing is not None:
            # Already imported. Repeating the request returns the same
            # answer instead of creating a second project.
            return ImportOutcome(existing[0], existing[1], created=False)

        assert_scannable(context)
        if draft is None or draft["state"] != "ready_to_import":
            raise OnboardingError(
                "draft_not_ready", "Run a scan that finds a valid repository manifest first."
            )
        if draft["manifest_source"] != "repository":
            # ADR-0007: a generated or browser-edited manifest is not intent
            # the repository has expressed.
            raise OnboardingError(
                "manifest_not_in_repository",
                "Commit the manifest to the repository and scan again before importing.",
            )

        token = await self._client.installation_token(
            context.installation_external_id,
            repository_ids=[context.external_id],
            permissions=dict(SCAN_PERMISSIONS),
        )
        # Re-resolve the branch: the review may have taken a while, and
        # importing a commit that is no longer HEAD would silently onboard
        # something nobody looked at.
        head = await self._client.resolve_branch_head(
            token, context.owner, context.name, context.default_branch
        )
        if head != draft["commit_sha"]:
            raise OnboardingError(
                "rescan_required",
                "The default branch moved after this scan. Scan again and review the new commit.",
            )

        entry = await self._client.get_content(
            token, context.owner, context.name, MANIFEST_PATH, draft["commit_sha"]
        )
        content = scanner._decode(entry, scanner.DEFAULT_BUDGET)
        if content is None:
            raise OnboardingError("manifest_unreadable", "The manifest could not be read.")
        if manifest_digest(content) != draft["manifest_digest"]:
            raise OnboardingError(
                "rescan_required", "The manifest changed after this scan. Scan again."
            )

        validation = manifest.validate_content(content)
        if not validation.valid or validation.document is None:
            raise OnboardingError("manifest_invalid", "The manifest is no longer valid.")
        identity = manifest.check_repository_identity(
            validation.document, context.owner, context.name, context.default_branch
        )
        if identity:
            raise OnboardingError(
                "repository_identity",
                "The manifest does not describe the repository it was read from.",
            )

        return await self._materialise(context, draft, validation.document, actor_identity_id)

    @staticmethod
    async def _existing_binding(
        connection: AsyncConnection, repository_row_id: uuid.UUID
    ) -> tuple[uuid.UUID, str] | None:
        row = (
            await connection.execute(
                text(
                    "SELECT p.id, p.project_key FROM github_repository_projects b "
                    "JOIN projects p ON p.id = b.project_id WHERE b.repository_id = :id"
                ),
                {"id": repository_row_id},
            )
        ).first()
        return (uuid.UUID(str(row[0])), str(row[1])) if row is not None else None

    async def _materialise(
        self,
        context: RepositoryContext,
        draft: dict[str, Any],
        document: dict[str, Any],
        actor_identity_id: uuid.UUID,
    ) -> ImportOutcome:
        """One transaction. Any failure leaves no catalog rows behind."""
        spec = document["spec"]
        metadata = document["metadata"]
        project_key = str(metadata["name"])
        source_ref = f"github:{context.external_id}:{MANIFEST_PATH}"
        revision = str(draft["commit_sha"])

        async with self._engine.begin() as connection:
            catalog_service = CatalogService(connection)

            existing = (
                await connection.execute(
                    text("SELECT id FROM projects WHERE project_key = :key"),
                    {"key": project_key},
                )
            ).first()
            if existing is not None:
                raise OnboardingError(
                    "project_key_taken",
                    f"A project named '{project_key}' already exists in the catalog.",
                )

            project = await catalog_service.create_project(
                project_key,
                str(metadata.get("displayName") or project_key),
                repo_provider=str(spec["repository"]["provider"]),
                repo_owner=str(spec["repository"]["owner"]),
                repo_name=str(spec["repository"]["name"]),
                default_branch=str(spec["repository"].get("defaultBranch") or ""),
                criticality=max(
                    (str(env["criticality"]) for env in spec["environments"]),
                    key=["low", "medium", "high", "critical"].index,
                ),
                tenant_model=str(spec["tenantModel"]["mode"]),
                owners=[
                    (str(owner["team"]), str(owner.get("role") or "primary"))
                    for owner in spec["owners"]
                ],
                source_ref=source_ref,
                source_revision=revision,
            )

            for environment in spec["environments"]:
                cluster_id = None
                if str(environment["runtime"]) == "kubernetes":
                    cluster_ref = str(environment["clusterRef"])
                    cluster_row = (
                        await connection.execute(
                            text("SELECT id FROM clusters WHERE cluster_ref = :ref"),
                            {"ref": cluster_ref},
                        )
                    ).first()
                    if cluster_row is None:
                        # A manifest naming a cluster does not conjure one.
                        # Clusters are operator-registered infrastructure.
                        raise OnboardingError(
                            "unknown_cluster",
                            f"The manifest references cluster '{cluster_ref}', "
                            "which is not registered in Drake.",
                        )
                    cluster_id = uuid.UUID(str(cluster_row[0]))

                environment_entity = await catalog_service.create_environment(
                    project.id,
                    str(environment["name"]),
                    runtime=str(environment["runtime"]),
                    branch=str(environment.get("branch") or ""),
                    criticality=str(environment["criticality"]),
                    cluster_id=cluster_id,
                    namespace=environment.get("namespace"),
                    source_ref=source_ref,
                    source_revision=revision,
                )

                for service_spec in spec["services"]:
                    service_key = str(service_spec["name"])
                    row = (
                        await connection.execute(
                            text(
                                "SELECT id FROM service_definitions "
                                "WHERE project_id = :project_id AND service_key = :key"
                            ),
                            {"project_id": project.id, "key": service_key},
                        )
                    ).first()
                    if row is not None:
                        service_id = uuid.UUID(str(row[0]))
                    else:
                        service_id = await catalog_service.create_service_definition(
                            project.id,
                            service_key,
                            component=str(service_spec["component"]),
                            runtime=str(service_spec["runtime"]),
                            metrics_profile=str(service_spec["metricsProfile"]),
                            workload_selector=service_spec.get("workloadSelector") or {},
                            health=service_spec.get("health") or {},
                            source_ref=source_ref,
                            source_revision=revision,
                        )
                    await catalog_service.bind_service(environment_entity.id, service_id)

            # Honest placeholders: the project exists in the catalog, but
            # nothing about it is wired up yet.
            for integration_type in ("prometheus", "github", "cluster-agent", "backup-reporter"):
                await catalog_service.register_integration(integration_type, project.scope_id)

            await connection.execute(
                text(
                    "INSERT INTO github_repository_projects "
                    "(repository_id, project_id, scope_id, commit_sha, manifest_digest) "
                    "VALUES (:repository_id, :project_id, :scope_id, :commit_sha, :digest)"
                ),
                {
                    "repository_id": context.row_id,
                    "project_id": project.id,
                    "scope_id": context.scope_id,
                    "commit_sha": revision,
                    "digest": str(draft["manifest_digest"]),
                },
            )
            await connection.execute(
                text(
                    "UPDATE github_onboarding_drafts SET state = 'imported', "
                    "accepted_project_id = :project_id, accepted_at = now(), "
                    "accepted_by = :actor, reason_code = NULL, "
                    "revision = revision + 1, updated_at = now() "
                    "WHERE repository_id = :repository_id"
                ),
                {
                    "project_id": project.id,
                    "actor": actor_identity_id,
                    "repository_id": context.row_id,
                },
            )

        return ImportOutcome(project.id, project_key, created=True)


def provider_failure(error: GitHubError) -> OnboardingError:
    """Turn a provider failure into an honest, never-successful outcome."""
    code = error_code(error)
    retryable = scanner.error_is_retryable(error)
    return OnboardingError(
        code,
        "GitHub could not be read right now; the scan did not complete."
        if retryable
        else "GitHub refused the request.",
        status=503 if retryable else 502,
    )


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
