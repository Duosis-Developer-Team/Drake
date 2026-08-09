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
after, through GitOps.

**Nothing is deleted, ever.** There is no code path here that removes a
catalog row. A service that vanished from a manifest is reported.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.alerting.contracts import default_burn_profile, indicator_template
from drake_api.audit.service import AuditEventData, record_audit_event_in
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
    ACTIONABLE_ACTIONS,
    ANALYZER_VERSION,
    BINDABLE_WORKLOAD_KINDS,
    MUTABLE_ENVIRONMENT_FIELDS,
    MUTABLE_PROJECT_FIELDS,
    MUTABLE_SERVICE_FIELDS,
    PLACEHOLDER_INTEGRATIONS,
    Action,
    CatalogSnapshot,
    EntityKind,
    Plan,
    PlanItem,
    SessionState,
    build_plan,
    deployment_source_item,
)
from drake_api.service_health.policy import DEFAULT_POLICY_KEY
from drake_api.service_health.presets import DEFAULT_PRESET_KEY
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


async def _session_row(connection: AsyncConnection, session_id: uuid.UUID) -> SessionRow | None:
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


# ---------------------------------------------------------------------------
# the session state machine
# ---------------------------------------------------------------------------

#: Which states each mutation may act from. Hiding a button is not a state
#: machine: an endpoint that accepts anything the UI happens to send can
#: resurrect a cancelled session or re-open an imported one, and both of
#: those rewrite history somebody already acted on.
#:
#: `imported` and `cancelled` appear in no list. They are terminal, and the
#: way to revisit a repository is a NEW session — which keeps the record of
#: what the old one decided.
ALLOWED_STATES: dict[str, frozenset[str]] = {
    "analyze": frozenset(
        {
            str(SessionState.DRAFT),
            str(SessionState.DISCOVERY_PENDING),
            str(SessionState.NEEDS_REVIEW),
            str(SessionState.READY),
            str(SessionState.PROVIDER_UNAVAILABLE),
            str(SessionState.STALE),
            str(SessionState.FAILED),
        }
    ),
    # An approval is of one reviewed plan, so the session has to be showing
    # one. `needs_review` is excluded on purpose: it means the plan has
    # blocking items, and approving around them is the whole failure mode.
    "approve": frozenset({str(SessionState.READY)}),
    "apply": frozenset({str(SessionState.APPROVED)}),
    "cancel": frozenset(
        {
            str(SessionState.DRAFT),
            str(SessionState.DISCOVERY_PENDING),
            str(SessionState.NEEDS_REVIEW),
            str(SessionState.READY),
            str(SessionState.APPROVED),
            str(SessionState.PROVIDER_UNAVAILABLE),
            str(SessionState.STALE),
            str(SessionState.FAILED),
        }
    ),
    # GitOps proposes a manifest built from an analysis, so there has to be
    # one. It stays available after approval because the repository still
    # has no manifest until somebody merges the proposal.
    "gitops": frozenset(
        {
            str(SessionState.NEEDS_REVIEW),
            str(SessionState.READY),
            str(SessionState.APPROVED),
        }
    ),
}


class InvalidSessionStateError(OnboardingError):
    """This action is not available from the state the session is in."""

    def __init__(self, action: str, state: str) -> None:
        super().__init__(
            "invalid_session_state",
            f"A session in state '{state}' cannot be {action}.",
            status=409,
        )


async def _state_of(connection: AsyncConnection, session_id: uuid.UUID) -> str | None:
    return (
        await connection.execute(
            text("SELECT state FROM onboarding_sessions WHERE id = :id"), {"id": session_id}
        )
    ).scalar_one_or_none()


#: States that deserve a more specific refusal than "wrong state".
#: `needs_review` IS a state approve may not act from, but saying only that
#: hides the reason: the plan has blocking items. The narrower code tells an
#: operator what to fix; the generic one tells them to guess.
_SPECIFIC_REFUSALS: dict[tuple[str, str], tuple[str, str]] = {
    ("approve", str(SessionState.NEEDS_REVIEW)): (
        "plan_blocked",
        "This plan has conflicts or unmapped bindings that must be resolved first.",
    ),
    ("approve", str(SessionState.APPROVED)): (
        "already_approved",
        "This plan version has already been approved.",
    ),
    ("apply", str(SessionState.IMPORTED)): (
        "already_imported",
        "This session has already been applied.",
    ),
}


async def lock_session_for(
    connection: AsyncConnection, session_id: uuid.UUID, action: str
) -> SessionRow:
    """Take the session's row lock, then check the action is legal from here.

    The lock is the point. Reading the state, deciding, and then writing
    leaves a window in which a second request reads the same state and
    decides the same thing — two analyses from one `ready`, two cancels, an
    apply racing a cancel. `FOR UPDATE` makes the second request wait for
    the first to commit and then see what it did.

    Must be called inside the transaction that performs the mutation. A lock
    released before the write protects nothing.
    """
    row = (
        await connection.execute(
            text(
                "SELECT id, repository_id, scope_id, state, version, analyzed_commit_sha, "
                "approved_plan_version FROM onboarding_sessions WHERE id = :id FOR UPDATE"
            ),
            {"id": session_id},
        )
    ).first()
    if row is None:
        raise OnboardingError("session_not_found", "No such onboarding session.", status=404)
    session = SessionRow(
        id=uuid.UUID(str(row[0])),
        repository_id=uuid.UUID(str(row[1])),
        scope_id=uuid.UUID(str(row[2])),
        state=str(row[3]),
        version=int(row[4]),
        analyzed_commit_sha=row[5],
        approved_plan_version=row[6],
    )
    if session.state not in ALLOWED_STATES[action]:
        specific = _SPECIFIC_REFUSALS.get((action, session.state))
        if specific is not None:
            raise OnboardingError(specific[0], specific[1], status=409)
        raise InvalidSessionStateError(action, session.state)
    return session


