"""Catalog onboarding endpoints (Sprint 5B).

Reads need the integration read permission; scanning, validating and
importing need `integration.manage` plus CSRF, because each of them either
talks to the provider or writes to the catalog.

An unauthorized repository id and an unknown one return the same 404, so
the endpoint cannot be used to discover which repositories exist.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.audit import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.db import get_engine
from drake_api.github_app import manifest, onboarding_service, scanner
from drake_api.github_app.client import GitHubError
from drake_api.github_app.router import (
    _as_of,
    _manageable_scope_ids,
    _readable_scope_ids,
)
from drake_api.settings import Settings

router = APIRouter(prefix="/v1/integrations/github", tags=["github-onboarding"])


def _not_found() -> HTTPException:
    # Same answer for "does not exist" and "not yours".
    return HTTPException(status_code=404, detail="not found")


async def _visible_repository(
    connection: AsyncConnection, repository_id: uuid.UUID, scopes: list[uuid.UUID]
) -> uuid.UUID:
    row = (
        await connection.execute(
            text("SELECT id FROM github_repositories WHERE id = :id AND scope_id = ANY(:scopes)"),
            {"id": repository_id, "scopes": scopes},
        )
    ).first()
    if row is None:
        raise _not_found()
    return uuid.UUID(str(row[0]))


class ValidateRequest(BaseModel):
    """A manifest the operator is editing, checked without being trusted."""

    content: str = Field(max_length=manifest.MAX_MANIFEST_BYTES)


def _draft_payload(draft: dict[str, Any] | None, repository_id: uuid.UUID) -> dict[str, Any]:
    if draft is None:
        return {
            "repository_id": str(repository_id),
            "state": "not_started",
            "commit_sha": "",
            "manifest_source": "none",
            "findings": [],
            "discovery": {},
            "operator_inputs_required": [],
            "importable": False,
            "as_of": _as_of(),
        }
    importable = draft["state"] == "ready_to_import" and draft["manifest_source"] == "repository"
    return {
        "repository_id": str(repository_id),
        "state": draft["state"],
        "commit_sha": draft["commit_sha"],
        "manifest_source": draft["manifest_source"],
        "manifest_digest": draft["manifest_digest"],
        "findings": draft["findings"],
        "discovery": draft["discovery"],
        "draft_manifest": draft["draft_manifest"],
        "reason_code": draft["reason_code"],
        "accepted_project_id": draft["accepted_project_id"],
        "accepted_at": draft["accepted_at"],
        "scanned_at": draft["scanned_at"],
        "revision": draft["revision"],
        "operator_inputs_required": scanner.missing_operator_inputs(draft["draft_manifest"]),
        # The one field the UI needs to decide whether Import is live.
        "importable": importable,
        "as_of": _as_of(),
    }


@router.get("/repositories/{repository_id}/onboarding")
async def get_onboarding(
    request: Request, repository_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """The current draft. Read-only users may look."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _readable_scope_ids(connection, auth)
        await _visible_repository(connection, repository_id, scopes)
        draft = await onboarding_service.load_draft(connection, repository_id)
    return _draft_payload(draft, repository_id)


