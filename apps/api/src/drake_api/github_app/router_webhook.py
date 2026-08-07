"""GitHub App webhook endpoint (ADR-0019 §4/§5).

This route belongs to no user session: no cookie, no CSRF token, no
identity. Trust is the HMAC over the raw bytes and nothing else. The
order below is the security contract — read it top to bottom.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from drake_api.db import get_engine
from drake_api.github_app import catalog, onboarding, service
from drake_api.github_app.auth import GitHubAuthError, load_webhook_secret
from drake_api.github_app.webhook import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    SUPPORTED_EVENTS,
    WebhookRejectedError,
    build_envelope,
    payload_digest,
    validate_delivery_id,
    validate_event_name,
    verify_signature,
)
from drake_api.settings import Settings

router = APIRouter(prefix="/v1/integrations/github", tags=["github-webhook"])


def _refused(reason: str) -> HTTPException:
    # One bounded shape for every refusal: the caller learns nothing about
    # which check failed.
    return HTTPException(status_code=401, detail="webhook rejected")


async def _read_bounded_body(request: Request, limit: int) -> bytes:
    """Read the raw body ONCE, under a hard ceiling."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="webhook payload too large")
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status_code=413, detail="webhook payload too large")
    return body


async def _organization_scope_id(connection: Any) -> uuid.UUID:
    row = (
        await connection.execute(
            text(
                "SELECT id FROM scopes WHERE scope_type = 'organization' AND external_ref = 'root'"
            )
        )
    ).first()
    if row is None:  # pragma: no cover - bootstrap invariant
        raise HTTPException(status_code=503, detail="organization scope is not seeded")
    return uuid.UUID(str(row[0]))


@router.post("/webhook", status_code=202)
async def receive_webhook(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    if not settings.github_app_enabled:
        raise HTTPException(status_code=404, detail="not found")

    # 1. raw bytes, once, bounded.
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
    import json as jsonlib

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

    # 7. installation/owner consistency.
    if envelope.installation_external_id is None:
        await service._audit(
            engine,
            action="github.webhook.rejected",
            result="denied",
            target_type="github_webhook",
            metadata={"reason": "installation_missing", "event": event},
        )
        raise _refused("installation_missing")
    if envelope.account_login and envelope.account_login.lower() != catalog.ORGANIZATION.lower():
        await service._audit(
            engine,
            action="github.webhook.ownership_mismatch",
            result="denied",
            target_type="github_webhook",
            metadata={"reason": "owner_mismatch", "event": event},
        )
        raise _refused("owner_mismatch")

    repository_external_id = (
        envelope.repositories[0]["external_id"] if envelope.repositories else None
    )

    async with engine.begin() as connection:
        outcome = await service.record_delivery(
            connection,
            delivery_id=delivery_id,
            event_type=event,
            payload_digest=digest,
            envelope=envelope.as_json(),
            installation_external_id=envelope.installation_external_id,
            repository_external_id=repository_external_id,
        )
        if outcome.status == "conflict" and outcome.delivery_row_id is not None:
            await service.mark_delivery_processed(connection, outcome.delivery_row_id, "rejected")
    if outcome.status == "conflict":
        # Same delivery id, different bytes: a security signal, not a retry.
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

    # 10. domain work, idempotent, after the claim is ours.
    async with engine.begin() as connection:
        scope_id = await _organization_scope_id(connection)
        installation_row = await service.upsert_installation(
            connection,
            scope_id=scope_id,
            external_id=envelope.installation_external_id,
            account_login=envelope.account_login,
            state=(
                "suspended"
                if envelope.action == "suspend"
                else "deleted"
                if envelope.action in ("deleted", "removed")
                else "active"
            ),
        )
        touched = 0
        conflicts: list[int] = []
        for summary in envelope.repositories:
            if summary.get("membership") == "removed" or envelope.action in (
                "removed",
                "deleted",
            ):
                await service.mark_access_removed(connection, [summary["external_id"]], "removed")
                continue
            repository_row_id, _created = await service.upsert_repository(
                connection,
                installation_row_id=installation_row,
                scope_id=scope_id,
                external_id=summary["external_id"],
                full_name=summary["full_name"],
                name=summary.get("name", ""),
                owner_login=summary.get("owner_login", ""),
                node_id=summary.get("node_id", ""),
                private=bool(summary.get("private", True)),
            )
            gate = catalog.security_gate_for(summary["full_name"])
            try:
                await service.apply_announced_state(connection, repository_row_id, gate)
            except onboarding.InvalidTransitionError:
                # The state machine refused. A validly signed delivery must
                # not become a 500 — GitHub would retry it forever against
                # an invariant that will not change. Keep the current state,
                # record the conflict, and carry on with the rest.
                conflicts.append(summary["external_id"])
                continue
            touched += 1
        if envelope.action == "suspend":
            await service.set_installation_state(
                connection, envelope.installation_external_id, "suspended"
            )
        if outcome.delivery_row_id is not None:
            await service.mark_delivery_processed(connection, outcome.delivery_row_id, "processed")

    if conflicts:
        await service._audit(
            engine,
            action="github.repository.state_conflict",
            result="denied",
            target_type="github_repository",
            metadata={"event": event, "repositories": len(conflicts)},
        )
    await service._audit(
        engine,
        action=f"github.webhook.{event}",
        result="success",
        target_type="github_installation",
        target_id=str(envelope.installation_external_id),
        metadata={"event": event, "action": envelope.action, "repositories": touched},
    )
    return {
        "status": "processed",
        "delivery_id": delivery_id,
        "repositories": touched,
        "conflicts": len(conflicts),
    }
