"""GitHub App authentication: RS256 app JWTs and installation tokens.

Contract, straight from GitHub's documentation:
- the JWT MUST be signed with `RS256`;
- `iat` is set 60 seconds in the past to absorb clock drift;
- `exp` is at most 10 minutes in the future — anything larger is refused
  locally rather than sent for GitHub to reject.

The private key is read from a file reference (the same pattern as the
agent CA key) and never leaves this module. Tokens are opaque strings:
nothing here assumes a fixed length or parses their internal structure
beyond the documented `ghs_` prefix used as a shape sanity check.
"""

import datetime as dt
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from drake_api.settings import Settings

# GitHub's hard ceiling for an app JWT.
MAX_JWT_TTL_SECONDS = 600
# How far in the past `iat` is set, per GitHub's clock-drift guidance.
IAT_BACKDATE_SECONDS = 60
# Installation tokens live ~1 hour; refresh before that with a buffer.
DEFAULT_REFRESH_BUFFER_SECONDS = 300

Clock = Callable[[], dt.datetime]


class GitHubAuthError(RuntimeError):
    """Authentication failure. The message NEVER carries key material."""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True)
class AppJwt:
    """A minted app JWT. Repr is redacted so it cannot leak by accident."""

    token: str = field(repr=False)
    expires_at: dt.datetime
    issuer: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"AppJwt(issuer={self.issuer!r}, expires_at={self.expires_at!r}, token=<redacted>)"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.__repr__()


@dataclass(frozen=True)
class InstallationToken:
    """A short-lived installation access token (opaque, variable length)."""

    token: str = field(repr=False)
    expires_at: dt.datetime
    permissions: dict[str, str]
    repository_selection: str = "selected"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            "InstallationToken("
            f"expires_at={self.expires_at!r}, "
            f"permissions={sorted(self.permissions)!r}, token=<redacted>)"
        )

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.__repr__()


class GitHubAppAuth:
    """Mints app JWTs from the configured private key reference."""

    def __init__(self, settings: Settings, clock: Clock | None = None) -> None:
        self._settings = settings
        self._clock = clock or _utcnow
        self._private_key_pem: str | None = None

    @property
    def issuer(self) -> str:
        """GitHub recommends the client id; the app id remains accepted."""
        issuer = self._settings.github_app_client_id or self._settings.github_app_id
        if not issuer:
            raise GitHubAuthError("github app issuer is not configured")
        return issuer

    def _load_private_key(self) -> str:
        if self._private_key_pem is not None:
            return self._private_key_pem
        reference = self._settings.github_app_private_key_file
        if not reference:
            raise GitHubAuthError("github app private key reference is not configured")
        try:
            material = Path(reference).read_text(encoding="utf-8")
        except OSError as error:
            # Never echo the path contents or the OS error detail verbatim.
            raise GitHubAuthError("github app private key is unreadable") from error
        if "PRIVATE KEY" not in material:
            raise GitHubAuthError("github app private key is not a PEM private key")
        self._private_key_pem = material
        return material

    def mint_app_jwt(self, ttl_seconds: int | None = None) -> AppJwt:
        ttl = ttl_seconds if ttl_seconds is not None else self._settings.github_jwt_ttl_seconds
        if ttl <= 0:
            raise GitHubAuthError("github app JWT lifetime must be positive")
        if ttl > MAX_JWT_TTL_SECONDS:
            # Refuse locally instead of minting a token GitHub will reject.
            raise GitHubAuthError("github app JWT lifetime exceeds the 10-minute ceiling")
        now = self._clock()
        issued_at = now - dt.timedelta(seconds=IAT_BACKDATE_SECONDS)
        expires_at = now + dt.timedelta(seconds=ttl)
        claims: dict[str, Any] = {
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": self.issuer,
        }
        try:
            token = jwt.encode(claims, self._load_private_key(), algorithm="RS256")
        except GitHubAuthError:
            raise
        except Exception as error:
            raise GitHubAuthError("github app JWT could not be signed") from error
        return AppJwt(token=token, expires_at=expires_at, issuer=claims["iss"])


@dataclass(frozen=True)
class TokenScope:
    """What a token is actually authorized for.

    Installation id alone is NOT the identity of an installation token: the
    same installation can mint tokens narrowed to particular repositories
    and particular permissions. Caching on the id alone lets a token minted
    for repository A satisfy a request for repository B, or a metadata-only
    token satisfy a caller that asked for more — quietly widening or
    narrowing authority without anyone asking for it.
    """

    installation_id: int
    repository_ids: tuple[int, ...] = ()
    permissions: tuple[tuple[str, str], ...] = ()

    @staticmethod
    def build(
        installation_id: int,
        repository_ids: "Iterable[int] | None" = None,
        permissions: dict[str, str] | None = None,
    ) -> "TokenScope":
        return TokenScope(
            installation_id=installation_id,
            repository_ids=tuple(sorted(set(repository_ids or ()))),
            permissions=tuple(sorted((str(k), str(v)) for k, v in (permissions or {}).items())),
        )