@router.post("/repositories/{repository_id}/onboarding/scan", status_code=202)
async def start_scan(
    request: Request, repository_id: uuid.UUID, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """Run one bounded static scan at the current default-branch commit."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _manageable_scope_ids(connection, auth)
        await _visible_repository(connection, repository_id, scopes)

    onboarding = getattr(request.app.state, "github_onboarding_scanner", None)
    if onboarding is None:
        raise HTTPException(status_code=503, detail="github integration is not configured")

    try:
        draft = await onboarding.scan(repository_id)
    except onboarding_service.OnboardingError as refusal:
        await _audit(
            request,
            auth,
            action="github.onboarding.scan",
            result="denied",
            repository_id=repository_id,
            metadata={"reason": refusal.code},
        )
        raise HTTPException(status_code=refusal.status, detail=str(refusal)) from refusal
    except GitHubError as error:
        failure = onboarding_service.provider_failure(error)
        raise HTTPException(status_code=failure.status, detail=str(failure)) from error

    await _audit(
        request,
        auth,
        action="github.onboarding.scan",
        result="success",
        repository_id=repository_id,
        metadata={
            "state": draft["state"],
            "commit_sha": draft["commit_sha"],
            "manifest_source": draft["manifest_source"],
            "files": len(draft["discovery"].get("files", [])),
        },
    )
    return _draft_payload(draft, repository_id)


@router.post("/repositories/{repository_id}/onboarding/validate")
async def validate_manifest(
    request: Request,
    repository_id: uuid.UUID,
    body: ValidateRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Validate an edited draft. Authoritative, and never an import path."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _manageable_scope_ids(connection, auth)
        await _visible_repository(connection, repository_id, scopes)
        context = await onboarding_service.load_repository_context(connection, repository_id)

    result = manifest.validate_content(body.content)
    findings = [finding.as_json() for finding in result.findings]
    if result.valid and result.document is not None:
        findings.extend(
            finding.as_json()
            for finding in manifest.check_repository_identity(
                result.document, context.owner, context.name, context.default_branch
            )
        )
    return {
        "repository_id": str(repository_id),
        "valid": not findings,
        "findings": findings,
        # Even a perfect edited manifest is not importable: ADR-0007 says
        # the repository is the source of intent.
        "importable": False,
        "next_step": (
            f"Commit this file to the repository as {manifest.MANIFEST_PATH}, then scan again."
        ),
        "as_of": _as_of(),
    }


@router.get("/repositories/{repository_id}/onboarding/download")
async def download_draft(
    request: Request, repository_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> Response:
    """The generated draft, as a file to commit to the repository."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _readable_scope_ids(connection, auth)
        await _visible_repository(connection, repository_id, scopes)
        draft = await onboarding_service.load_draft(connection, repository_id)
    if draft is None or not draft["draft_manifest"]:
        raise _not_found()
    return Response(
        content=draft["draft_manifest"],
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="project.yaml"'},
    )


@router.post("/repositories/{repository_id}/onboarding/import", status_code=201)
async def import_repository(
    request: Request,
    repository_id: uuid.UUID,
    auth: AuthContext = Depends(require_csrf),
    idempotency_key: Annotated[str | None, Field()] = None,
) -> dict[str, Any]:
    """Import the reviewed repository manifest into the catalog, atomically."""
    if not request.headers.get("Idempotency-Key"):
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        scopes = await _manageable_scope_ids(connection, auth)
        await _visible_repository(connection, repository_id, scopes)

    importer = getattr(request.app.state, "github_catalog_importer", None)
    if importer is None:
        raise HTTPException(status_code=503, detail="github integration is not configured")

    try:
        outcome = await importer.import_repository(repository_id, auth.principal.identity_id)
    except onboarding_service.OnboardingError as refusal:
        await _audit(
            request,
            auth,
            action="github.onboarding.import",
            result="denied",
            repository_id=repository_id,
            metadata={"reason": refusal.code},
        )
        raise HTTPException(status_code=refusal.status, detail=str(refusal)) from refusal
    except GitHubError as error:
        failure = onboarding_service.provider_failure(error)
        raise HTTPException(status_code=failure.status, detail=str(failure)) from error

    if outcome.created:
        # Audited only when something actually happened: repeating the
        # request must not write a second record of the same import.
        await _audit(
            request,
            auth,
            action="github.onboarding.import",
            result="success",
            repository_id=repository_id,
            metadata={"project_key": outcome.project_key, "project_id": str(outcome.project_id)},
        )
    return {
        "repository_id": str(repository_id),
        "project_id": str(outcome.project_id),
        "project_key": outcome.project_key,
        "created": outcome.created,
        "as_of": _as_of(),
    }


async def _audit(
    request: Request,
    auth: AuthContext,
    *,
    action: str,
    result: str,
    repository_id: uuid.UUID,
    metadata: dict[str, Any],
) -> None:
    settings: Settings = request.app.state.settings
    await record_audit_event(
        get_engine(settings),
        AuditEventData(
            action=action,
            result=result,
            actor_type="user",
            actor_id=str(auth.principal.identity_id),
            target_type="github_repository",
            target_id=str(repository_id),
            metadata=metadata,
        ),
    )