async def _set_state(
    connection: AsyncConnection,
    session_id: uuid.UUID,
    state: str,
    *,
    reason_code: str | None = None,
    **columns: Any,
) -> None:
    assignments = [
        "state = :state",
        "reason_code = :reason",
        "version = version + 1",
        "updated_at = now()",
    ]
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

    # Claiming `analyzing` is the critical section: two clicks, or a click
    # and a retry, otherwise both read `ready` and both start scanning.
    async with engine.begin() as connection:
        await lock_session_for(connection, session_id, "analyze")
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
                manifest_digest = hashlib.sha256(result.manifest_text.encode("utf-8")).hexdigest()
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
                    {
                        "analysis": analysis_id,
                        **{
                            "type": finding["finding_type"],
                            "path": finding["safe_path"],
                            "confidence": finding["confidence"],
                            "evidence": finding["evidence_kind"],
                            "target": finding["proposed_target"],
                            "reason": finding["review_reason"],
                        },
                    },
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
        state = str(SessionState.NEEDS_REVIEW) if plan.blocking_items else str(SessionState.READY)
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
    project_metadata: dict[str, dict[str, Any]] = {}
    environment_metadata: dict[tuple[str, str], dict[str, Any]] = {}
    service_metadata: dict[tuple[str, str], dict[str, Any]] = {}
    slo_definitions: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}

    if project_id is not None:
        current = (
            await connection.execute(
                text(
                    "SELECT project_key, display_name, criticality, tenant_model, "
                    "repo_provider, repo_owner, repo_name FROM projects WHERE id = :p"
                ),
                {"p": project_id},
            )
        ).one()
        project_metadata[project_key] = {
            "project_key": str(current[0]),
            "display_name": str(current[1] or ""),
            "criticality": str(current[2] or ""),
            "tenant_model": str(current[3] or ""),
            "repo_provider": str(current[4] or ""),
            "repo_owner": str(current[5] or ""),
            "repo_name": str(current[6] or ""),
        }
        for entry in (
            await connection.execute(
                text(
                    "SELECT e.environment_key, e.id, e.branch, e.criticality, e.runtime, "
                    "e.namespace, c.cluster_ref FROM environments e "
                    "LEFT JOIN clusters c ON c.id = e.cluster_id WHERE e.project_id = :p"
                ),
                {"p": project_id},
            )
        ).all():
            environments[(project_key, str(entry[0]))] = str(entry[1])
            environment_metadata[(project_key, str(entry[0]))] = {
                "environment_key": str(entry[0]),
                "branch": str(entry[2] or ""),
                "criticality": str(entry[3] or ""),
                "runtime": str(entry[4] or ""),
                "namespace": str(entry[5] or ""),
                "cluster_ref": str(entry[6] or ""),
            }
        for entry in (
            await connection.execute(
                text(
                    "SELECT slo_key, id, display_name, indicator, objective_ratio, "
                    "window_seconds FROM slo_definitions WHERE project_id = :p"
                ),
                {"p": project_id},
            )
        ).all():
            slo_definitions[(project_key, str(entry[0]))] = (
                str(entry[1]),
                {
                    "slo_key": str(entry[0]),
                    "display_name": str(entry[2] or ""),
                    "indicator": str(entry[3] or ""),
                    "objective_ratio": round(float(entry[4]), 7),
                    "window_seconds": int(entry[5]),
                },
            )
        manifest_services = {str(item.get("name") or "") for item in spec.get("services") or []}
        for entry in (
            await connection.execute(
                text(
                    "SELECT service_key, id, display_name, component, runtime, "
                    "metrics_profile, workload_selector, health "
                    "FROM service_definitions WHERE project_id = :p"
                ),
                {"p": project_id},
            )
        ).all():
            services[(project_key, str(entry[0]))] = str(entry[1])
            service_metadata[(project_key, str(entry[0]))] = {
                "service_key": str(entry[0]),
                "display_name": str(entry[2] or ""),
                "component": str(entry[3] or ""),
                "runtime": str(entry[4] or ""),
                "metrics_profile": str(entry[5] or ""),
                "workload_selector": dict(entry[6] or {}),
                "health": dict(entry[7] or {}),
            }
            if str(entry[0]) not in manifest_services:
                catalog_only.append(str(entry[0]))

    clusters = {
        str(entry[0]): str(entry[1])
        for entry in (await connection.execute(text("SELECT cluster_ref, id FROM clusters"))).all()
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

    observed, existing_bindings = await _binding_evidence(connection, document, project_id)

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
        project_metadata=project_metadata,
        environment_metadata=environment_metadata,
        service_metadata=service_metadata,
        slo_definitions=slo_definitions,
        observed_workloads=observed,
        existing_bindings=existing_bindings,
    )


