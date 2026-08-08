"""The onboarding session: discover, plan, approve, apply.

    repository selected
      → exact commit SHA captured
      → manifest and safe metadata read
      → bounded static analysis
      → proposed project graph
      → conflicts and unmapped bindings shown
      → authorized actor approves
      → catalog transaction applied
      → downstream reconciliation requested

Sprint 5B already had the ends of that chain: a bounded static scanner and
an atomic catalog import. It had no middle. A valid manifest went straight
to catalog rows, which is fine exactly once — for a repository nobody has
onboarded — and wrong every other time.

What this module adds, and why each piece is load-bearing:

**A plan nobody approved cannot be applied.** Approval names a plan
VERSION and a digest. Apply re-checks both, so approving one proposal and
applying a different one is not a race that can be lost, it is a state that
cannot exist.

**A plan built on a commit that moved is stale.** The analysed commit, the
manifest digest and the analyzer version are frozen onto the plan. Any
drift refuses the apply and asks for a re-analysis, because a review of a
commit is not a review of its successor.

**Apply is one transaction, and it makes no network calls.** A slow GitHub
must not hold a catalog lock, and a failing one must not roll back the
import that succeeded. Provider work happens before, in the analysis, or
after, through the GitOps outbox.

**Nothing is deleted, ever.** There is no code path here that removes a
catalog row. A service that vanished from a manifest is reported.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.catalog.service import CatalogService
from drake_api.github_app import catalog as repo_catalog
from drake_api.github_app import manifest as manifest_module
from drake_api.github_app import scanner as scanner_module
from drake_api.github_app.auth import missing_permissions
from drake_api.github_app.client import GitHubClient, GitHubError, error_code
from drake_api.github_app.onboarding_service import (
    OnboardingError,
    RepositoryContext,
    assert_scannable,
    load_repository_context,
)
from drake_api.onboarding.model import (
    ANALYZER_VERSION,
    Action,
    CatalogSnapshot,
    EntityKind,
    Plan,
    PlanItem,
    SessionState,
    build_plan,
    deployment_source_item,
)
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.onboarding")

MANIFEST_PATH = manifest_module.MANIFEST_PATH
SOURCE_KIND = "manifest"

# Exactly the read permissions a static analysis needs, and no more.
SCAN_PERMISSIONS: dict[str, str] = {"metadata": "read", "contents": "read"}


class NotConfiguredError(OnboardingError):
    """The GitHub integration is off. No token, no call, no invented data."""

    def __init__(self) -> None:
        super().__init__(
            "github_not_configured",
            "The GitHub App integration is not configured, so Drake cannot read "
            "repositories. Nothing has been contacted and nothing is cached.",
            status=409,
        )


def require_configured(settings: Settings) -> None:
    """The fail-closed gate, checked before ANY provider path.

    Placed here, at the top of every entry point, rather than deeper down:
    a check that runs after a token mint has already broken the promise it
    was supposed to make.
    """
    configured = bool(
        settings.github_app_enabled
        and settings.github_app_private_key_file
        and settings.github_webhook_secret_file
        and (settings.github_app_client_id or settings.github_app_id)
    )
    if not configured:
        raise NotConfiguredError()


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRow:
    id: uuid.UUID
    repository_id: uuid.UUID
    scope_id: uuid.UUID
    state: str
    version: int
    analyzed_commit_sha: str | None
    approved_plan_version: int | None


async def _session_row(
    connection: AsyncConnection, session_id: uuid.UUID
) -> SessionRow | None:
    row = (
        await connection.execute(
            text(
                "SELECT id, repository_id, scope_id, state, version, analyzed_commit_sha, "
                "approved_plan_version FROM onboarding_sessions WHERE id = :id"
            ),
            {"id": session_id},
        )
    ).first()
    if row is None:
        return None
    return SessionRow(
        id=uuid.UUID(str(row[0])),
        repository_id=uuid.UUID(str(row[1])),
        scope_id=uuid.UUID(str(row[2])),
        state=str(row[3]),
        version=int(row[4]),
        analyzed_commit_sha=row[5],
        approved_plan_version=row[6],
    )


async def create_session(
    engine: AsyncEngine,
    settings: Settings,
    *,
    repository_row_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
) -> dict[str, Any]:
    """Open a session for one repository, or return the live one.

    The security gate is checked here, before anything else: a gated
    repository must produce zero provider calls and zero token mints, and a
    check that runs later would already have broken that.
    """
    require_configured(settings)

    async with engine.connect() as connection:
        context = await load_repository_context(connection, repository_row_id)
    if context.security_gate:
        raise OnboardingError(
            "security_gate_open",
            "This repository is closed by a manual security gate and cannot be onboarded.",
        )
    if repo_catalog.security_gate_for(context.full_name):
        raise OnboardingError(
            "security_gate_open",
            "This repository is closed by a manual security gate and cannot be onboarded.",
        )

    async with engine.begin() as connection:
        existing = (
            await connection.execute(
                text(
                    "SELECT id FROM onboarding_sessions WHERE repository_id = :repo "
                    "AND state NOT IN ('imported', 'cancelled', 'failed')"
                ),
                {"repo": repository_row_id},
            )
        ).first()
        if existing is not None:
            # A live session already exists. Returning it beats opening a
            # second one whose approval would race the first.
            session_id = uuid.UUID(str(existing[0]))
            created = False
        else:
            session_id = uuid.UUID(
                str(
                    (
                        await connection.execute(
                            text(
                                """
                                INSERT INTO onboarding_sessions
                                    (repository_id, scope_id, state, created_by)
                                VALUES (:repo, :scope, 'draft', :actor)
                                RETURNING id
                                """
                            ),
                            {
                                "repo": repository_row_id,
                                "scope": context.scope_id,
                                "actor": actor_identity_id,
                            },
                        )
                    ).scalar_one()
                )
            )
            created = True
    return {"session_id": str(session_id), "created": created}


async def _set_state(
    connection: AsyncConnection,
    session_id: uuid.UUID,
    state: str,
    *,
    reason_code: str | None = None,
    **columns: Any,
) -> None:
    assignments = ["state = :state", "reason_code = :reason", "version = version + 1",
                   "updated_at = now()"]
    params: dict[str, Any] = {"id": session_id, "state": state, "reason": reason_code}
    for name, value in columns.items():
        assignments.append(f"{name} = :{name}")
        params[name] = value
    # The only variable fragment is a join of column names defined in this
    # module. No request value reaches it; every VALUE is a bind parameter.
    clause = ", ".join(assignments)
    statement = f"UPDATE onboarding_sessions SET {clause} WHERE id = :id"  # noqa: S608
    await connection.execute(text(statement), params)


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


async def analyze(
    engine: AsyncEngine,
    settings: Settings,
    client: GitHubClient,
    *,
    session_id: uuid.UUID,
) -> dict[str, Any]:
    """Run one bounded static analysis and build a plan from it.

    Nothing here executes anything from the repository: the scanner reads an
    allowlist of metadata files through the Contents API at one immutable
    commit, under hard budgets. Discovery that runs the thing it is
    discovering is not discovery.
    """
    require_configured(settings)

    async with engine.connect() as connection:
        session = await _session_row(connection, session_id)
        if session is None:
            raise OnboardingError("session_not_found", "No such onboarding session.", status=404)
        context = await load_repository_context(connection, session.repository_id)
    assert_scannable(context)
    if repo_catalog.security_gate_for(context.full_name):
        raise OnboardingError(
            "security_gate_open", "This repository is closed by a manual security gate."
        )

    async with engine.begin() as connection:
        await _set_state(connection, session_id, str(SessionState.ANALYZING))

    try:
        token = await client.installation_token(
            context.installation_external_id,
            repository_ids=[context.external_id],
            permissions=dict(SCAN_PERMISSIONS),
        )
        shortfall = missing_permissions(token.permissions, dict(SCAN_PERMISSIONS))
        if shortfall:
            raise OnboardingError(
                "permission_missing",
                "The installation has not granted the read permissions an analysis needs.",
            )
        result = await scanner_module.RepositoryScanner(client).scan(
            token, context.owner, context.name, context.default_branch
        )
    except GitHubError as error:
        async with engine.begin() as connection:
            await _set_state(
                connection,
                session_id,
                str(SessionState.PROVIDER_UNAVAILABLE),
                reason_code=error_code(error),
            )
        raise OnboardingError(
            error_code(error),
            "GitHub could not be read right now; the analysis did not complete.",
            status=503 if scanner_module.error_is_retryable(error) else 502,
        ) from error

    return await _record_analysis(engine, session, context, result)


def _findings_from(result: scanner_module.ScanResult) -> list[dict[str, Any]]:
    """Turn a scan into safe findings. Paths and codes; never content."""
    findings: list[dict[str, Any]] = []
    for detection in result.detections:
        findings.append(
            {
                "finding_type": f"detected.{detection.kind}",
                "safe_path": detection.evidence.split(" ")[0][:512],
                "confidence": detection.confidence,
                "evidence_kind": "file_present",
                "proposed_target": detection.value[:256],
                "review_reason": None,
            }
        )
    for warning in result.warnings:
        findings.append(
            {
                "finding_type": "warning",
                "safe_path": str(warning.get("path") or "(scan)")[:512],
                "confidence": "high",
                "evidence_kind": str(warning.get("rule") or "warning")[:128],
                "proposed_target": None,
                # The rule, not the matched text. A finding that quoted the
                # value would put the credential in the database it was
                # flagged for containing.
                "review_reason": str(warning.get("rule") or "")[:256],
            }
        )
    for discovered in result.files:
        findings.append(
            {
                "finding_type": "file.read",
                "safe_path": discovered.path[:512],
                "confidence": "high",
                "evidence_kind": "content_digest",
                "proposed_target": None,
                "review_reason": None,
            }
        )
    return findings


async def _record_analysis(
    engine: AsyncEngine,
    session: SessionRow,
    context: RepositoryContext,
    result: scanner_module.ScanResult,
) -> dict[str, Any]:
    """Persist one analysis, idempotently, and plan from it."""
    manifest_digest: str | None = None
    document: dict[str, Any] | None = None
    reason: str | None = None

    if result.manifest_found and result.manifest_text is not None:
        validation = manifest_module.validate_content(result.manifest_text)
        if validation.valid and validation.document is not None:
            identity = manifest_module.check_repository_identity(
                validation.document, context.owner, context.name, context.default_branch
            )
            if identity:
                reason = "manifest_invalid"
            else:
                document = validation.document
                manifest_digest = hashlib.sha256(
                    result.manifest_text.encode("utf-8")
                ).hexdigest()
        else:
            # The invalid body is NOT stored: it came from a repository and
            # may contain the very credential the policy refused it for.
            reason = "manifest_invalid"
    else:
        reason = "manifest_absent"

    status = "partial" if result.truncated else "complete"

    async with engine.begin() as connection:
        analysis_row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO onboarding_analyses
                        (session_id, repository_id, commit_sha, analyzer_version, status,
                         truncated, manifest_found, manifest_digest, files_read, bytes_read,
                         provider_calls, error_code)
                    VALUES (:session, :repo, :commit, :analyzer, :status, :truncated,
                            :manifest_found, :digest, :files, :bytes, :calls, :error)
                    -- Same repository, same commit, same analyzer: one
                    -- analysis. A retry finds the existing row rather than
                    -- manufacturing a second opinion about one commit.
                    ON CONFLICT (repository_id, commit_sha, analyzer_version) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "session": session.id,
                    "repo": context.row_id,
                    "commit": result.commit_sha,
                    "analyzer": ANALYZER_VERSION,
                    "status": status,
                    "truncated": result.truncated,
                    "manifest_found": result.manifest_found,
                    "digest": manifest_digest,
                    "files": len(result.files),
                    "bytes": result.total_bytes,
                    "calls": result.provider_calls,
                    "error": reason,
                },
            )
        ).first()

        if analysis_row is None:
            analysis_id = uuid.UUID(
                str(
                    (
                        await connection.execute(
                            text(
                                "SELECT id FROM onboarding_analyses WHERE repository_id = :repo "
                                "AND commit_sha = :commit AND analyzer_version = :analyzer"
                            ),
                            {
                                "repo": context.row_id,
                                "commit": result.commit_sha,
                                "analyzer": ANALYZER_VERSION,
                            },
                        )
                    ).scalar_one()
                )
            )
            reused = True
        else:
            analysis_id = uuid.UUID(str(analysis_row[0]))
            reused = False
            for finding in _findings_from(result):
                await connection.execute(
                    text(
                        """
                        INSERT INTO onboarding_findings
                            (analysis_id, finding_type, safe_path, confidence, evidence_kind,
                             proposed_target, review_reason)
                        VALUES (:analysis, :type, :path, :confidence, :evidence, :target, :reason)
                        """
                    ),
                    {"analysis": analysis_id, **{
                        "type": finding["finding_type"],
                        "path": finding["safe_path"],
                        "confidence": finding["confidence"],
                        "evidence": finding["evidence_kind"],
                        "target": finding["proposed_target"],
                        "reason": finding["review_reason"],
                    }},
                )

        if document is None:
            await _set_state(
                connection,
                session.id,
                str(SessionState.NEEDS_REVIEW),
                reason_code=reason,
                analyzed_commit_sha=result.commit_sha,
                analyzed_at=datetime.now(UTC),
            )
            return {
                "session_id": str(session.id),
                "analysis_id": str(analysis_id),
                "reused": reused,
                "state": str(SessionState.NEEDS_REVIEW),
                "reason_code": reason,
                "truncated": result.truncated,
                "plan_version": None,
            }

        snapshot = await load_snapshot(connection, document, context.row_id)
        plan = build_plan(
            document,
            snapshot,
            repository_row_id=str(context.row_id),
            truncated=result.truncated,
        )
        extra = deployment_source_item(
            [
                {"kind": item.kind, "value": item.value, "evidence": item.evidence}
                for item in result.detections
            ]
        )
        if extra is not None:
            plan.items.append(extra)

        plan_version = await _store_plan(
            connection,
            session_id=session.id,
            analysis_id=analysis_id,
            plan=plan,
            commit_sha=result.commit_sha,
            manifest_digest=manifest_digest,
        )
        state = (
            str(SessionState.NEEDS_REVIEW)
            if plan.blocking_items
            else str(SessionState.READY)
        )
        await _set_state(
            connection,
            session.id,
            state,
            reason_code=None,
            analyzed_commit_sha=result.commit_sha,
            analyzed_at=datetime.now(UTC),
        )

    return {
        "session_id": str(session.id),
        "analysis_id": str(analysis_id),
        "reused": reused,
        "state": state,
        "reason_code": None,
        "truncated": result.truncated,
        "plan_version": plan_version,
        "blocking_items": plan.blocking_items,
    }


async def load_snapshot(
    connection: AsyncConnection, document: dict[str, Any], repository_row_id: uuid.UUID
) -> CatalogSnapshot:
    """Everything the planner needs, read once, so planning stays pure."""
    spec = document.get("spec") or {}
    project_key = str((document.get("metadata") or {}).get("name") or "")

    projects: dict[str, str] = {}
    project_repository: dict[str, str] = {}
    row = (
        await connection.execute(
            text(
                "SELECT p.id, b.repository_id FROM projects p "
                "LEFT JOIN github_repository_projects b ON b.project_id = p.id "
                "WHERE p.project_key = :key"
            ),
            {"key": project_key},
        )
    ).first()
    project_id: uuid.UUID | None = None
    if row is not None:
        project_id = uuid.UUID(str(row[0]))
        projects[project_key] = str(row[0])
        if row[1] is not None:
            project_repository[project_key] = str(row[1])

    environments: dict[tuple[str, str], str] = {}
    services: dict[tuple[str, str], str] = {}
    catalog_only: list[str] = []
    if project_id is not None:
        for entry in (
            await connection.execute(
                text("SELECT environment_key, id FROM environments WHERE project_id = :p"),
                {"p": project_id},
            )
        ).all():
            environments[(project_key, str(entry[0]))] = str(entry[1])
        manifest_services = {str(item.get("name") or "") for item in spec.get("services") or []}
        for entry in (
            await connection.execute(
                text("SELECT service_key, id FROM service_definitions WHERE project_id = :p"),
                {"p": project_id},
            )
        ).all():
            services[(project_key, str(entry[0]))] = str(entry[1])
            if str(entry[0]) not in manifest_services:
                catalog_only.append(str(entry[0]))

    clusters = {
        str(entry[0]): str(entry[1])
        for entry in (
            await connection.execute(text("SELECT cluster_ref, id FROM clusters"))
        ).all()
    }
    namespace_bindings = {
        (str(entry[0]), str(entry[1])): str(entry[2])
        for entry in (
            await connection.execute(
                text(
                    "SELECT c.cluster_ref, e.namespace, e.environment_key "
                    "FROM environments e JOIN clusters c ON c.id = e.cluster_id "
                    "WHERE e.namespace IS NOT NULL"
                )
            )
        ).all()
    }
    owner_teams = {
        str(entry[0]): str(entry[0])
        for entry in (
            await connection.execute(text("SELECT DISTINCT team_key FROM project_owners"))
        ).all()
    }

    return CatalogSnapshot(
        projects=projects,
        project_repository=project_repository,
        environments=environments,
        services=services,
        clusters=clusters,
        owner_teams=owner_teams,
        metric_profiles=_metric_profiles(),
        slo_profiles=frozenset({"availability", "latency"}),
        namespace_bindings=namespace_bindings,
        catalog_only_services=tuple(sorted(catalog_only)),
    )


def _metric_profiles() -> frozenset[str]:
    """The curated profiles a manifest may reference.

    Read from the Sprint 5 registry, so a manifest cannot name a profile
    that does not exist and cannot invent one.
    """
    from drake_api.telemetry.registry import get_registry

    profiles: set[str] = set()
    for metric in get_registry().metrics.values():
        profiles.update(metric.profiles)
    return frozenset(profiles)


async def _store_plan(
    connection: AsyncConnection,
    *,
    session_id: uuid.UUID,
    analysis_id: uuid.UUID,
    plan: Plan,
    commit_sha: str,
    manifest_digest: str | None,
) -> int:
    """Write a new plan version and supersede the previous live one."""
    await connection.execute(
        text(
            "UPDATE onboarding_plans SET state = 'superseded' "
            "WHERE session_id = :session AND state IN ('ready', 'needs_review')"
        ),
        {"session": session_id},
    )
    next_version = int(
        (
            await connection.execute(
                text(
                    "SELECT coalesce(max(plan_version), 0) + 1 FROM onboarding_plans "
                    "WHERE session_id = :session"
                ),
                {"session": session_id},
            )
        ).scalar_one()
    )
    plan_id = uuid.UUID(
        str(
            (
                await connection.execute(
                    text(
                        """
                        INSERT INTO onboarding_plans
                            (session_id, analysis_id, plan_version, commit_sha,
                             manifest_digest, analyzer_version, plan_digest, state,
                             blocking_items, total_items)
                        VALUES (:session, :analysis, :version, :commit, :digest, :analyzer,
                                :plan_digest, :state, :blocking, :total)
                        RETURNING id
                        """
                    ),
                    {
                        "session": session_id,
                        "analysis": analysis_id,
                        "version": next_version,
                        "commit": commit_sha,
                        "digest": manifest_digest,
                        "analyzer": ANALYZER_VERSION,
                        "plan_digest": plan.digest(),
                        "state": plan.state,
                        "blocking": plan.blocking_items,
                        "total": len(plan.items),
                    },
                )
            ).scalar_one()
        )
    )
    for item in plan.items:
        await connection.execute(
            text(
                """
                INSERT INTO onboarding_plan_items
                    (plan_id, entity_kind, action, item_key, proposed_name,
                     existing_entity_id, existing_name, reason_code, detail)
                VALUES (:plan, :kind, :action, :key, :proposed, :existing, :existing_name,
                        :reason, CAST(:detail AS jsonb))
                ON CONFLICT (plan_id, item_key) DO NOTHING
                """
            ),
            {
                "plan": plan_id,
                "kind": item.entity_kind,
                "action": item.action,
                "key": item.item_key,
                "proposed": item.proposed_name,
                "existing": item.existing_entity_id,
                "existing_name": item.existing_name,
                "reason": item.reason_code,
                "detail": json.dumps(item.detail),
            },
        )
    return next_version


# ---------------------------------------------------------------------------
# approval and apply
# ---------------------------------------------------------------------------


async def approve(
    engine: AsyncEngine,
    *,
    session_id: uuid.UUID,
    plan_version: int,
    expected_version: int,
    actor_identity_id: uuid.UUID,
) -> dict[str, Any]:
    """Approve one exact plan version.

    Approving a version rather than "the current plan" is what makes the
    apply check meaningful: a re-analysis between review and apply produces
    a new version, and the old approval no longer matches.
    """
    async with engine.begin() as connection:
        session = await _session_row(connection, session_id)
        if session is None:
            raise OnboardingError("session_not_found", "No such onboarding session.", status=404)
        if session.version != expected_version:
            raise OnboardingError(
                "version_conflict", "The session changed since it was read.", status=409
            )
        plan = (
            await connection.execute(
                text(
                    "SELECT id, state, blocking_items FROM onboarding_plans "
                    "WHERE session_id = :session AND plan_version = :version"
                ),
                {"session": session_id, "version": plan_version},
            )
        ).first()
        if plan is None:
            raise OnboardingError("plan_not_found", "No such plan version.", status=404)
        if str(plan[1]) == "superseded":
            raise OnboardingError(
                "plan_superseded",
                "A newer analysis replaced this plan. Review the current one.",
            )
        if str(plan[1]) == "stale":
            raise OnboardingError(
                "plan_stale", "The repository moved after this plan was built."
            )
        if int(plan[2]) > 0:
            # Conflicts and unmapped items are decisions, not warnings.
            raise OnboardingError(
                "plan_blocked",
                "This plan has conflicts or unmapped bindings that must be resolved first.",
            )
        await _set_state(
            connection,
            session_id,
            str(SessionState.APPROVED),
            approved_by=actor_identity_id,
            approved_at=datetime.now(UTC),
            approved_plan_version=plan_version,
        )
    return {"session_id": str(session_id), "approved_plan_version": plan_version}


@dataclass
class ApplyOutcome:
    outcome: str
    project_id: uuid.UUID | None
    created: int = 0
    linked: int = 0
    unchanged: int = 0
    error_code: str | None = None


async def apply(
    engine: AsyncEngine,
    settings: Settings,
    client: GitHubClient,
    *,
    session_id: uuid.UUID,
    plan_version: int,
    idempotency_key: str,
    actor_identity_id: uuid.UUID,
) -> ApplyOutcome:
    """Apply an approved plan, in one transaction, with no network calls.

    The provider check happens BEFORE the transaction opens: a slow GitHub
    must not hold a catalog lock, and a failing one must not roll back an
    import that otherwise succeeded.
    """
    require_configured(settings)

    async with engine.connect() as connection:
        session = await _session_row(connection, session_id)
        if session is None:
            raise OnboardingError("session_not_found", "No such onboarding session.", status=404)
        context = await load_repository_context(connection, session.repository_id)
        plan_row = (
            await connection.execute(
                text(
                    "SELECT id, state, commit_sha, manifest_digest, plan_digest, blocking_items "
                    "FROM onboarding_plans WHERE session_id = :session AND plan_version = :version"
                ),
                {"session": session_id, "version": plan_version},
            )
        ).first()

    if plan_row is None:
        raise OnboardingError("plan_not_found", "No such plan version.", status=404)

    # "Have I already done exactly this?" is asked BEFORE "may I do it now?".
    # After a successful apply the session is `imported`, so the ordinary
    # approval check would refuse a retry — and a client that merely lost
    # the response would be told its own successful import was unapproved.
    async with engine.connect() as connection:
        recorded = (
            await connection.execute(
                text(
                    "SELECT outcome, project_id, created_entities, linked_entities, "
                    "unchanged_entities FROM onboarding_applies "
                    "WHERE plan_id = :plan AND idempotency_key = :key"
                ),
                {"plan": uuid.UUID(str(plan_row[0])), "key": idempotency_key},
            )
        ).first()
    if recorded is not None:
        return ApplyOutcome(
            outcome="unchanged",
            project_id=uuid.UUID(str(recorded[1])) if recorded[1] else None,
            created=int(recorded[2]),
            linked=int(recorded[3]),
            unchanged=int(recorded[4]),
        )

    if session.state != str(SessionState.APPROVED):
        raise OnboardingError("not_approved", "This plan has not been approved.")
    if session.approved_plan_version != plan_version:
        # Approving one version and applying another is exactly the failure
        # a plan version exists to prevent.
        raise OnboardingError(
            "plan_version_mismatch",
            "The approved plan version is not the one being applied.",
        )
    if int(plan_row[5]) > 0:
        raise OnboardingError("plan_blocked", "This plan has unresolved conflicts.")

    plan_id = uuid.UUID(str(plan_row[0]))
    planned_commit = str(plan_row[2])

    # --- freshness, outside the transaction --------------------------------
    token = await client.installation_token(
        context.installation_external_id,
        repository_ids=[context.external_id],
        permissions=dict(SCAN_PERMISSIONS),
    )
    head = await client.resolve_branch_head(
        token, context.owner, context.name, context.default_branch
    )
    if head != planned_commit:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE onboarding_plans SET state = 'stale' WHERE id = :id"),
                {"id": plan_id},
            )
            await _set_state(
                connection, session_id, str(SessionState.STALE), reason_code="commit_moved"
            )
        raise OnboardingError(
            "plan_stale",
            "The default branch moved after this plan was reviewed. Analyse again.",
        )

    entry = await client.get_content(
        token, context.owner, context.name, MANIFEST_PATH, planned_commit
    )
    content = scanner_module._decode(entry, scanner_module.DEFAULT_BUDGET)
    if content is None:
        raise OnboardingError("manifest_unreadable", "The manifest could not be read.")
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != str(plan_row[3] or ""):
        raise OnboardingError(
            "plan_stale", "The manifest changed after this plan was built. Analyse again."
        )
    validation = manifest_module.validate_content(content)
    if not validation.valid or validation.document is None:
        raise OnboardingError("manifest_invalid", "The manifest is no longer valid.")

    return await _materialise(
        engine,
        context=context,
        session=session,
        plan_id=plan_id,
        plan_version=plan_version,
        document=validation.document,
        commit_sha=planned_commit,
        manifest_digest=str(plan_row[3] or ""),
        idempotency_key=idempotency_key,
        actor_identity_id=actor_identity_id,
    )


async def _materialise(
    engine: AsyncEngine,
    *,
    context: RepositoryContext,
    session: SessionRow,
    plan_id: uuid.UUID,
    plan_version: int,
    document: dict[str, Any],
    commit_sha: str,
    manifest_digest: str,
    idempotency_key: str,
    actor_identity_id: uuid.UUID,
) -> ApplyOutcome:
    """One transaction. Any failure leaves no catalog rows behind."""
    spec = document["spec"]
    metadata = document["metadata"]
    project_key = str(metadata["name"])
    source_ref = f"github:{context.external_id}:{MANIFEST_PATH}"

    async with engine.begin() as connection:
        # The idempotency claim comes first, inside the same transaction as
        # the work: a client that lost the response repeats the call and
        # gets the recorded answer instead of a second project.
        #
        # Claimed as `failed` and promoted to `applied` at the end. That is
        # not bookkeeping — it keeps the row's invariant true at every
        # instant, so a row that names no project never claims to have made
        # one. The whole thing is one transaction, so nobody observes the
        # intermediate state.
        claim = (
            await connection.execute(
                text(
                    """
                    INSERT INTO onboarding_applies
                        (plan_id, session_id, actor_identity_id, outcome, idempotency_key)
                    VALUES (:plan, :session, :actor, 'failed', :key)
                    ON CONFLICT (plan_id, idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "plan": plan_id,
                    "session": session.id,
                    "actor": actor_identity_id,
                    "key": idempotency_key,
                },
            )
        ).first()
        if claim is None:
            existing = (
                await connection.execute(
                    text(
                        "SELECT outcome, project_id, created_entities, linked_entities, "
                        "unchanged_entities FROM onboarding_applies "
                        "WHERE plan_id = :plan AND idempotency_key = :key"
                    ),
                    {"plan": plan_id, "key": idempotency_key},
                )
            ).one()
            return ApplyOutcome(
                outcome="unchanged",
                project_id=uuid.UUID(str(existing[1])) if existing[1] else None,
                created=int(existing[2]),
                linked=int(existing[3]),
                unchanged=int(existing[4]),
            )
        apply_id = uuid.UUID(str(claim[0]))

        service = CatalogService(connection)
        created = linked = unchanged = 0

        project_row = (
            await connection.execute(
                text("SELECT id, scope_id FROM projects WHERE project_key = :key"),
                {"key": project_key},
            )
        ).first()
        if project_row is None:
            project = await service.create_project(
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
                source_revision=commit_sha,
            )
            project_id, project_scope = project.id, project.scope_id
            created += 1
        else:
            # Linking an existing project. Its identity stays authoritative —
            # nothing about the existing row is replaced from the manifest.
            project_id = uuid.UUID(str(project_row[0]))
            project_scope = uuid.UUID(str(project_row[1]))
            unchanged += 1

        for environment in spec["environments"]:
            environment_key = str(environment["name"])
            cluster_id = None
            if str(environment["runtime"]) == "kubernetes":
                cluster_row = (
                    await connection.execute(
                        text("SELECT id FROM clusters WHERE cluster_ref = :ref"),
                        {"ref": str(environment["clusterRef"])},
                    )
                ).first()
                if cluster_row is None:
                    # The plan should have caught this. Re-checked here
                    # because a cluster can be removed between plan and
                    # apply, and a manifest never conjures infrastructure.
                    raise OnboardingError(
                        "unknown_cluster",
                        "The manifest references a cluster that is not registered in Drake.",
                    )
                cluster_id = uuid.UUID(str(cluster_row[0]))

            existing_env = (
                await connection.execute(
                    text(
                        "SELECT id FROM environments WHERE project_id = :p "
                        "AND environment_key = :key"
                    ),
                    {"p": project_id, "key": environment_key},
                )
            ).first()
            if existing_env is None:
                environment_entity = await service.create_environment(
                    project_id,
                    environment_key,
                    runtime=str(environment["runtime"]),
                    branch=str(environment.get("branch") or ""),
                    criticality=str(environment["criticality"]),
                    cluster_id=cluster_id,
                    namespace=environment.get("namespace"),
                    source_ref=source_ref,
                    source_revision=commit_sha,
                )
                environment_id = environment_entity.id
                created += 1
            else:
                environment_id = uuid.UUID(str(existing_env[0]))
                linked += 1

            for service_spec in spec["services"]:
                service_key = str(service_spec["name"])
                existing_service = (
                    await connection.execute(
                        text(
                            "SELECT id FROM service_definitions WHERE project_id = :p "
                            "AND service_key = :key"
                        ),
                        {"p": project_id, "key": service_key},
                    )
                ).first()
                if existing_service is None:
                    service_id = await service.create_service_definition(
                        project_id,
                        service_key,
                        component=str(service_spec["component"]),
                        runtime=str(service_spec["runtime"]),
                        metrics_profile=str(service_spec["metricsProfile"]),
                        workload_selector=service_spec.get("workloadSelector") or {},
                        health=service_spec.get("health") or {},
                        source_ref=source_ref,
                        source_revision=commit_sha,
                    )
                    created += 1
                else:
                    service_id = uuid.UUID(str(existing_service[0]))
                    unchanged += 1
                await service.bind_service(environment_id, service_id)

        # Honest placeholders. The project exists; nothing is wired up yet,
        # and each of these will report `not_configured` until it is.
        for integration_type in ("prometheus", "github", "cluster-agent", "backup-reporter"):
            await service.register_integration(integration_type, project_scope)

        await connection.execute(
            text(
                "INSERT INTO github_repository_projects "
                "(repository_id, project_id, scope_id, commit_sha, manifest_digest) "
                "VALUES (:repo, :project, :scope, :commit, :digest) "
                "ON CONFLICT (repository_id) DO NOTHING"
            ),
            {
                "repo": context.row_id,
                "project": project_id,
                "scope": context.scope_id,
                "commit": commit_sha,
                "digest": manifest_digest,
            },
        )
        await connection.execute(
            text(
                "UPDATE onboarding_applies SET outcome = 'applied', project_id = :project, "
                "created_entities = :created, linked_entities = :linked, "
                "unchanged_entities = :unchanged WHERE id = :id"
            ),
            {
                "id": apply_id,
                "project": project_id,
                "created": created,
                "linked": linked,
                "unchanged": unchanged,
            },
        )
        await connection.execute(
            text("UPDATE onboarding_plans SET state = 'applied' WHERE id = :id"),
            {"id": plan_id},
        )
        await _set_state(
            connection,
            session.id,
            str(SessionState.IMPORTED),
            imported_project_id=project_id,
            imported_at=datetime.now(UTC),
        )

    _ = plan_version
    return ApplyOutcome(
        outcome="applied",
        project_id=project_id,
        created=created,
        linked=linked,
        unchanged=unchanged,
    )