class InstallationTokenCache:
    """Process-memory cache of installation tokens, keyed by SCOPE.

    Nothing here touches the database: an installation token is a
    credential, and credentials never persist. A token inside the refresh
    buffer is treated as already expired so a slow request can never ride
    one that dies mid-flight.
    """

    def __init__(
        self,
        refresh_buffer_seconds: int = DEFAULT_REFRESH_BUFFER_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        self._buffer = dt.timedelta(seconds=max(0, refresh_buffer_seconds))
        self._clock = clock or _utcnow
        self._entries: dict[TokenScope, InstallationToken] = {}
        self._lock = threading.Lock()

    def get(self, scope: TokenScope) -> InstallationToken | None:
        with self._lock:
            token = self._entries.get(scope)
            if token is None:
                return None
            if token.expires_at - self._buffer <= self._clock():
                del self._entries[scope]
                return None
            return token

    def put(self, scope: TokenScope, token: InstallationToken) -> None:
        with self._lock:
            self._entries[scope] = token

    def invalidate(self, installation_id: int) -> None:
        """Drop every scope belonging to an installation."""
        with self._lock:
            for key in [k for k in self._entries if k.installation_id == installation_id]:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def missing_permissions(granted: dict[str, str], required: dict[str, str]) -> list[str]:
    """Which required read permissions the token did not actually get.

    A token that came back narrower than asked for is not a reason to try
    again with more; it is a reason to report UNKNOWN for whatever needed
    the missing permission.
    """
    missing: list[str] = []
    for name, level in sorted(required.items()):
        actual = granted.get(name)
        if actual is None:
            missing.append(name)
        elif level == "read" and actual not in ("read", "write", "admin"):
            missing.append(name)
    return missing


def looks_like_installation_token(value: str) -> bool:
    """Shape sanity check only — length is deliberately NOT constrained.

    GitHub has changed token formats before; the only stable promise is
    the documented `ghs_` prefix for installation tokens.
    """
    return value.startswith("ghs_") and len(value) > len("ghs_")


def load_webhook_secret(settings: Settings) -> str:
    """Read the webhook secret from its file reference.

    The value stays in memory for the duration of one verification and is
    never logged, stored, or returned through the API.
    """
    reference = settings.github_webhook_secret_file
    if not reference:
        raise GitHubAuthError("github webhook secret reference is not configured")
    try:
        return Path(reference).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise GitHubAuthError("github webhook secret is unreadable") from error


# --- startup validation --------------------------------------------------

# Bounds that keep a misconfiguration from becoming a security problem
# rather than merely a wrong number.
MIN_REFRESH_BUFFER_SECONDS = 30
MAX_REFRESH_BUFFER_SECONDS = 3000
MIN_WEBHOOK_BODY_BYTES = 1024
MAX_WEBHOOK_BODY_BYTES = 26_214_400  # GitHub caps deliveries at 25 MiB


def validate_credentials(settings: Settings) -> None:
    """Prove the configured credentials are usable, at startup.

    Checking that the path *strings* are non-empty proves nothing: the file
    may be absent, unreadable, or not a key at all, and the first webhook
    would be the thing that discovers it. This actually opens the
    references and parses the key, so a broken credential is a refusal to
    start rather than a runtime surprise.

    Every failure names WHAT is wrong and never includes file contents, so
    the message is safe to log.
    """
    if not settings.github_app_enabled:
        # Disabled means disabled: no secret file is opened at all.
        return

    if not (settings.github_app_client_id or settings.github_app_id):
        raise GitHubAuthError("github app identity (client id or app id) is not configured")

    if not 0 < settings.github_jwt_ttl_seconds <= MAX_JWT_TTL_SECONDS:
        raise GitHubAuthError(
            "github app JWT lifetime must be between 1 second and GitHub's 10-minute ceiling"
        )
    if not (
        MIN_REFRESH_BUFFER_SECONDS
        <= settings.github_token_refresh_buffer_seconds
        <= MAX_REFRESH_BUFFER_SECONDS
    ):
        raise GitHubAuthError("github token refresh buffer is outside the safe range")
    if not (
        MIN_WEBHOOK_BODY_BYTES <= settings.github_webhook_max_body_bytes <= MAX_WEBHOOK_BODY_BYTES
    ):
        raise GitHubAuthError("github webhook body limit is outside the safe range")
    if not 1.0 <= settings.github_recovery_poll_seconds <= 3600.0:
        raise GitHubAuthError("github recovery poll interval is outside the safe range")
    if not 1 <= settings.github_recovery_batch_size <= 500:
        raise GitHubAuthError("github recovery batch size is outside the safe range")

    if settings.env not in ("local", "test") and not settings.github_api_base_url.startswith(
        "https://"
    ):
        raise GitHubAuthError("github API base URL must be https outside local/test")

    _validate_private_key(settings)
    _validate_webhook_secret(settings)


def _validate_private_key(settings: Settings) -> None:
    reference = settings.github_app_private_key_file
    if not reference:
        raise GitHubAuthError("github app private key reference is not configured")
    path = Path(reference)
    if not path.is_file():
        raise GitHubAuthError("github app private key reference does not point at a file")
    try:
        material = path.read_bytes()
    except OSError as error:
        raise GitHubAuthError("github app private key is unreadable") from error
    if not material.strip():
        raise GitHubAuthError("github app private key file is empty")

    try:
        key = serialization.load_pem_private_key(material, password=None)
    except Exception:
        # The underlying exception can quote parts of the file, so it is
        # deliberately NOT chained into ours.
        raise GitHubAuthError(
            "github app private key is not a readable unencrypted PEM private key"
        ) from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GitHubAuthError("github app private key must be an RSA key (GitHub signs RS256)")


def _validate_webhook_secret(settings: Settings) -> None:
    reference = settings.github_webhook_secret_file
    if not reference:
        raise GitHubAuthError("github webhook secret reference is not configured")
    path = Path(reference)
    if not path.is_file():
        raise GitHubAuthError("github webhook secret reference does not point at a file")
    try:
        material = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GitHubAuthError("github webhook secret is unreadable") from error
    if not material.strip():
        raise GitHubAuthError("github webhook secret file is empty")