async def _binding_evidence(
    connection: AsyncConnection, document: dict[str, Any], project_id: uuid.UUID | None
) -> tuple[
    dict[tuple[str, str], tuple[dict[str, str], ...]], dict[tuple[str, str], dict[str, Any]]
]:
    """Workloads the cluster agent has OBSERVED, and bindings that exist.

    The manifest says a service exists; only the agent knows which workload
    runs it. So candidates come from `inventory_resources` — things actually
    seen in the cluster — matched by namespace and by the service's own
    `workloadSelector` labels. No selector means no evidence, and no
    evidence means no proposal.
    """
    spec = document.get("spec") or {}
    observed: dict[tuple[str, str], tuple[dict[str, str], ...]] = {}
    existing: dict[tuple[str, str], dict[str, Any]] = {}

    for environment in spec.get("environments") or []:
        environment_key = str(environment.get("name") or "")
        if str(environment.get("runtime") or "") != "kubernetes":
            continue
        namespace = str(environment.get("namespace") or "")
        cluster_ref = str(environment.get("clusterRef") or "")
        if not (namespace and cluster_ref):
            continue

        for service in spec.get("services") or []:
            service_key = str(service.get("name") or "")
            selector = service.get("workloadSelector") or {}
            if not isinstance(selector, dict) or not selector:
                # Without a selector Drake has no way to tell this service's
                # workload from any other in the namespace.
                continue
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT r.kind, r.name
                        FROM inventory_resources r
                        JOIN clusters c ON c.id = r.cluster_id
                        WHERE c.cluster_ref = :cluster
                          AND r.namespace = :namespace
                          AND r.lifecycle = 'active'
                          AND r.kind = ANY(:kinds)
                          AND (r.payload -> 'labels') @> CAST(:selector AS jsonb)
                        ORDER BY r.kind, r.name
                        LIMIT 5
                        """
                    ),
                    {
                        "cluster": cluster_ref,
                        "namespace": namespace,
                        "kinds": sorted(BINDABLE_WORKLOAD_KINDS),
                        "selector": json.dumps(
                            {str(key): str(value) for key, value in selector.items()}
                        ),
                    },
                )
            ).all()
            if rows:
                observed[(environment_key, service_key)] = tuple(
                    {"kind": str(row[0]), "name": str(row[1])} for row in rows
                )

    if project_id is not None:
        for row in (
            await connection.execute(
                text(
                    """
                    SELECT e.environment_key, sd.service_key, b.id, b.workload_kind,
                           b.workload_name
                    FROM service_workload_bindings b
                    JOIN environments e ON e.id = b.environment_id
                    JOIN service_definitions sd ON sd.id = b.service_id
                    WHERE b.project_id = :p AND b.lifecycle = 'active'
                    """
                ),
                {"p": project_id},
            )
        ).all():
            existing[(str(row[0]), str(row[1]))] = {
                "id": str(row[2]),
                "workload_kind": str(row[3]),
                "workload_name": str(row[4]),
            }
    return observed, existing


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
                # The payload and the before/after travel WITH the item, so
                # what apply executes is what the approval covered. They are
                # in `detail` because a plan item's columns are a reviewed
                # schema and this needs no new one.
                "detail": json.dumps(
                    {
                        **item.detail,
                        **({"payload": item.payload} if item.payload else {}),
                        **({"changes": item.changes} if item.changes else {}),
                    }
                ),
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
        session = await lock_session_for(connection, session_id, "approve")
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
            raise OnboardingError("plan_stale", "The repository moved after this plan was built.")
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
    """What actually committed.

    Every counter reflects a COMMITTED transaction. A rollback returns no
    outcome at all, so nothing here can report work that was undone.
    """

    outcome: str
    project_id: uuid.UUID | None
    created: int = 0
    linked: int = 0
    unchanged: int = 0
    error_code: str | None = None
    metadata_updated: int = 0
    slo_definitions_created: int = 0
    slo_definitions_updated: int = 0
    bindings_created: int = 0
    #: False for a receipt written before migration 0020, whose extended
    #: counters were never recorded. Stored zeros are not measured zeros.
    counters_complete: bool = True


async def apply(
    engine: AsyncEngine,
    settings: Settings,
    client: GitHubClient,
    *,
    session_id: uuid.UUID,
    plan_version: int,
    idempotency_key: str,
    actor_identity_id: uuid.UUID,
    correlation_id: str = "",
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

    plan_id = uuid.UUID(str(plan_row[0]))
    planned_commit = str(plan_row[2])
    plan_digest = str(plan_row[4])

    # Integrity FIRST, ahead of everything: ahead of the replay lookup and
    # ahead of the first provider call.
    #
    # Ahead of the provider because a plan somebody rewrote should not buy
    # an installation token and two GitHub reads before being refused —
    # refusing after spending is a way to make refusing expensive.
    #
    # Ahead of the replay lookup because a receipt would otherwise let a
    # tampered plan through: the first apply succeeds, the plan is rewritten
    # afterwards, and the same idempotency key returns the recorded answer
    # without ever looking at what the plan now says. A client that asks
    # again gets told the current plan applied cleanly. It did not.
    async with engine.connect() as connection:
        await verify_plan_integrity(connection, plan_id, plan_digest)

    # "Have I already done exactly this?" is asked BEFORE "may I do it now?".
    # After a successful apply the session is `imported`, so the ordinary
    # approval check would refuse a retry — and a client that merely lost
    # the response would be told its own successful import was unapproved.
    async with engine.connect() as connection:
        recorded = await _recorded_receipt(connection, session.id, idempotency_key)
    if recorded is not None:
        if str(recorded["plan_id"]) != str(plan_id):
            raise IdempotencyKeyReusedError()
        return _outcome_from_receipt(recorded)

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
        plan_digest=plan_digest,
        idempotency_key=idempotency_key,
        actor_identity_id=actor_identity_id,
        correlation_id=correlation_id,
    )


@dataclass
class _ApplyCounters:
    """What actually committed. Every field is incremented by a handler."""

    created: int = 0
    linked: int = 0
    unchanged: int = 0
    metadata_updated: int = 0
    slo_definitions_created: int = 0
    slo_definitions_updated: int = 0
    bindings_created: int = 0


@dataclass
class _ApplyContext:
    """Everything a handler may touch, resolved once."""

    connection: AsyncConnection
    catalog: CatalogService
    repository: RepositoryContext
    commit_sha: str
    #: The digest recorded on the approved plan. Not recomputed from a
    #: document at apply time: that would be a second, differently-derived
    #: value pretending to be the one that was reviewed.
    manifest_digest: str
    source_ref: str
    counters: _ApplyCounters
    project_id: uuid.UUID | None = None
    project_scope: uuid.UUID | None = None
    #: environment_key -> id, filled as environments are applied
    environments: dict[str, uuid.UUID] = field(default_factory=dict)
    #: service_key -> id
    services: dict[str, uuid.UUID] = field(default_factory=dict)
    #: environment keys the approved plan created or linked
    planned_environments: set[str] = field(default_factory=set)


class PlanNotApplicableError(OnboardingError):
    """An approved plan item nothing knows how to apply.

    Raised BEFORE any mutation. Applying the rest and reporting success
    would leave a catalog half-matching a plan somebody approved, which is
    worse than refusing: the operator would have no way to tell which half.
    """

    def __init__(self, item_keys: list[str]) -> None:
        super().__init__(
            "plan_item_unsupported",
            "This plan contains items Drake cannot apply: " + ", ".join(sorted(item_keys)[:5]),
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
    plan_digest: str,
    idempotency_key: str,
    actor_identity_id: uuid.UUID,
    correlation_id: str = "",
) -> ApplyOutcome:
    """Apply the APPROVED PLAN, item by item, in one transaction.

    This used to walk the manifest and infer what to do, which is how the
    two drifted: the plan could propose something apply never did, and apply
    could change something the plan never mentioned. Now the plan is the
    instruction set — every actionable item is dispatched to exactly one
    registered handler, and a handler runs only for an item that is in the
    approved plan.

    No value comes from the manifest. Every one comes from the approved
    item's payload, and the digest over those items is verified against the
    plan's stored digest before anything is written — so what applies is
    what was approved, not what the stored plan happens to say now.

    `document` is deliberately unused here. It is the proof, carried down
    from the freshness check, that the manifest at the approved commit still
    hashes to the digest frozen on the plan; keeping it in the signature is
    what lets a test replace it with an empty object and demonstrate that no
    handler reads it. If that ever stops being true the test fails, which is
    the point.
    """
    source_ref = f"github:{context.external_id}:{MANIFEST_PATH}"

    async with engine.begin() as connection:
        # Integrity again, and inside the transaction this time. `apply`
        # already checked before it spoke to the provider; that round-trip
        # takes real time, and a plan can be rewritten during it. Checking
        # here — before the claim, before any mutation — is what makes the
        # items below the ones that were approved.
        items = await verify_plan_integrity(connection, plan_id, plan_digest)

        # The claim comes next, in the same transaction as the work: a
        # client that lost the response repeats the call and gets the
        # recorded answer instead of a second project.
        claim = await claim_apply(
            connection,
            plan_id=plan_id,
            session_id=session.id,
            actor_identity_id=actor_identity_id,
            idempotency_key=idempotency_key,
        )
        if claim.replay is not None:
            # A replay is answered before the state is examined, and that
            # order matters: after a successful apply the session is
            # `imported`, which is not a state an apply may start from. It
            # is not starting one. The work is already committed and this is
            # its recorded answer.
            return claim.replay
        assert claim.apply_id is not None
        apply_id = claim.apply_id

        # Only a caller that actually claimed gets here, so this is the one
        # that is about to mutate. The state check holds the session's row
        # lock: `apply` already refused a session that was not `approved`,
        # but that read happened outside this transaction, and a cancel or a
        # re-analysis committed in between would otherwise be overwritten by
        # an apply that never saw it.
        await lock_session_for(connection, session.id, "apply")

        # Coverage check, before a single mutation. An actionable item with
        # no handler stops the whole apply.
        unsupported = [
            item["item_key"]
            for item in items
            if item["action"] in ACTIONABLE_ACTIONS
            and (item["entity_kind"], item["action"]) not in _HANDLERS
        ]
        if unsupported:
            raise PlanNotApplicableError(unsupported)

        apply_context = _ApplyContext(
            connection=connection,
            catalog=CatalogService(connection),
            repository=context,
            commit_sha=commit_sha,
            manifest_digest=manifest_digest,
            source_ref=source_ref,
            counters=_ApplyCounters(),
        )

        # Deterministic order: a project before its environments, an
        # environment before the services bound into it, and bindings last
        # because they need both.
        for item in sorted(items, key=lambda entry: _ORDER.get(entry["entity_kind"], 99)):
            if item["action"] == str(Action.NO_CHANGE):
                # A re-onboarding where nothing differs still needs the
                # project resolved: its children are addressed relative to
                # it, and `no_change` means "already correct", not "absent".
                if item["entity_kind"] == str(EntityKind.PROJECT) and item["proposed_name"]:
                    await _resolve_project(apply_context, item)
                apply_context.counters.unchanged += 1
                continue
            if item["action"] not in ACTIONABLE_ACTIONS:
                continue
            handler = _HANDLERS[(item["entity_kind"], item["action"])]
            await handler(apply_context, item)

        assert apply_context.project_id is not None
        counters = apply_context.counters

        await _promote_receipt(connection, apply_id, apply_context.project_id, counters)
        await connection.execute(
            text("UPDATE onboarding_plans SET state = 'applied' WHERE id = :id"),
            {"id": plan_id},
        )
        await _set_state(
            connection,
            session.id,
            str(SessionState.IMPORTED),
            imported_project_id=apply_context.project_id,
            imported_at=datetime.now(UTC),
        )

        # In the SAME transaction as everything above. An apply that changed
        # a catalog with no record of who asked is worse than one that did
        # not happen: nobody can discover it afterwards. If this insert
        # fails, every row this function wrote goes with it.
        await record_audit_event_in(
            connection,
            AuditEventData(
                actor_type="user",
                actor_id=str(actor_identity_id),
                action="onboarding.apply",
                result="success",
                target_type="onboarding_session",
                target_id=str(session.id),
                correlation_id=correlation_id,
                metadata={
                    "plan_version": plan_version,
                    "plan_digest": plan_digest[:16],
                    "project_id": str(apply_context.project_id),
                    "created": counters.created,
                    "linked": counters.linked,
                    "metadata_updated": counters.metadata_updated,
                    "slo_definitions": counters.slo_definitions_created
                    + counters.slo_definitions_updated,
                    "bindings_created": counters.bindings_created,
                },
            ),
        )
        _ = manifest_digest

    return ApplyOutcome(
        outcome="applied",
        project_id=apply_context.project_id,
        created=counters.created,
        linked=counters.linked,
        unchanged=counters.unchanged,
        metadata_updated=counters.metadata_updated,
        slo_definitions_created=counters.slo_definitions_created,
        slo_definitions_updated=counters.slo_definitions_updated,
        bindings_created=counters.bindings_created,
    )


@dataclass
class ApplyClaim:
    """Who holds `(session, key)` — this call, or an apply that already ran.

    Exactly one of the two is set. `apply_id` means this caller owns the
    receipt and must finish it; `replay` means somebody else already did the
    work and this is its recorded answer.
    """

    apply_id: uuid.UUID | None = None
    replay: ApplyOutcome | None = None


async def claim_apply(
    connection: AsyncConnection,
    *,
    plan_id: uuid.UUID,
    session_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
    idempotency_key: str,
) -> ApplyClaim:
    """Take `(session, key)` for this apply, or resolve who already has it.

    Claimed as `failed` and promoted to `applied` once the work is done, so
    the row's invariant holds at every instant and a row that names no
    project never claims to have made one.

    `ON CONFLICT DO NOTHING` against an UNCOMMITTED conflicting row waits
    for that transaction to finish, so a loser reads a settled receipt
    rather than an absent one. If the holder rolled back, the insert
    succeeds and this caller becomes the holder — which is why a retry after
    a failed apply works.

    The reuse decision lives here rather than in the caller because this is
    the only place that learns, from the database, that somebody else got
    there first. A pre-check cannot: both callers read "no receipt" before
    either writes one.
    """
    claimed = (
        await connection.execute(
            text(
                """
                INSERT INTO onboarding_applies
                    (plan_id, session_id, actor_identity_id, outcome, idempotency_key)
                VALUES (:plan, :session, :actor, 'failed', :key)
                ON CONFLICT (session_id, idempotency_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "plan": plan_id,
                "session": session_id,
                "actor": actor_identity_id,
                "key": idempotency_key,
            },
        )
    ).first()
    if claimed is not None:
        return ApplyClaim(apply_id=uuid.UUID(str(claimed[0])))

    existing = await _recorded_receipt(connection, session_id, idempotency_key)
    if existing is None:  # pragma: no cover - the row cannot vanish
        raise OnboardingError(
            "apply_claim_unavailable",
            "This idempotency key is held by an apply whose result is not readable.",
            status=409,
        )
    if str(existing["plan_id"]) != str(plan_id):
        raise IdempotencyKeyReusedError()
    return ApplyClaim(replay=_outcome_from_receipt(existing))