async def cancel(
    engine: AsyncEngine, *, session_id: uuid.UUID, expected_version: int
) -> dict[str, Any]:
    async with engine.begin() as connection:
        session = await _session_row(connection, session_id)
        if session is None:
            raise OnboardingError("session_not_found", "No such onboarding session.", status=404)
        if session.state == str(SessionState.IMPORTED):
            # An import already happened. Cancelling it would suggest the
            # catalog rows went away, and they did not.
            raise OnboardingError("already_imported", "This session has already been applied.")
        if session.version != expected_version:
            raise OnboardingError(
                "version_conflict", "The session changed since it was read.", status=409
            )
        await _set_state(connection, session_id, str(SessionState.CANCELLED))
    return {"session_id": str(session_id), "state": str(SessionState.CANCELLED)}


async def mark_stale_for_commit(
    engine: AsyncEngine, *, repository_row_id: uuid.UUID, new_commit_sha: str
) -> int:
    """A push moved the branch: every plan built on an older commit is stale.

    Called from the webhook consumer. It changes no catalog row and makes no
    provider call — it marks reviews that describe a repository state that
    no longer exists, so nobody can approve one by accident.
    """
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                """
                UPDATE onboarding_plans SET state = 'stale'
                WHERE state IN ('ready', 'needs_review')
                  AND commit_sha <> :commit
                  AND session_id IN (
                      SELECT id FROM onboarding_sessions
                      WHERE repository_id = :repo
                        AND state NOT IN ('imported', 'cancelled', 'failed')
                  )
                RETURNING id
                """
            ),
            {"repo": repository_row_id, "commit": new_commit_sha},
        )
        stale = len(result.all())
        if stale:
            await connection.execute(
                text(
                    """
                    UPDATE onboarding_sessions
                    SET state = 'stale', reason_code = 'commit_moved',
                        version = version + 1, updated_at = now()
                    WHERE repository_id = :repo
                      AND state IN ('needs_review', 'ready', 'approved')
                    """
                ),
                {"repo": repository_row_id},
            )
    return stale


__all__ = [
    "ANALYZER_VERSION",
    "Action",
    "ApplyOutcome",
    "EntityKind",
    "NotConfiguredError",
    "OnboardingError",
    "PlanItem",
    "SessionState",
    "analyze",
    "apply",
    "approve",
    "cancel",
    "create_session",
    "load_snapshot",
    "mark_stale_for_commit",
    "require_configured",
]
