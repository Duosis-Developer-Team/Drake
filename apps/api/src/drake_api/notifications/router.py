"""Notification endpoints: inbox, policies, destinations, delivery audit.

Two things are deliberately absent. There is no endpoint that sends a test
notification, replays an event, or retries a delivery on demand — each of
those is a way for an authenticated user to make Drake call an external
endpoint on cue. And there is no field anywhere that accepts a URL, a
header, a token or a message body: destinations are chosen from the
operator's registry by key, and every word a recipient reads is composed
by the server.
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
from drake_api.notifications import repository as repo
from drake_api.notifications.model import NOTIFIABLE_EVENTS, SEVERITIES
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.notifications")

router = APIRouter(prefix="/v1", tags=["notifications"])

_NOT_FOUND = "not found"

# Repository error code → HTTP status. Everything a caller may not see maps
# to 404, so no message distinguishes "not yours" from "not there".
_STATUS_BY_CODE: dict[str, int] = {
    "not_found": 404,
    "environment_out_of_scope": 404,
    "service_out_of_scope": 404,
    "destination_out_of_scope": 404,
    "version_conflict": 409,
    "duplicate_destination": 409,
}


def _fail(error: repo.NotificationError) -> HTTPException:
    status = _STATUS_BY_CODE.get(error.code, 422)
    return HTTPException(status_code=status, detail=_NOT_FOUND if status == 404 else str(error))


class MarkReadRequest(BaseModel):
    """Ids only. Marking read is not a place to write anything."""

    model_config = ConfigDict(extra="forbid")
    notification_ids: list[uuid.UUID] = Field(min_length=1, max_length=repo.MAX_MARK_READ)


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=120)
    project_id: uuid.UUID
    environment_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    event_types: list[str] = Field(min_length=1, max_length=len(NOTIFIABLE_EVENTS))
    severities: list[str] = Field(default_factory=lambda: list(SEVERITIES), max_length=4)


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=120)
    environment_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None
    event_types: list[str] = Field(min_length=1, max_length=len(NOTIFIABLE_EVENTS))
    severities: list[str] = Field(default_factory=lambda: list(SEVERITIES), max_length=4)
    enabled: bool = True
    expected_version: int = Field(ge=1)


class DestinationCreate(BaseModel):
    """A user id or a registry key — never an address.

    `extra=forbid` is what makes that structural: there is no `url`,
    `headers` or `token` field for a caller to populate.
    """

    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID
    destination_type: str = Field(pattern="^(in_app_user|webhook)$")
    display_name: str = Field(min_length=1, max_length=120)
    identity_id: uuid.UUID | None = None
    destination_key: str | None = Field(default=None, max_length=64)


class AttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination_id: uuid.UUID


# ---------------------------------------------------------------------------
# inbox — every authenticated user, own rows only
# ---------------------------------------------------------------------------


@router.get("/notifications")
async def inbox(
    request: Request,
    unread_only: bool = Query(default=False),
    window: str | None = Query(default=None, max_length=8),
    limit: int = Query(default=repo.DEFAULT_PAGE_SIZE, ge=1, le=repo.MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The caller's own inbox.

    There is no recipient parameter. The identity comes from the session,
    so there is nothing to change in order to read someone else's mail.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        try:
            return await repo.list_inbox(
                connection,
                auth.principal,
                unread_only=unread_only,
                window=window,
                limit=limit,
                cursor=cursor,
            )
        except repo.NotificationError as error:
            raise _fail(error) from error


@router.get("/notifications/unread-count")
async def unread_count(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, int]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        return {"unread": await repo.unread_count(connection, auth.principal)}


@router.post("/notifications/read")
async def mark_read(
    request: Request, payload: MarkReadRequest, auth: AuthContext = Depends(require_csrf)
) -> dict[str, int]:
    """Mark the caller's own notifications read.

    Idempotent: repeating the call marks nothing further and reports zero.
    It writes no audit event and touches no incident — reading your mail is
    not part of an incident's history.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        try:
            updated = await repo.mark_read(connection, auth.principal, payload.notification_ids)
        except repo.NotificationError as error:
            # An id that is not the caller's, or whose incident has left
            # their scope, is a 404 — the same answer an unknown id gives,
            # so the response cannot confirm that either exists.
            raise _fail(error) from error
    return {"marked_read": updated}


# ---------------------------------------------------------------------------
# policies and destinations
# ---------------------------------------------------------------------------