async def _promote_receipt(
    connection: AsyncConnection,
    apply_id: uuid.UUID,
    project_id: uuid.UUID,
    counters: "_ApplyCounters",
) -> None:
    """Finish the receipt, inside the transaction that did the work.

    Every counter the response carries is stored, because a retry replays
    this row rather than recounting anything. Storing three of seven is how
    a retried apply came to report zero for work that had happened.
    """
    await connection.execute(
        text(
            "UPDATE onboarding_applies SET outcome = 'applied', project_id = :project, "
            "created_entities = :created, linked_entities = :linked, "
            "unchanged_entities = :unchanged, metadata_updated = :metadata, "
            "slo_definitions_created = :slo_created, "
            "slo_definitions_updated = :slo_updated, "
            "bindings_created = :bindings, counters_complete = true "
            "WHERE id = :id"
        ),
        {
            "id": apply_id,
            "project": project_id,
            "created": counters.created,
            "linked": counters.linked,
            "unchanged": counters.unchanged,
            "metadata": counters.metadata_updated,
            "slo_created": counters.slo_definitions_created,
            "slo_updated": counters.slo_definitions_updated,
            "bindings": counters.bindings_created,
        },
    )


async def _recorded_receipt(
    connection: AsyncConnection, session_id: uuid.UUID, idempotency_key: str
) -> dict[str, Any] | None:
    """The receipt for one (session, key), or nothing."""
    row = (
        await connection.execute(
            text(
                "SELECT plan_id, outcome, project_id, created_entities, linked_entities, "
                "unchanged_entities, metadata_updated, slo_definitions_created, "
                "slo_definitions_updated, bindings_created, counters_complete "
                "FROM onboarding_applies "
                "WHERE session_id = :session AND idempotency_key = :key"
            ),
            {"session": session_id, "key": idempotency_key},
        )
    ).first()
    if row is None:
        return None
    return {
        "plan_id": row[0],
        "outcome": str(row[1]),
        "project_id": row[2],
        "created": int(row[3]),
        "linked": int(row[4]),
        "unchanged": int(row[5]),
        "metadata_updated": int(row[6]),
        "slo_definitions_created": int(row[7]),
        "slo_definitions_updated": int(row[8]),
        "bindings_created": int(row[9]),
        "counters_complete": bool(row[10]),
    }


