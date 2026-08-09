"""Onboarding endpoints.

The read surface is read-only: no endpoint here analyses a repository,
builds a plan, or writes a catalog row as a side effect of being called.

The mutations are the state machine, and each one is narrow:

    create   → open a session for a repository Drake already projects
    analyze  → one bounded static analysis at one commit
    approve  → accept one exact plan VERSION
    apply    → materialise that version, once
    cancel   → close the session
    gitops   → propose a manifest as a pull request

What no endpoint accepts, anywhere: a repository URL, an owner/name pair, a
branch, a file path, a manifest body, a plan item, a catalog id, a cluster,
a permission, or a provider address. A repository is chosen from Drake's own
projection by its row id; everything else is server-composed.

Four permissions, deliberately separate. Being able to see a session is not
being able to analyse one; being able to approve is not being able to apply;
and being able to apply to Drake is not being able to propose a change to
someone's repository.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.github_app.onboarding_service import OnboardingError
from drake_api.onboarding import gitops as gitops_module
from drake_api.onboarding import repository as repo
from drake_api.onboarding import service
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.onboarding")

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])

_NOT_FOUND = "not found"


class CreateSessionBody(BaseModel):
    """A repository row id. Not a URL, not an owner/name, not a branch."""

    model_config = ConfigDict(extra="forbid")
    repository_id: uuid.UUID


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class GitOpsBody(BaseModel):
    """Deliberately empty of targets.

    There is no field for a branch, a path, a base repository or file
    content: a caller who could choose them could write anywhere the
    installation can reach.
    """

    model_config = ConfigDict(extra="forbid")


def _failure(error: OnboardingError) -> HTTPException:
    """A typed, bounded refusal. Never a provider message."""
    return HTTPException(
        status_code=error.status, detail={"code": error.code, "message": str(error)}
    )


async def _require(request: Request, auth: AuthContext, permission: str) -> None:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        if not await repo.can(connection, auth.principal, permission):
            # The same 404 an unknown session produces: a 403 would confirm
            # the session exists.
            raise HTTPException(status_code=404, detail=_NOT_FOUND)


# ---------------------------------------------------------------------------
# integration status
# ---------------------------------------------------------------------------


@router.get("/github/status")
async def github_status(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Whether onboarding can run at all, and why not when it cannot.

    A deployment with no GitHub App answers `not_configured` and stops
    there: no token is minted, no call is made, no repository list is
    invented, and nothing cached is presented as fresh.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    missing: list[str] = []
    if not settings.github_app_enabled:
        missing.append("feature_disabled")
    if not (settings.github_app_client_id or settings.github_app_id):
        missing.append("app_identity")
    if not settings.github_app_private_key_file:
        missing.append("private_key_reference")
    if not settings.github_webhook_secret_file:
        missing.append("webhook_secret_reference")

    async with engine.connect() as connection:
        evidence = await repo.integration_evidence(connection, auth.principal)
        can_manage = await repo.can(connection, auth.principal, repo.MANAGE_PERMISSION)
        can_apply = await repo.can(connection, auth.principal, repo.APPLY_PERMISSION)
        can_gitops = await repo.can(connection, auth.principal, repo.GITOPS_PERMISSION)

    return {
        "configuration_state": "not_configured" if missing else "configured",
        # Which reference is absent — never its name and never its value.
        "missing_operator_inputs": missing,
        "gitops_pr_enabled": settings.github_gitops_pr_enabled,
        "can_manage": can_manage,
        "can_apply": can_apply,
        "can_gitops": can_gitops,
        **evidence,
    }


@router.get("/filters")
async def onboarding_filters(_auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    return repo.filter_options()


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def list_sessions(
    request: Request,
    state: str | None = Query(default=None, max_length=32),
    repository_id: uuid.UUID | None = None,
    limit: int = Query(default=repo.DEFAULT_PAGE_SIZE, ge=1, le=repo.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        try:
            return await repo.list_sessions(
                connection,
                auth.principal,
                state=state,
                repository_id=repository_id,
                limit=limit,
                offset=offset,
            )
        except repo.FilterError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/sessions/{session_id}")
async def get_session(
    request: Request, session_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        session = await repo.get_session(connection, auth.principal, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        session["gitops_requests"] = await repo.session_gitops(
            connection, auth.principal, session_id
        )
    return session


@router.get("/sessions/{session_id}/findings")
async def session_findings(
    request: Request, session_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        findings = await repo.session_findings(connection, auth.principal, session_id)
    if findings is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return findings


@router.get("/sessions/{session_id}/plan")
async def session_plan(
    request: Request,
    session_id: uuid.UUID,
    plan_version: int | None = Query(default=None, ge=1),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        plan = await repo.session_plan(
            connection, auth.principal, session_id, plan_version=plan_version
        )
    if plan is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return plan


# ---------------------------------------------------------------------------
# mutations
# ---------------------------------------------------------------------------


@router.post("/sessions", status_code=201)
async def create_session(
    request: Request, payload: CreateSessionBody, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """Open a session for a repository Drake already projects."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    await _require(request, auth, repo.MANAGE_PERMISSION)

    from sqlalchemy import text

    async with engine.connect() as connection:
        scopes = await repo.scopes_for(connection, auth.principal, repo.MANAGE_PERMISSION)
        visible = (
            await connection.execute(
                text(
                    "SELECT id FROM github_repositories WHERE id = :id AND scope_id = ANY(:scopes)"
                ),
                {"id": payload.repository_id, "scopes": scopes},
            )
        ).first()
    if visible is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    try:
        result = await service.create_session(
            engine,
            settings,
            repository_row_id=payload.repository_id,
            actor_identity_id=uuid.UUID(auth.session.identity_id),
        )
    except OnboardingError as error:
        raise _failure(error) from error

    if result["created"]:
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action="onboarding.session.create",
                result="success",
                target_type="onboarding_session",
                target_id=result["session_id"],
                correlation_id=correlation_id_var.get(),
                metadata={"repository_id": str(payload.repository_id)},
            ),
        )
    return result


