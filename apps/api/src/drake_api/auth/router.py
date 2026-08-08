"""Authentication endpoints: login, callback, logout, me."""

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.auth.flows import AuthFlows, sanitize_post_login_redirect
from drake_api.auth.local import (
    LocalAuthenticator,
    LocalAuthUnavailableError,
    RateLimitedError,
    audit_metadata,
    client_key,
    normalize_email,
)
from drake_api.auth.oidc import OidcError, ValidatedIdentity
from drake_api.auth.sessions import Session, SessionBackendUnavailableError, new_csrf_token
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.rbac.service import RbacService
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.auth")

router = APIRouter(prefix="/v1", tags=["auth"])

# OIDC failure codes that indicate a provider outage rather than a bad request.
_UNAVAILABLE_CODES = {"provider_unavailable"}


def _client_ip_hash(request: Request) -> str:
    host = request.client.host if request.client else ""
    return hashlib.sha256(host.encode()).hexdigest()[:16] if host else ""


def _set_session_cookie(response: Response, settings: Settings, session_id: str) -> None:
    response.set_cookie(
        key=settings.effective_session_cookie_name,
        value=session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.env not in ("local", "test"),
        path="/",
    )


class LocalLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/auth/login")
async def local_login(
    request: Request, response: Response, payload: LocalLoginRequest
) -> JSONResponse:
    """Email and password sign-in.

    Every failure returns the same body and status. Distinguishing "no such
    account" from "wrong password" would turn this endpoint into a way to
    enumerate who has access.
    """
    settings: Settings = request.app.state.settings
    if not settings.local_auth_enabled:
        # Not 401: the endpoint genuinely is not part of this deployment.
        raise HTTPException(status_code=404, detail="not found")

    engine = get_engine(settings)
    store = request.app.state.session_store
    authenticator = LocalAuthenticator(settings, request.app.state.telemetry_redis)
    correlation_id = correlation_id_var.get()
    ip_hash = _client_ip_hash(request)
    caller = client_key(request.client.host if request.client else None)
    normalized = normalize_email(payload.email)

    async def _audit(result: str, actor_id: str, outcome: str) -> None:
        try:
            await record_audit_event(
                engine,
                AuditEventData(
                    actor_type="user",
                    actor_id=actor_id,
                    action="auth.login",
                    result=result,
                    correlation_id=correlation_id,
                    metadata={"ip_hash": ip_hash, **audit_metadata(normalized, outcome)},
                ),
            )
        except Exception:
            logger.warning("login audit did not persist")

    try:
        await authenticator.check_rate_limit(caller, normalized)
    except RateLimitedError:
        await _audit("failure", "unknown", "rate_limited")
        raise HTTPException(status_code=429, detail="too many attempts") from None
    except LocalAuthUnavailableError as error:
        raise HTTPException(status_code=503, detail="authentication backend unavailable") from error

    identity = await authenticator.authenticate(engine, payload.email, payload.password)
    if identity is None:
        await _audit("failure", "unknown", "invalid_credentials")
        raise HTTPException(status_code=401, detail="invalid email or password")

    # A brand new session id for every successful login: the caller can
    # never keep a session identifier it chose or previously held.
    session = Session(
        identity_id=identity.identity_id,
        issuer=identity.issuer,
        subject=identity.subject,
        display_name=identity.display_name,
        email=identity.email,
        groups=[],
        csrf_token=new_csrf_token(),
    )
    try:
        session_id = await store.create(session)
    except SessionBackendUnavailableError as error:
        raise HTTPException(status_code=503, detail="authentication backend unavailable") from error

    await authenticator.clear_rate_limit(caller, normalized)
    await authenticator.record_login(engine, identity.identity_id)
    await _audit("success", identity.identity_id, "authenticated")

    body = JSONResponse({"status": "signed_in", "csrf_token": session.csrf_token})
    _set_session_cookie(body, settings, session_id)
    return body