@router.get("/notification-policies")
async def list_policies(
    request: Request,
    project_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        policies = await repo.list_policies(connection, auth.principal, project_id)
    return {"policies": policies}


@router.get("/notification-policies/options")
async def policy_options(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """The vocabulary a policy form may offer.

    Webhook targets appear as key + display name. The URL behind a key is
    not returned here or anywhere else.
    """
    settings: Settings = request.app.state.settings
    return {
        "event_types": list(NOTIFIABLE_EVENTS),
        "severities": list(SEVERITIES),
        "destination_types": ["in_app_user", "webhook"],
        "webhook_keys": repo.available_webhook_keys(settings),
    }


@router.get("/notification-policies/{policy_id}")
async def get_policy(
    request: Request, policy_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        policy = await repo.get_policy(connection, auth.principal, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return policy


@router.post("/notification-policies", status_code=201)
async def create_policy(
    request: Request, payload: PolicyCreate, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        try:
            created = await repo.create_policy(
                connection,
                auth.principal,
                display_name=payload.display_name,
                project_id=payload.project_id,
                environment_id=payload.environment_id,
                service_id=payload.service_id,
                event_types=payload.event_types,
                severities=payload.severities,
                actor_identity_id=uuid.UUID(auth.session.identity_id),
            )
        except repo.NotificationError as error:
            raise _fail(error) from error

    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="notification.policy.create",
            result="success",
            target_type="notification_policy",
            target_id=created["id"],
            correlation_id=correlation_id_var.get(),
            metadata={"event_types": sorted(set(payload.event_types))},
        ),
    )
    return created


@router.post("/notification-policies/{policy_id}")
async def update_policy(
    request: Request,
    policy_id: uuid.UUID,
    payload: PolicyUpdate,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Update a policy. Only future events are affected by the change."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        try:
            updated = await repo.update_policy(
                connection,
                auth.principal,
                policy_id,
                display_name=payload.display_name,
                environment_id=payload.environment_id,
                service_id=payload.service_id,
                event_types=payload.event_types,
                severities=payload.severities,
                enabled=payload.enabled,
                expected_version=payload.expected_version,
                actor_identity_id=uuid.UUID(auth.session.identity_id),
            )
        except repo.NotificationError as error:
            raise _fail(error) from error

    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="notification.policy.update",
            result="success",
            target_type="notification_policy",
            target_id=str(policy_id),
            correlation_id=correlation_id_var.get(),
            metadata={"enabled": payload.enabled},
        ),
    )
    return updated


@router.post("/notification-policies/{policy_id}/destinations")
async def attach_destination(
    request: Request,
    policy_id: uuid.UUID,
    payload: AttachRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        try:
            result = await repo.attach_destination(
                connection, auth.principal, policy_id, payload.destination_id
            )
        except repo.NotificationError as error:
            raise _fail(error) from error
    return result


@router.get("/notification-destinations")
async def list_destinations(
    request: Request,
    project_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        destinations = await repo.list_destinations(connection, auth.principal, project_id)
    return {"destinations": destinations}


@router.post("/notification-destinations", status_code=201)
async def create_destination(
    request: Request, payload: DestinationCreate, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        try:
            created = await repo.create_destination(
                connection,
                auth.principal,
                project_id=payload.project_id,
                destination_type=payload.destination_type,
                display_name=payload.display_name,
                identity_id=payload.identity_id,
                destination_key=payload.destination_key,
                settings=settings,
                actor_identity_id=uuid.UUID(auth.session.identity_id),
            )
        except repo.NotificationError as error:
            raise _fail(error) from error

    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="notification.destination.create",
            result="success",
            target_type="notification_destination",
            target_id=created["id"],
            correlation_id=correlation_id_var.get(),
            # The key, not the target. The audit trail is not a place to
            # start recording endpoints either.
            metadata={"destination_type": payload.destination_type},
        ),
    )
    return created


# ---------------------------------------------------------------------------
# delivery audit
# ---------------------------------------------------------------------------


@router.get("/notification-deliveries")
async def list_deliveries(
    request: Request,
    project_id: uuid.UUID | None = None,
    state: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=repo.DEFAULT_PAGE_SIZE, ge=1, le=repo.MAX_PAGE_SIZE),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        return await repo.list_deliveries(
            connection, auth.principal, project_id=project_id, state=state, limit=limit
        )


@router.get("/notification-deliveries/{delivery_id}/attempts")
async def delivery_attempts(
    request: Request, delivery_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """The attempt timeline: outcomes, statuses and durations.

    No response body, no target, no exception — an audit trail that
    recorded those would be a place a receiver's secrets come to rest.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        attempts = await repo.list_delivery_attempts(connection, auth.principal, delivery_id)
    if attempts is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"attempts": attempts}