@router.post("/sessions/{session_id}/analyze", status_code=202)
async def analyze(
    request: Request, session_id: uuid.UUID, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """One bounded static analysis, at one immutable commit.

    Nothing from the repository is executed: an allowlist of metadata files
    is read through the Contents API under hard budgets. A budget stop is
    reported as partial, never smoothed into a complete result.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    await _require(request, auth, repo.MANAGE_PERMISSION)

    async with engine.connect() as connection:
        if await repo.get_session(connection, auth.principal, session_id) is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

    client = getattr(request.app.state, "github_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="github client unavailable")

    try:
        result = await service.analyze(engine, settings, client, session_id=session_id)
    except OnboardingError as error:
        raise _failure(error) from error

    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="onboarding.analyze",
            result="success",
            target_type="onboarding_session",
            target_id=str(session_id),
            correlation_id=correlation_id_var.get(),
            metadata={
                "analysis_id": result["analysis_id"],
                "reused": result["reused"],
                "truncated": result["truncated"],
            },
        ),
    )
    return result


@router.post("/sessions/{session_id}/approve")
async def approve(
    request: Request,
    session_id: uuid.UUID,
    payload: ApproveBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Approve one exact plan version.

    Approval is separate from apply on purpose: reviewing a proposal and
    executing it are different decisions, and in most organizations they
    are different people.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    await _require(request, auth, repo.MANAGE_PERMISSION)

    async with engine.connect() as connection:
        if await repo.get_session(connection, auth.principal, session_id) is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

    try:
        result = await service.approve(
            engine,
            session_id=session_id,
            plan_version=payload.plan_version,
            expected_version=payload.expected_version,
            actor_identity_id=uuid.UUID(auth.session.identity_id),
        )
    except OnboardingError as error:
        raise _failure(error) from error

    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="onboarding.approve",
            result="success",
            target_type="onboarding_session",
            target_id=str(session_id),
            correlation_id=correlation_id_var.get(),
            metadata={"plan_version": payload.plan_version},
        ),
    )
    return result


@router.post("/sessions/{session_id}/apply")
async def apply(
    request: Request,
    session_id: uuid.UUID,
    payload: ApplyBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Materialise an approved plan version, once.

    `onboarding.apply` is its own permission: someone who may review a
    proposal is not automatically someone who may write to the catalog.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    await _require(request, auth, repo.APPLY_PERMISSION)

    async with engine.connect() as connection:
        if await repo.get_session(connection, auth.principal, session_id) is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

    client = getattr(request.app.state, "github_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="github client unavailable")

    try:
        outcome = await service.apply(
            engine,
            settings,
            client,
            session_id=session_id,
            plan_version=payload.plan_version,
            idempotency_key=payload.idempotency_key,
            actor_identity_id=uuid.UUID(auth.session.identity_id),
            correlation_id=correlation_id_var.get() or "",
        )
    except OnboardingError as error:
        raise _failure(error) from error

    # The audit row is written INSIDE the apply transaction (see
    # `_materialise`), not here. Writing it afterwards would mean a failed
    # audit leaves a committed catalog change nobody can discover — and a
    # safe retry returns the recorded outcome without a second row, because
    # the transaction that would have written one never runs again.
    #
    # Normalized result only: no manifest, no provider body, no repository
    # content. Every counter is from the COMMITTED transaction — a rollback
    # raises rather than returning partial numbers.
    #
    # A retry replays the stored receipt. Receipts written before migration
    # 0020 never recorded the four extended counters, so those come back as
    # `null` — "this was not recorded" — rather than as a zero that reads
    # like measured work. They are not reconstructed from audit metadata:
    # audit records what happened, not how many rows a counter reached, and
    # inferring one from the other would produce a confident wrong number.
    unrecorded = not outcome.counters_complete
    return {
        "outcome": outcome.outcome,
        "project_id": str(outcome.project_id) if outcome.project_id else None,
        "created_entities": outcome.created,
        "linked_entities": outcome.linked,
        "unchanged_entities": outcome.unchanged,
        "no_change_count": outcome.unchanged,
        "metadata_updated": None if unrecorded else outcome.metadata_updated,
        "slo_definitions_created": None if unrecorded else outcome.slo_definitions_created,
        "slo_definitions_updated": None if unrecorded else outcome.slo_definitions_updated,
        "bindings_created": None if unrecorded else outcome.bindings_created,
    }


@router.post("/sessions/{session_id}/cancel")
async def cancel(
    request: Request,
    session_id: uuid.UUID,
    payload: CancelBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    await _require(request, auth, repo.MANAGE_PERMISSION)

    async with engine.connect() as connection:
        if await repo.get_session(connection, auth.principal, session_id) is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

    try:
        result = await service.cancel(
            engine, session_id=session_id, expected_version=payload.expected_version
        )
    except OnboardingError as error:
        raise _failure(error) from error

    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="onboarding.cancel",
            result="success",
            target_type="onboarding_session",
            target_id=str(session_id),
            correlation_id=correlation_id_var.get(),
            metadata={},
        ),
    )
    return result


@router.post("/sessions/{session_id}/gitops-request", status_code=202)
async def gitops_request(
    request: Request,
    session_id: uuid.UUID,
    _payload: GitOpsBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Propose `.drake/project.yaml` to the repository, as a pull request.

    `202` and `pending`: the request is recorded and audited, and the worker
    calls GitHub afterwards. Answering with an open pull request before the
    provider agreed would tell an operator something that is not true.

    Merging the resulting pull request does not import anything into Drake.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    await _require(request, auth, repo.GITOPS_PERMISSION)

    async with engine.connect() as connection:
        if await repo.get_session(connection, auth.principal, session_id) is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

    try:
        result = await gitops_module.request_pull_request(
            engine,
            settings,
            session_id=session_id,
            actor_identity_id=uuid.UUID(auth.session.identity_id),
        )
    except OnboardingError as error:
        raise _failure(error) from error

    if result["created"]:
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action="onboarding.gitops.request",
                result="success",
                target_type="onboarding_session",
                target_id=str(session_id),
                correlation_id=correlation_id_var.get(),
                metadata={"gitops_request_id": result["id"]},
            ),
        )
    return result
