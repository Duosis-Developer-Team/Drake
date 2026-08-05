"""Server-side session store (Redis-backed, fail-closed).

- The browser holds only an opaque random session ID in an HttpOnly cookie.
- Redis stores the session under ``sha256(session_id)`` so the raw ID exists
  nowhere server-side; a leaked store dump cannot be replayed as cookies.
- Redis is a session/cache backend here, never an authoritative business
  store; losing it logs everyone out — it can never silently authenticate.
- Any Redis failure raises ``SessionBackendUnavailableError`` → authentication
  fails closed with a typed 503, never an anonymous pass-through.
"""

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import redis.asyncio as aioredis

from drake_api.settings import Settings

_SESSION_PREFIX = "drake:session:"


class SessionBackendUnavailableError(RuntimeError):
    """Session backend cannot be reached. Message never contains URLs."""


@dataclass(slots=True)
class Session:
    identity_id: str
    issuer: str
    subject: str
    display_name: str
    email: str
    groups: list[str] = field(default_factory=list)
    groups_overage: bool = False
    csrf_token: str = ""
    created_at: str = ""
    expires_at: str = ""


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def storage_key(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return f"{_SESSION_PREFIX}{digest}"


class SessionStore:
    def __init__(self, settings: Settings, client: aioredis.Redis | None = None) -> None:
        self._settings = settings
        # ``client`` is injectable for tests (e.g. fakeredis).
        self._client: aioredis.Redis = client or aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.ready_check_timeout_seconds,
            socket_timeout=settings.ready_check_timeout_seconds,
        )

    async def create(self, session: Session) -> str:
        """Store a session and return the (never-logged) session ID."""
        session_id = new_session_id()
        session.csrf_token = session.csrf_token or new_csrf_token()
        session.created_at = datetime.now(UTC).isoformat()
        try:
            await self._client.set(
                storage_key(session_id),
                json.dumps(asdict(session)),
                ex=self._settings.session_ttl_seconds,
            )
        except Exception as error:
            raise SessionBackendUnavailableError("session backend unavailable") from error
        return session_id

    async def get(self, session_id: str) -> Session | None:
        try:
            raw = await self._client.get(storage_key(session_id))
        except Exception as error:
            raise SessionBackendUnavailableError("session backend unavailable") from error
        if raw is None:
            return None
        data = json.loads(raw)
        return Session(**data)

    async def delete(self, session_id: str) -> None:
        """Server-side invalidation (logout / revoke)."""
        try:
            await self._client.delete(storage_key(session_id))
        except Exception as error:
            raise SessionBackendUnavailableError("session backend unavailable") from error

    async def put_login_state(self, state: str, payload: dict[str, str]) -> None:
        """Store one-time login state (state -> nonce/verifier/redirect)."""
        try:
            await self._client.set(
                f"drake:auth:state:{state}",
                json.dumps(payload),
                ex=self._settings.login_state_ttl_seconds,
            )
        except Exception as error:
            raise SessionBackendUnavailableError("session backend unavailable") from error

    async def take_login_state(self, state: str) -> dict[str, str] | None:
        """Atomically fetch AND delete login state — replayed state fails."""
        try:
            raw = await self._client.getdel(f"drake:auth:state:{state}")
        except Exception as error:
            raise SessionBackendUnavailableError("session backend unavailable") from error
        if raw is None:
            return None
        result: dict[str, str] = json.loads(raw)
        return result

    async def aclose(self) -> None:
        await self._client.aclose()
