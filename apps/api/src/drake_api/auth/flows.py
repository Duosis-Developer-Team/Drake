"""Login/callback/logout orchestration.

- ``state`` is single-use (atomic GETDEL) — replay fails.
- PKCE S256; the verifier lives only in the server-side login state.
- Post-login redirect targets come from a strict same-site allowlist
  (relative paths only) — no open redirects.
- A successful callback always mints a brand-new session ID — any session
  cookie sent before login is ignored, preventing session fixation.
"""

import base64
import hashlib
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from drake_api.auth.oidc import OidcClient, OidcError, ValidatedIdentity
from drake_api.auth.sessions import Session, SessionStore
from drake_api.settings import Settings

IdentityResolver = Callable[[ValidatedIdentity], Awaitable[str]]

# Relative paths only; "//host" and "/\host" style tricks are rejected.
_SAFE_REDIRECT = re.compile(r"^/(?![/\\])[A-Za-z0-9/_\-.?=&%#]*$")


def sanitize_post_login_redirect(target: str | None) -> str:
    if target and _SAFE_REDIRECT.fullmatch(target):
        return target
    return "/"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@dataclass(slots=True)
class LoginRedirect:
    url: str


class AuthFlows:
    def __init__(self, settings: Settings, oidc: OidcClient, sessions: SessionStore) -> None:
        self._settings = settings
        self._oidc = oidc
        self._sessions = sessions

    async def begin_login(
        self, post_login_redirect: str | None, login_hint: str | None = None
    ) -> LoginRedirect:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier, challenge = _pkce_pair()
        await self._sessions.put_login_state(
            state,
            {
                "nonce": nonce,
                "verifier": verifier,
                "redirect": sanitize_post_login_redirect(post_login_redirect),
            },
        )
        authorize = await self._oidc.authorization_url()
        params = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": self._settings.oidc_redirect_url,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        # Standard OIDC login_hint pass-through (used by tests/E2E to pick a
        # fake-provider user; harmless for real providers).
        if login_hint:
            params["login_hint"] = login_hint
        query = urlencode(params)
        return LoginRedirect(url=f"{authorize}?{query}")

    async def complete_login(
        self, code: str, state: str, identity_id_resolver: "IdentityResolver"
    ) -> tuple[str, str, Session]:
        """Validate the callback and mint a fresh session.

        Returns ``(session_id, post_login_redirect, session)``. Raises
        ``OidcError`` (typed) on any validation failure — including replayed
        or unknown ``state``.
        """
        login_state = await self._sessions.take_login_state(state)
        if login_state is None:
            raise OidcError("invalid_state")

        tokens = await self._oidc.exchange_code(code, login_state["verifier"])
        identity = await self._oidc.validate_id_token(tokens["id_token"], login_state["nonce"])

        identity_id = await identity_id_resolver(identity)

        session = Session(
            identity_id=identity_id,
            issuer=identity.issuer,
            subject=identity.subject,
            display_name=identity.display_name,
            email=identity.email,
            groups=identity.groups,
            groups_overage=identity.groups_overage,
        )
        # New ID on every login: pre-login cookies can never become
        # authenticated sessions (fixation defense).
        session_id = await self._sessions.create(session)
        return session_id, login_state["redirect"], session