@router.get("/auth/login")
async def login(
    request: Request, redirect: str | None = None, login_hint: str | None = None
) -> RedirectResponse:
    flows: AuthFlows = request.app.state.auth_flows
    try:
        target = await flows.begin_login(redirect, login_hint=login_hint)
    except SessionBackendUnavailableError as error:
        raise HTTPException(status_code=503, detail="authentication backend unavailable") from error
    except OidcError as error:
        raise HTTPException(status_code=503, detail="identity provider unavailable") from error
    return RedirectResponse(url=target.url, status_code=302)


@router.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    settings: Settings = request.app.state.settings
    flows: AuthFlows = request.app.state.auth_flows
    engine = get_engine(settings)
    correlation_id = correlation_id_var.get()
    ip_hash = _client_ip_hash(request)

    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")

    async def resolve_identity(identity: ValidatedIdentity) -> str:
        async with engine.begin() as connection:
            service = RbacService(connection)
            identity_id = await service.upsert_identity_on_login(
                identity.issuer, identity.subject, identity.display_name, identity.email
            )
            await service.record_audit(
                AuditEventData(
                    actor_type="user",
                    actor_id=str(identity_id),
                    action="auth.login",
                    result="success",
                    correlation_id=correlation_id,
                    metadata={
                        "ip_hash": ip_hash,
                        "groups_overage": identity.groups_overage,
                    },
                )
            )
            return str(identity_id)

    try:
        session_id, post_login_redirect, _session = await flows.complete_login(
            code, state, resolve_identity
        )
    except SessionBackendUnavailableError as error:
        raise HTTPException(status_code=503, detail="authentication backend unavailable") from error
    except OidcError as error:
        # Failed login attempts are audited (best effort — the failure itself
        # must still surface even if audit storage is down).
        try:
            await record_audit_event(
                engine,
                AuditEventData(
                    actor_type="system",
                    actor_id="auth",
                    action="auth.login",
                    result="failure",
                    correlation_id=correlation_id,
                    metadata={"code": error.code, "ip_hash": ip_hash},
                ),
            )
        except Exception:
            logger.warning("failed-login audit write did not persist")
        if error.code in _UNAVAILABLE_CODES:
            raise HTTPException(status_code=503, detail="identity provider unavailable") from error
        raise HTTPException(status_code=403, detail=f"login rejected: {error.code}") from error

    response = RedirectResponse(
        url=sanitize_post_login_redirect(post_login_redirect), status_code=302
    )
    _set_session_cookie(response, settings, session_id)
    return response


@router.post("/auth/logout")
async def logout(request: Request, auth: AuthContext = Depends(require_csrf)) -> JSONResponse:
    settings: Settings = request.app.state.settings
    store = request.app.state.session_store
    try:
        await store.delete(auth.session_id)
    except SessionBackendUnavailableError as error:
        raise HTTPException(status_code=503, detail="authentication backend unavailable") from error

    engine = get_engine(settings)
    try:
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action="auth.logout",
                result="success",
                correlation_id=correlation_id_var.get(),
            ),
        )
    except Exception:
        logger.warning("logout audit write did not persist")

    response = JSONResponse({"status": "signed_out"})
    response.delete_cookie(settings.effective_session_cookie_name, path="/")
    return response


@router.get("/me")
async def me(request: Request, auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        service = RbacService(connection)
        grants = await service.effective_grants(auth.principal)

    scopes: dict[str, dict[str, Any]] = {}
    for grant in grants:
        key = str(grant.scope_id)
        entry = scopes.setdefault(
            key,
            {"scope_type": grant.scope_type, "scope_ref": grant.scope_ref, "permissions": []},
        )
        if grant.permission not in entry["permissions"]:
            entry["permissions"].append(grant.permission)
    for entry in scopes.values():
        entry["permissions"].sort()

    return {
        "identity": {
            "display_name": auth.session.display_name,
            "email": auth.session.email,
            "issuer": auth.session.issuer,
        },
        "groups_overage": auth.session.groups_overage,
        "permissions": sorted({grant.permission for grant in grants}),
        "scopes": scopes,
        "csrf_token": auth.session.csrf_token,
    }