def _outcome_from_receipt(receipt: dict[str, Any]) -> ApplyOutcome:
    """A retry returns exactly what the first call returned.

    Including the outcome WORD. An earlier version answered `unchanged`
    here, which reads as "this request changed nothing" — but the request
    did change things; it changed them the first time it was sent. Reusing
    an idempotency key is not a new operation with a different result, it is
    a replay of one committed answer, so the whole answer is replayed.

    Every counter is restored, not recomputed — the work already happened
    and counting it again would mean re-deriving it from a catalog that has
    since moved on.

    A receipt written before migration 0020 never recorded the extended
    counters. `counters_complete` says so, and the outcome carries that
    forward rather than presenting stored zeros as measured work.
    """
    return ApplyOutcome(
        outcome=receipt["outcome"],
        project_id=uuid.UUID(str(receipt["project_id"])) if receipt["project_id"] else None,
        created=receipt["created"],
        linked=receipt["linked"],
        unchanged=receipt["unchanged"],
        metadata_updated=receipt["metadata_updated"],
        slo_definitions_created=receipt["slo_definitions_created"],
        slo_definitions_updated=receipt["slo_definitions_updated"],
        bindings_created=receipt["bindings_created"],
        counters_complete=receipt["counters_complete"],
    )


class IdempotencyKeyReusedError(OnboardingError):
    """The same key, the same session, a different plan.

    An idempotency key is a client's statement that a request is the SAME
    request. Honouring it across plan versions would let a retry apply an
    approval the client never meant to send — so a reuse under another plan
    is a conflict, not a replay.
    """

    def __init__(self) -> None:
        super().__init__(
            "idempotency_key_reused",
            "This idempotency key was already used for a different plan version in this session.",
            status=409,
        )


class PlanIntegrityError(OnboardingError):
    """The stored plan no longer hashes to the digest that was approved.

    Somebody — or something — changed a plan item after approval. The values
    an approval binds are only binding if they are checked, so this refuses
    before any provider call, before the replay lookup, before any claim and
    before any mutation. See `verify_plan_integrity` for where each of those
    checks sits.
    """

    def __init__(self) -> None:
        super().__init__(
            "plan_integrity_mismatch",
            "This plan no longer matches what was approved. Analyse and review again.",
            status=409,
        )


async def verify_plan_integrity(
    connection: AsyncConnection, plan_id: uuid.UUID, plan_digest: str
) -> list[dict[str, Any]]:
    """Load the stored plan and prove it is still the one that was approved.

    Runs TWICE on the way to a mutation, and both are load-bearing:

    - once before anything else the apply does — before the receipt lookup,
      before a single provider call — so a tampered plan costs nothing and
      cannot be replayed past the check by reusing an idempotency key;
    - once inside the mutation transaction, before the claim, because the
      provider round-trip between the two takes real time and a plan can be
      rewritten during it.

    Returns the items, so the caller that is about to apply them does not
    read them a second time and risk checking one list and applying another.
    """
    items = await _approved_items(connection, plan_id)
    if _recompute_digest(items) != plan_digest:
        raise PlanIntegrityError()
    return items


