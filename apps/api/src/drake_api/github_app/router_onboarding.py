"""Sprint 5B catalog onboarding — retired (Sprint 12A.2a).

These five routes were the original path from a GitHub repository to a
catalog project: scan, validate, download a draft, import. They worked, and
they bypassed every guarantee Sprints 11 and 12A.1 were built to provide.

An import here produced catalog rows with:

- no plan, so nobody could see what it would do before it did it;
- no approval, so nobody accepted the values it wrote;
- no plan digest, so nothing could be checked afterwards;
- no apply receipt, so a repeated call was not idempotent;
- no plan/apply parity, so what it changed was whatever the code happened
  to do that day.

Leaving them reachable would mean the authoritative path is a convention
rather than a rule, and a convention is what somebody skips at 3am. So they
answer `410 Gone` — a tombstone, not a 404: the difference tells an operator
or an old client that this moved, rather than that they got the URL wrong.

Nothing else happens. No provider call, no token, no draft write, no catalog
mutation, no success audit. The repository id is not looked up at all, so
the response is identical for one that exists and one that never did.

The path forward is `/v1/onboarding/sessions`, and the manifest draft this
endpoint used to serve now lives at
`/v1/onboarding/sessions/{session_id}/manifest-draft`, built from a session's
own stored analysis.

The `OnboardingScanner` and `CatalogImporter` classes and the
`github_onboarding_drafts` table are deliberately still here. Retiring an
entry point is not the same as dropping the code behind it or the rows it
wrote, and a migration that deleted historical drafts would destroy a record
this change has no business touching.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from drake_api.auth.dependencies import AuthContext, require_auth

router = APIRouter(prefix="/v1/integrations/github", tags=["github-onboarding"])

RETIRED_CODE = "legacy_onboarding_retired"
_RETIRED_MESSAGE = (
    "This onboarding path is retired. Start a session at /v1/onboarding/sessions, "
    "which plans, reviews and approves the change before applying it."
)


def _gone() -> HTTPException:
    """One bounded refusal, identical for every repository id."""
    return HTTPException(
        status_code=410,
        detail={
            "code": RETIRED_CODE,
            "message": _RETIRED_MESSAGE,
            # A pointer, not a redirect: an old client should be changed by a
            # person who understands the new flow, not followed automatically
            # into a mutation with different semantics.
            "replacement": "/v1/onboarding/sessions",
        },
    )


@router.get("/repositories/{repository_id}/onboarding")
async def get_onboarding(
    request: Request,
    repository_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    raise _gone()


@router.post("/repositories/{repository_id}/onboarding/scan")
async def start_scan(
    request: Request,
    repository_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    raise _gone()


@router.post("/repositories/{repository_id}/onboarding/validate")
async def validate_manifest(
    request: Request,
    repository_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    raise _gone()


@router.get("/repositories/{repository_id}/onboarding/download")
async def download_draft(
    request: Request,
    repository_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    raise _gone()


@router.post("/repositories/{repository_id}/onboarding/import")
async def import_repository(
    request: Request,
    repository_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    raise _gone()
