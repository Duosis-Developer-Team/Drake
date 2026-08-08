"""Request-scoped auth dependencies: session resolution, CSRF, origin checks.

Standardized semantics:
- no/invalid session → 401
- session backend down → 503 (fail closed, never anonymous)
- CSRF/origin failure on cookie-authenticated mutations → 403 (safe 4xx,
  no provider/internal detail)
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from drake_api.auth.sessions import Session, SessionBackendUnavailableError, SessionStore
from drake_api.rbac.service import Principal
from drake_api.settings import Settings


@dataclass(frozen=True, slots=True)
class AuthContext:
    session_id: str
    session: Session

    @property
    def principal(self) -> Principal:
        return Principal(
            identity_id=uuid.UUID(self.session.identity_id),
            issuer=self.session.issuer,
            groups=tuple(self.session.groups),
            groups_overage=self.session.groups_overage,
        )


def get_session_store(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.session_store
    return store


async def optional_auth(request: Request) -> AuthContext | None:
    settings: Settings = request.app.state.settings
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        return None
    store = get_session_store(request)
    try:
        session = await store.get(session_id)
    except SessionBackendUnavailableError as error:
        raise HTTPException(status_code=503, detail="authentication backend unavailable") from error
    if session is None:
        return None
    return AuthContext(session_id=session_id, session=session)


async def require_auth(
    auth: AuthContext | None = Depends(optional_auth),
) -> AuthContext:
    if auth is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return auth


async def require_csrf(request: Request, auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """CSRF defense for cookie-authenticated mutations.

    Layer 1: the request must echo the per-session CSRF token in a header —
    something a cross-site form/fetch cannot read. Layer 2: when the browser
    sends an Origin header, it must be an allowed web origin or this API
    itself.
    """
    settings: Settings = request.app.state.settings

    header_token = request.headers.get("X-CSRF-Token", "")
    if not header_token or header_token != auth.session.csrf_token:
        raise HTTPException(status_code=403, detail="csrf validation failed")

    origin = request.headers.get("Origin")
    if origin:
        allowed = set(settings.allowed_web_origins)
        if settings.is_production_like:
            # Production compares against the CONFIGURED origin only. Adding
            # `request.base_url` would fold a forged Host or X-Forwarded-Host
            # into the allow-list, which is the whole attack this check
            # exists to stop.
            allowed = {str(settings.resolved_public_origin())}
        else:
            # Local and E2E reach the API on several equivalent loopback
            # spellings; trusting the request there costs nothing.
            allowed.add(str(request.base_url).rstrip("/"))
        if origin.rstrip("/") not in allowed:
            raise HTTPException(status_code=403, detail="origin not allowed")
    return auth
