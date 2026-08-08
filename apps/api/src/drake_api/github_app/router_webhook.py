"""GitHub App webhook endpoint (ADR-0019 §4/§5).

This route belongs to no user session: no cookie, no CSRF token, no
identity. Trust is the HMAC over the raw bytes and nothing else. The
order below is the security contract — read it top to bottom.
"""

import json as jsonlib
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from drake_api.db import get_engine
from drake_api.github_app import service
from drake_api.github_app.auth import GitHubAuthError, load_webhook_secret
from drake_api.github_app.webhook import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    SUPPORTED_EVENTS,
    WebhookRejectedError,
    build_envelope,
    check_ownership,
    default_branch_push,
    foreign_repositories,
    payload_digest,
    validate_delivery_id,
    validate_event_name,
    verify_signature,
)
from drake_api.onboarding.service import mark_stale_for_commit
from drake_api.settings import Settings

router = APIRouter(prefix="/v1/integrations/github", tags=["github-webhook"])


class _ForeignRepositoryError(Exception):
    """An announced repository outside the organization that we do not track."""

    def __init__(self, external_ids: list[int]) -> None:
        super().__init__("repository owner mismatch")
        self.external_ids = external_ids


def _refused(reason: str) -> HTTPException:
    # One bounded shape for every refusal: the caller learns nothing about
    # which check failed.
    return HTTPException(status_code=401, detail="webhook rejected")


