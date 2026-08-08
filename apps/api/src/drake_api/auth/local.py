"""Local email/password authentication.

Drake's default way in is an identity provider. This is the other one: an
email and a password that Drake verifies itself, for deployments where no
IdP is wired up yet.

It is deliberately not a second user system. A local credential is a row
attached to an existing `identities` row, so roles, grants, scopes and
audit behave exactly as they do for a federated user, and `/v1/me` cannot
tell the difference.

Two properties are worth stating because they are easy to lose:

- The failure path is identical whether or not the account exists. An
  unknown email still costs a full Argon2 verification against a dummy
  hash, so response timing does not answer "is this address registered?".
- Redis is required. If it is unavailable the login is refused rather than
  allowed, because the rate limiter and the session store both live there
  and neither has a safe degraded mode.
"""

import hmac
import unicodedata
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.profiles import RFC_9106_LOW_MEMORY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.auth.sessions import SessionBackendUnavailableError
from drake_api.settings import Settings

# RFC 9106's low-memory profile: Argon2id, and sized so a login stays
# responsive on an API pod with a modest memory limit rather than being
# tuned for a machine that only hashes passwords.
_hasher = PasswordHasher.from_parameters(RFC_9106_LOW_MEMORY)

# Verified against every failed lookup so that a missing account costs the
# same as a wrong password. The value is a hash of a random string; nothing
# can match it.
_DUMMY_HASH = _hasher.hash("drake-nonexistent-account-placeholder")


class LocalAuthUnavailableError(RuntimeError):
    """The backend a safe decision depends on is not reachable."""


class RateLimitedError(RuntimeError):
    """Too many recent attempts for this address from this client."""


@dataclass(frozen=True)
class LocalIdentity:
    identity_id: str
    issuer: str
    subject: str
    display_name: str
    email: str


LOCAL_ISSUER = "local"


def normalize_email(raw: str) -> str:
    """Lower-case, trim, and normalize unicode.

    NFKC first, so two visually identical addresses cannot become two
    accounts. This is normalization, not validation — the schema and the
    caller check shape.
    """
    return unicodedata.normalize("NFKC", raw).strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-ish comparison delegated to argon2-cffi.

    Returns False rather than raising for a wrong password, and also for a
    stored value that is not a usable hash — an unreadable credential must
    fail closed, never succeed.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


class LocalAuthenticator:
    """Verifies credentials and throttles attempts."""

    def __init__(self, settings: Settings, redis: aioredis.Redis) -> None:
        self._settings = settings
        self._redis = redis

    def _throttle_key(self, client_key: str, email: str) -> str:
        # Both parts matter: per-address alone lets one client grind every
        # account, per-client alone lets a botnet grind one account.
        return f"drake:login:attempts:{client_key}:{email}"

    async def check_rate_limit(self, client_key: str, email: str) -> None:
        key = self._throttle_key(client_key, email)
        try:
            attempts = await self._redis.incr(key)
            if attempts == 1:
                await self._redis.expire(key, self._settings.local_login_window_seconds)
        except Exception as error:
            raise LocalAuthUnavailableError("login backend unavailable") from error
        if attempts > self._settings.local_login_max_attempts:
            raise RateLimitedError("too many login attempts")

    async def clear_rate_limit(self, client_key: str, email: str) -> None:
        try:
            await self._redis.delete(self._throttle_key(client_key, email))
        except Exception:
            return

    async def authenticate(
        self, engine: AsyncEngine, email: str, password: str
    ) -> LocalIdentity | None:
        """Return the identity when the credential matches, else None.

        Both branches perform one Argon2 verification, so the work done for
        an unknown address matches the work done for a wrong password.
        """
        normalized = normalize_email(email)
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT c.password_hash, i.id, i.issuer, i.subject,
                               i.display_name, i.email, i.status
                        FROM local_credentials c
                        JOIN identities i ON i.id = c.identity_id
                        WHERE c.email_normalized = :email
                        """
                    ),
                    {"email": normalized},
                )
            ).first()

        if row is None:
            verify_password(_DUMMY_HASH, password)
            return None

        password_hash, identity_id, issuer, subject, display_name, stored_email, status = row
        if not verify_password(password_hash, password):
            return None
        # A disabled identity keeps its credential row but cannot sign in.
        if status != "active":
            return None

        return LocalIdentity(
            identity_id=str(identity_id),
            issuer=issuer,
            subject=subject,
            display_name=display_name or stored_email,
            email=stored_email,
        )

    async def record_login(self, engine: AsyncEngine, identity_id: str) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE local_credentials SET last_login_at = now() WHERE identity_id = :id"),
                {"id": identity_id},
            )
            await connection.execute(
                text("UPDATE identities SET last_login_at = now() WHERE id = :id"),
                {"id": identity_id},
            )


def client_key(client_host: str | None) -> str:
    """A stable, non-identifying key for the caller.

    The raw address is not stored: the throttle only needs to tell clients
    apart, not name them.
    """
    host = client_host or "unknown"
    digest = hmac.new(b"drake-login-throttle", host.encode(), "sha256").hexdigest()
    return digest[:16]


def is_backend_unavailable(error: Exception) -> bool:
    return isinstance(error, LocalAuthUnavailableError | SessionBackendUnavailableError)


def audit_metadata(email: str, outcome: str) -> dict[str, Any]:
    """Audit fields for a login attempt.

    The address is recorded because an operator investigating a lockout
    needs it. The password never appears, and neither does the hash.
    """
    return {"email": normalize_email(email), "outcome": outcome, "method": "local"}