def _stored_mapping(value: Any) -> dict[str, Any]:
    """A stored JSON object, or a bounded refusal.

    `dict()` raises `ValueError`/`TypeError` on a string or a list, which
    would leave a 500 for what is really a detected tamper: a plan item
    whose `detail` is no longer an object cannot hash to the digest that was
    approved. Refusing here says that in the same words the digest check
    uses, instead of as an unhandled exception.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    raise PlanIntegrityError()


def _recompute_digest(items: list[dict[str, Any]]) -> str:
    """Rebuild the plan's digest from what is stored, the same way it was made.

    The reconstruction has to use the SAME canonical shape `Plan.digest()`
    used, or this check would fail on plans nobody touched — and a check
    that cries wolf gets turned off.
    """
    rebuilt = Plan(
        items=[
            PlanItem(
                entity_kind=item["entity_kind"],
                action=item["action"],
                item_key=item["item_key"],
                proposed_name=item["proposed_name"],
                existing_entity_id=(
                    str(item["existing_entity_id"])
                    if item["existing_entity_id"] is not None
                    else None
                ),
                reason_code=item["reason_code"],
                payload=item["payload"],
                changes=item["changes"],
            )
            for item in items
        ]
    )
    return rebuilt.digest()


async def _approved_items(connection: AsyncConnection, plan_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await connection.execute(
            text(
                "SELECT entity_kind, action, item_key, proposed_name, existing_entity_id, "
                "detail, reason_code FROM onboarding_plan_items WHERE plan_id = :id "
                "ORDER BY item_key"
            ),
            {"id": plan_id},
        )
    ).all()
    return [
        {
            "entity_kind": str(row[0]),
            "action": str(row[1]),
            "item_key": str(row[2]),
            "proposed_name": row[3],
            "existing_entity_id": row[4],
            "detail": _stored_mapping(row[5]),
            # The approved values. A handler reads these and re-reads
            # nothing: the manifest, the analysis and the live request are
            # all mutable between approval and apply, and this is not.
            "payload": _stored_mapping(_stored_mapping(row[5]).get("payload")),
            "changes": _stored_mapping(_stored_mapping(row[5]).get("changes")),
            "reason_code": row[6],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# apply handlers — one per (entity kind, action) the plan may propose
# ---------------------------------------------------------------------------


async def _apply_project_create(context: _ApplyContext, item: dict[str, Any]) -> None:
    # Every value from the APPROVED payload. Reading the manifest here would
    # mean the values applied are whatever it says NOW — the digest check
    # proves the manifest is unchanged, not that this plan was built from
    # this reading of it.
    payload = item["payload"]
    project = await context.catalog.create_project(
        str(payload["project_key"]),
        str(payload.get("display_name") or payload["project_key"]),
        repo_provider=str(payload.get("repo_provider") or ""),
        repo_owner=str(payload.get("repo_owner") or ""),
        repo_name=str(payload.get("repo_name") or ""),
        default_branch=str(payload.get("default_branch") or ""),
        criticality=str(payload.get("criticality") or "low"),
        tenant_model=str(payload.get("tenant_model") or ""),
        # Owner teams are created with the project. The plan says so on its
        # own `owner_team` items rather than leaving it implicit.
        owners=[
            (str(owner["team"]), str(owner.get("role") or "primary"))
            for owner in payload.get("owners") or []
        ],
        source_ref=context.source_ref,
        source_revision=context.commit_sha,
    )
    context.project_id, context.project_scope = project.id, project.scope_id
    context.counters.created += 1
    # Declared on this item in the plan, so registering them is not a
    # mutation the plan failed to mention.
    for integration_type in PLACEHOLDER_INTEGRATIONS:
        await context.catalog.register_integration(integration_type, project.scope_id)
    _ = item


async def _resolve_project(context: _ApplyContext, item: dict[str, Any]) -> uuid.UUID:
    row = (
        await context.connection.execute(
            text("SELECT id, scope_id FROM projects WHERE project_key = :key"),
            {"key": str(item["proposed_name"])},
        )
    ).one()
    context.project_id = uuid.UUID(str(row[0]))
    context.project_scope = uuid.UUID(str(row[1]))
    return context.project_id


async def _apply_project_link(context: _ApplyContext, item: dict[str, Any]) -> None:
    """Take ownership of an unclaimed project row. Nothing about it changes."""
    await _resolve_project(context, item)
    context.counters.linked += 1


async def _apply_project_update(context: _ApplyContext, item: dict[str, Any]) -> None:
    project_id = await _resolve_project(context, item)
    # Only the fields the APPROVED payload carries, intersected with the
    # mutable allowlist. A field that appeared in the manifest afterwards is
    # not in the payload and is therefore not applied.
    updates = {
        name: value for name, value in item["payload"].items() if name in MUTABLE_PROJECT_FIELDS
    }
    if not updates:
        context.counters.unchanged += 1
        return
    assignments = ", ".join(f"{name} = :{name}" for name in sorted(updates))
    await context.connection.execute(
        text(
            f"UPDATE projects SET {assignments}, source_revision = :revision, "  # noqa: S608
            "version = version + 1, updated_at = now() WHERE id = :id"
        ),
        {**updates, "id": project_id, "revision": context.commit_sha},
    )
    context.counters.metadata_updated += 1


async def _apply_environment_create(context: _ApplyContext, item: dict[str, Any]) -> None:
    payload = item["payload"]
    environment_key = str(payload["environment_key"])
    assert context.project_id is not None
    cluster_id = await _require_cluster(context, payload)
    entity = await context.catalog.create_environment(
        context.project_id,
        environment_key,
        runtime=str(payload["runtime"]),
        branch=str(payload.get("branch") or ""),
        criticality=str(payload["criticality"]),
        cluster_id=cluster_id,
        namespace=payload.get("namespace"),
        source_ref=context.source_ref,
        source_revision=context.commit_sha,
    )
    context.environments[environment_key] = entity.id
    context.planned_environments.add(environment_key)
    context.counters.created += 1


async def _require_cluster(context: _ApplyContext, payload: dict[str, Any]) -> uuid.UUID | None:
    """Re-checked here as well as in the plan: a cluster can be removed
    between review and apply, and a manifest never conjures infrastructure."""
    if str(payload.get("runtime") or "") != "kubernetes":
        return None
    row = (
        await context.connection.execute(
            text("SELECT id FROM clusters WHERE cluster_ref = :ref"),
            {"ref": str(payload.get("cluster_ref") or "")},
        )
    ).first()
    if row is None:
        raise OnboardingError(
            "unknown_cluster",
            "The manifest references a cluster that is not registered in Drake.",
        )
    return uuid.UUID(str(row[0]))


async def _resolve_environment(context: _ApplyContext, key: str) -> uuid.UUID:
    if key in context.environments:
        return context.environments[key]
    assert context.project_id is not None
    row = (
        await context.connection.execute(
            text("SELECT id FROM environments WHERE project_id = :p AND environment_key = :key"),
            {"p": context.project_id, "key": key},
        )
    ).one()
    resolved = uuid.UUID(str(row[0]))
    context.environments[key] = resolved
    return resolved


async def _apply_environment_link(context: _ApplyContext, item: dict[str, Any]) -> None:
    key = str(item["proposed_name"])
    await _resolve_environment(context, key)
    context.planned_environments.add(key)
    context.counters.linked += 1


async def _apply_environment_update(context: _ApplyContext, item: dict[str, Any]) -> None:
    key = str(item["proposed_name"])
    environment_id = await _resolve_environment(context, key)
    context.planned_environments.add(key)
    updates = {
        name: value for name, value in item["payload"].items() if name in MUTABLE_ENVIRONMENT_FIELDS
    }
    if not updates:
        context.counters.unchanged += 1
        return
    assignments = ", ".join(f"{name} = :{name}" for name in sorted(updates))
    await context.connection.execute(
        text(
            f"UPDATE environments SET {assignments}, source_revision = :revision, "  # noqa: S608
            "version = version + 1, updated_at = now() WHERE id = :id"
        ),
        {**updates, "id": environment_id, "revision": context.commit_sha},
    )
    context.counters.metadata_updated += 1


async def _resolve_service(context: _ApplyContext, key: str) -> uuid.UUID:
    if key in context.services:
        return context.services[key]
    assert context.project_id is not None
    row = (
        await context.connection.execute(
            text("SELECT id FROM service_definitions WHERE project_id = :p AND service_key = :k"),
            {"p": context.project_id, "k": key},
        )
    ).one()
    resolved = uuid.UUID(str(row[0]))
    context.services[key] = resolved
    return resolved


async def _apply_service_create(context: _ApplyContext, item: dict[str, Any]) -> None:
    payload = item["payload"]
    key = str(payload["service_key"])
    assert context.project_id is not None
    service_id = await context.catalog.create_service_definition(
        context.project_id,
        key,
        component=str(payload["component"]),
        runtime=str(payload["runtime"]),
        metrics_profile=str(payload["metrics_profile"]),
        workload_selector=payload.get("workload_selector") or {},
        health=payload.get("health") or {},
        source_ref=context.source_ref,
        source_revision=context.commit_sha,
    )
    context.services[key] = service_id
    context.counters.created += 1
    await _bind_to_environments(context, service_id)


async def _apply_service_link(context: _ApplyContext, item: dict[str, Any]) -> None:
    service_id = await _resolve_service(context, str(item["proposed_name"]))
    context.counters.linked += 1
    await _bind_to_environments(context, service_id)


async def _apply_service_update(context: _ApplyContext, item: dict[str, Any]) -> None:
    service_id = await _resolve_service(context, str(item["proposed_name"]))
    updates = {
        name: value for name, value in item["payload"].items() if name in MUTABLE_SERVICE_FIELDS
    }
    await _bind_to_environments(context, service_id)
    if not updates:
        context.counters.unchanged += 1
        return
    assignments = ", ".join(
        f"{name} = CAST(:{name} AS jsonb)"
        if name in ("workload_selector", "health")
        else f"{name} = :{name}"
        for name in sorted(updates)
    )
    parameters = {
        name: json.dumps(value) if name in ("workload_selector", "health") else value
        for name, value in updates.items()
    }
    await context.connection.execute(
        text(
            f"UPDATE service_definitions SET {assignments}, "  # noqa: S608
            "source_revision = :revision, version = version + 1, "
            "updated_at = now() WHERE id = :id"
        ),
        {**parameters, "id": service_id, "revision": context.commit_sha},
    )
    context.counters.metadata_updated += 1


async def _bind_to_environments(context: _ApplyContext, service_id: uuid.UUID) -> None:
    """Attach the service to every environment this manifest declares.

    Idempotent in the catalog service, so a repeated apply attaches nothing
    twice.
    """
    # The environments the PLAN acted on, not the manifest's list. Reading
    # the manifest here would reintroduce exactly the coupling this slice
    # removed: an environment added after approval would get a binding
    # nobody reviewed.
    for environment_key in sorted(context.planned_environments):
        environment_id = await _resolve_environment(context, environment_key)
        # `bind_service` raises on a duplicate, which is right for the
        # catalog API and wrong for a re-apply. Checked here so a second
        # apply of the same plan is a no-op rather than an error.
        already = (
            await context.connection.execute(
                text(
                    "SELECT 1 FROM environment_services "
                    "WHERE environment_id = :e AND service_id = :s"
                ),
                {"e": environment_id, "s": service_id},
            )
        ).first()
        if already is None:
            await context.catalog.bind_service(environment_id, service_id)


async def _apply_binding_create(context: _ApplyContext, item: dict[str, Any]) -> None:
    """One service → workload binding, from an OBSERVED workload only.

    Everything here comes from the approved plan item, which the planner
    built from `inventory_resources`. Nothing is inferred at apply time.
    """
    payload = item["payload"]
    environment_key = str(payload["environment_key"])
    service_key = str(payload["service_key"])
    environment_id = await _resolve_environment(context, environment_key)
    service_id = await _resolve_service(context, service_key)
    assert context.project_id is not None

    row = (
        await context.connection.execute(
            text(
                """
                SELECT es.id, c.id
                FROM environment_services es
                JOIN environments e ON e.id = es.environment_id
                JOIN clusters c ON c.cluster_ref = :cluster
                WHERE es.environment_id = :environment AND es.service_id = :service
                """
            ),
            {
                "environment": environment_id,
                "service": service_id,
                "cluster": str(payload.get("cluster_ref") or ""),
            },
        )
    ).first()
    if row is None:
        raise OnboardingError(
            "binding_target_missing",
            "The service is not bound into this environment, so it cannot be bound to a workload.",
        )

    inserted = (
        await context.connection.execute(
            text(
                """
                INSERT INTO service_workload_bindings
                    (environment_service_id, project_id, environment_id, service_id,
                     cluster_id, namespace, workload_kind, workload_name,
                     preset_key, health_policy_key)
                VALUES (:es, :project, :environment, :service, :cluster, :namespace,
                        :kind, :name, :preset, :policy)
                -- A repeated apply finds the same target and changes nothing.
                ON CONFLICT (environment_service_id, cluster_id, namespace,
                             workload_kind, workload_name) DO NOTHING
                RETURNING id
                """
            ),
            {
                "es": row[0],
                "project": context.project_id,
                "environment": environment_id,
                "service": service_id,
                "cluster": row[1],
                "namespace": str(payload.get("namespace") or ""),
                "kind": str(payload.get("workload_kind") or ""),
                "name": str(payload.get("workload_name") or ""),
                "preset": DEFAULT_PRESET_KEY,
                "policy": DEFAULT_POLICY_KEY,
            },
        )
    ).first()
    if inserted is None:
        context.counters.unchanged += 1
        return
    context.counters.bindings_created += 1


async def _apply_repository_link(context: _ApplyContext, item: dict[str, Any]) -> None:
    assert context.project_id is not None
    await context.connection.execute(
        text(
            "INSERT INTO github_repository_projects "
            "(repository_id, project_id, scope_id, commit_sha, manifest_digest) "
            "VALUES (:repo, :project, :scope, :commit, :digest) "
            "ON CONFLICT (repository_id) DO NOTHING"
        ),
        {
            "repo": context.repository.row_id,
            "project": context.project_id,
            "scope": context.repository.scope_id,
            "commit": context.commit_sha,
            "digest": context.manifest_digest,
        },
    )
    context.counters.linked += 1
    _ = item


async def _apply_slo_create(context: _ApplyContext, item: dict[str, Any]) -> None:
    await _write_slo(context, item, update=False)


async def _apply_slo_update(context: _ApplyContext, item: dict[str, Any]) -> None:
    await _write_slo(context, item, update=True)


async def _write_slo(context: _ApplyContext, item: dict[str, Any], *, update: bool) -> None:
    """Materialise one manifest SLO into `slo_definitions`.

    Sprint 11 read `spec.slos` to propose a profile and then stored nothing,
    so an operator could approve an objective Drake never recorded.

    Deliberately NOT deleting: an SLO removed from a manifest keeps its
    definition and its evaluation history. Retiring one is a separate,
    explicit decision — see the ADR.
    """
    values = item["payload"]
    key = str(values["slo_key"])
    assert context.project_id is not None

    service_ref = str(values["service_ref"])
    service_id = await _resolve_service(context, service_ref) if service_ref else None
    environment_service_id = None
    if service_id is not None:
        environment_service_id = (
            await context.connection.execute(
                text(
                    "SELECT id FROM environment_services WHERE project_id = :p "
                    "AND service_id = :s ORDER BY created_at LIMIT 1"
                ),
                {"p": context.project_id, "s": service_id},
            )
        ).scalar_one_or_none()

    template_key = indicator_template(str(values["indicator"]))
    if not template_key:
        # The planner refuses an unknown indicator, so reaching here means
        # the registry changed between plan and apply.
        raise OnboardingError("slo_indicator_unsupported", "Unsupported SLO indicator.")

    await context.connection.execute(
        text(
            """
            INSERT INTO slo_definitions
                (project_id, environment_id, service_id, environment_service_id, slo_key,
                 display_name, indicator, objective_ratio, window_seconds,
                 sli_template_key, burn_profile_key)
            VALUES (:project, NULL, :service, :es, :key, :name, :indicator, :objective,
                    :window, :template, :burn)
            ON CONFLICT (project_id, slo_key) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                indicator = EXCLUDED.indicator,
                objective_ratio = EXCLUDED.objective_ratio,
                window_seconds = EXCLUDED.window_seconds,
                service_id = EXCLUDED.service_id,
                environment_service_id = EXCLUDED.environment_service_id,
                -- A changed objective is a NEW version. Historical
                -- evaluations keep the version they were judged against.
                version = CASE
                    WHEN slo_definitions.objective_ratio <> EXCLUDED.objective_ratio
                      OR slo_definitions.window_seconds <> EXCLUDED.window_seconds
                    THEN slo_definitions.version + 1
                    ELSE slo_definitions.version
                END,
                updated_at = now()
            """
        ),
        {
            "project": context.project_id,
            "service": service_id,
            "es": environment_service_id,
            "key": key,
            "name": str(values["display_name"]),
            "indicator": str(values["indicator"]),
            "objective": values["objective_ratio"],
            "window": values["window_seconds"],
            "template": template_key,
            "burn": default_burn_profile(),
        },
    )
    if update:
        context.counters.slo_definitions_updated += 1
    else:
        context.counters.slo_definitions_created += 1


# The registry. An actionable plan item with no entry here stops the apply
# before any mutation — see `PlanNotApplicableError`.
_HANDLERS: dict[tuple[str, str], Any] = {
    (str(EntityKind.PROJECT), str(Action.CREATE)): _apply_project_create,
    (str(EntityKind.PROJECT), str(Action.LINK)): _apply_project_link,
    (str(EntityKind.PROJECT), str(Action.UPDATE_METADATA)): _apply_project_update,
    (str(EntityKind.ENVIRONMENT), str(Action.CREATE)): _apply_environment_create,
    (str(EntityKind.ENVIRONMENT), str(Action.LINK)): _apply_environment_link,
    (str(EntityKind.ENVIRONMENT), str(Action.UPDATE_METADATA)): _apply_environment_update,
    (str(EntityKind.SERVICE), str(Action.CREATE)): _apply_service_create,
    (str(EntityKind.SERVICE), str(Action.LINK)): _apply_service_link,
    (str(EntityKind.SERVICE), str(Action.UPDATE_METADATA)): _apply_service_update,
    (str(EntityKind.WORKLOAD_BINDING), str(Action.CREATE)): _apply_binding_create,
    (str(EntityKind.REPOSITORY), str(Action.LINK)): _apply_repository_link,
    (str(EntityKind.SLO_PROFILE), str(Action.CREATE)): _apply_slo_create,
    (str(EntityKind.SLO_PROFILE), str(Action.UPDATE_METADATA)): _apply_slo_update,
}

# A project exists before its environments; an environment before the
# services bound into it; the repository link last, once the project is real.
_ORDER: dict[str, int] = {
    str(EntityKind.PROJECT): 0,
    str(EntityKind.OWNER_TEAM): 1,
    str(EntityKind.ENVIRONMENT): 2,
    str(EntityKind.CLUSTER_BINDING): 3,
    str(EntityKind.NAMESPACE_BINDING): 4,
    str(EntityKind.SERVICE): 5,
    str(EntityKind.METRIC_PROFILE): 6,
    # After the service and its environment attachment both exist.
    str(EntityKind.WORKLOAD_BINDING): 6,
    str(EntityKind.SLO_PROFILE): 7,
    str(EntityKind.DEPLOYMENT_SOURCE): 8,
    str(EntityKind.REPOSITORY): 9,
}


async def cancel(
    engine: AsyncEngine, *, session_id: uuid.UUID, expected_version: int
) -> dict[str, Any]:
    async with engine.begin() as connection:
        if await _state_of(connection, session_id) == str(SessionState.IMPORTED):
            # An import already happened. Cancelling it would suggest the
            # catalog rows went away, and they did not — so this keeps its
            # own named refusal rather than the generic state error.
            raise OnboardingError("already_imported", "This session has already been applied.")
        session = await lock_session_for(connection, session_id, "cancel")
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