async def _read_bounded_body(request: Request, limit: int) -> bytes:
    """Stream the raw body ONCE and stop the moment it exceeds the limit.

    `await request.body()` is not a limit: it buffers whatever arrives
    first and only then lets us measure it, so a chunked body or a lying
    `Content-Length` puts the entire payload in memory before any check
    runs. Reading chunk by chunk means at most `limit + 1` bytes are ever
    held, and the declared length is treated as a hint that lets us refuse
    early — never as the security boundary.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="webhook payload too large")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            # Refuse as soon as the ceiling is crossed. The partial body is
            # dropped rather than assembled.
            raise HTTPException(status_code=413, detail="webhook payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhook", status_code=202)
async def receive_webhook(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    if not settings.github_app_enabled:
        raise HTTPException(status_code=404, detail="not found")

    # 1. raw bytes, once, streamed under a hard ceiling.
    raw_body = await _read_bounded_body(request, settings.github_webhook_max_body_bytes)

    # 2-4. required headers, then the HMAC over those exact bytes.
    try:
        secret = load_webhook_secret(settings)
    except GitHubAuthError as error:
        raise HTTPException(status_code=503, detail="webhook verification unavailable") from error

    engine = get_engine(settings)
    try:
        verify_signature(raw_body, secret, request.headers.get(SIGNATURE_HEADER))
        delivery_id = validate_delivery_id(request.headers.get(DELIVERY_HEADER))
        event = validate_event_name(request.headers.get(EVENT_HEADER))
    except WebhookRejectedError as rejection:
        await service._audit(
            engine,
            action="github.webhook.rejected",
            result="denied",
            target_type="github_webhook",
            metadata={"reason": rejection.reason},
        )
        raise _refused(rejection.reason) from rejection

    # 5. ONLY now is the body parsed.
    try:
        payload = jsonlib.loads(raw_body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="webhook payload is not JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="webhook payload is not an object")

    if event not in SUPPORTED_EVENTS:
        # `ping` and friends: acknowledged, no domain work, no row.
        return {"status": "acknowledged", "event": event}

    envelope = build_envelope(event, payload)
    digest = payload_digest(raw_body)

    # 7. installation and owner identity, fail-closed: absent evidence is a
    # refusal, never a pass-through.
    try:
        check_ownership(envelope)
    except WebhookRejectedError as rejection:
        await service._audit(
            engine,
            action="github.webhook.ownership_rejected",
            result="denied",
            target_type="github_webhook",
            target_id=delivery_id,
            metadata={"reason": rejection.reason, "event": event},
        )
        raise _refused(rejection.reason) from rejection

    assert envelope.installation_external_id is not None  # guaranteed by check_ownership
    repository_external_id = (
        envelope.repositories[0]["external_id"] if envelope.repositories else None
    )

    # 8-9. Claim the delivery AND make its work durable in ONE transaction.
    # Only after this commits may the endpoint acknowledge anything.
    try:
        async with engine.begin() as connection:
            scope_id = await service.organization_scope_id(connection)
            await service.assert_installation_scope(
                connection, envelope.installation_external_id, scope_id
            )
            foreign = foreign_repositories(envelope)
            unknown = await service.unknown_repository_ids(connection, foreign) if foreign else []
            if unknown:
                raise _ForeignRepositoryError(unknown)
    except _ForeignRepositoryError as foreign_error:
        await service._audit(
            engine,
            action="github.webhook.ownership_rejected",
            result="denied",
            target_type="github_webhook",
            target_id=delivery_id,
            metadata={
                "reason": "repository_owner_mismatch",
                "event": event,
                "repositories": len(foreign_error.external_ids),
            },
        )
        raise _refused("repository_owner_mismatch") from foreign_error
    except service.InstallationScopeMismatchError as mismatch:
        # Audited outside the transaction it aborted, so the record
        # survives the rollback that refusing causes.
        await service._audit(
            engine,
            action="github.installation.scope_mismatch",
            result="denied",
            target_type="github_installation",
            target_id=str(envelope.installation_external_id),
            metadata={"reason": "scope_mismatch", "event": event},
        )
        raise _refused("installation_scope_mismatch") from mismatch

    async with engine.begin() as connection:
        outcome = await service.record_delivery(
            connection,
            delivery_id=delivery_id,
            event_type=event,
            payload_digest=digest,
            envelope=envelope.as_json(),
            installation_external_id=envelope.installation_external_id,
            repository_external_id=repository_external_id,
            scope_id=scope_id,
        )

    if outcome.status == "conflict":
        # Same delivery id, different bytes: a security signal, not a retry.
        # The stored row is evidence of the ORIGINAL delivery and is left
        # exactly as it was — a forger does not get to edit the record, nor
        # to close out honest work that is still pending.
        await service._audit(
            engine,
            action="github.webhook.replay_conflict",
            result="denied",
            target_type="github_webhook",
            target_id=delivery_id,
            metadata={"reason": "digest_mismatch", "event": event},
        )
        raise HTTPException(status_code=409, detail="webhook delivery conflict")

    if outcome.status == "duplicate":
        return {"status": "duplicate", "delivery_id": delivery_id}

    if outcome.status == "failed":
        # Dead-lettered earlier. Say so plainly: reporting `processed` or a
        # plain `duplicate` here would claim work that never happened, and
        # would invite an unbounded redelivery loop against the ceiling.
        return {
            "status": "failed",
            "delivery_id": delivery_id,
            "detail": "delivery exhausted its retry budget; operator action required",
        }

    # 10. Run the durable work item now. If this fails the row stays
    # `pending`, so a redelivery or the drain worker finishes it — the
    # event cannot be acknowledged into nothing.
    assert outcome.delivery_row_id is not None
    result = await service.process_delivery(engine, outcome.delivery_row_id)
    if result == "duplicate":
        return {"status": "duplicate", "delivery_id": delivery_id}
    if result == "failed":
        return {"status": "failed", "delivery_id": delivery_id}
    if result == "unsupported":
        # Acknowledged so GitHub stops retrying, and recorded as refused by
        # `process_delivery`. No success audit: nothing succeeded.
        return {
            "status": "unsupported",
            "delivery_id": delivery_id,
            "event": event,
            "action": envelope.action,
        }

    # A default-branch push invalidates reviews of the commit it replaced.
    # Nothing else: no catalog write, no provider call, no token.
    stale_plans = await _mark_plans_stale(engine, event, payload)

    await service._audit(
        engine,
        action=f"github.webhook.{event}",
        result="success",
        target_type="github_installation",
        target_id=str(envelope.installation_external_id),
        metadata={"event": event, "action": envelope.action, "stale_plans": stale_plans},
    )
    return {"status": "processed", "delivery_id": delivery_id}


async def _mark_plans_stale(engine: Any, event: str, payload: dict[str, Any]) -> int:
    """Mark onboarding plans stale when the branch they describe moves.

    Runs after the delivery is durable and outside its transaction, so a
    slow catalog update cannot hold the webhook open. Returning zero is the
    normal case: most pushes touch a repository nobody is onboarding.
    """
    if event != "push":
        return 0
    moved = default_branch_push(payload)
    if moved is None:
        return 0
    external_id, after = moved
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT id FROM github_repositories WHERE external_id = :external"),
                {"external": external_id},
            )
        ).first()
    if row is None:
        # A push for a repository Drake does not project. Fail closed by
        # doing nothing rather than by guessing which row it meant.
        return 0
    return await mark_stale_for_commit(
        engine, repository_row_id=uuid.UUID(str(row[0])), new_commit_sha=after
    )
