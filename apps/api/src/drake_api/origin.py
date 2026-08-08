"""The canonical production public origin (ADR-0021).

Drake is served from ONE public origin. The browser talks to `/` for the
web application and `/v1` for the API on that same origin, so cookies and
CSRF work without CORS and without a second hostname to keep in sync.

Everything externally visible — OIDC redirects, the webhook URL an
operator pastes into GitHub, post-login and logout redirects — derives
from this one value. Deriving them from the request instead would let a
forged `Host` or `X-Forwarded-Host` decide where Drake sends a user, or
where it claims its own callback lives.
"""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# Hostname labels per RFC 1123: letters, digits and hyphens, not leading or
# trailing with a hyphen.
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)

# Values that mean "somebody has not filled this in yet". Rendering or
# starting with one of these is a misconfiguration, not a default.
PLACEHOLDERS = frozenset(
    {
        "replace_me",
        "changeme",
        "change-me",
        "example.com",
        "example.org",
        "<public_host>",
        "todo",
    }
)


class InvalidOriginError(ValueError):
    """The configured public origin cannot be used in production."""


@dataclass(frozen=True)
class PublicOrigin:
    """A validated `https://host[:port]` with no path, query or fragment."""

    scheme: str
    host: str
    port: int | None

    def __str__(self) -> str:
        if self.port is None:
            return f"{self.scheme}://{self.host}"
        return f"{self.scheme}://{self.host}:{self.port}"

    def url(self, path: str) -> str:
        """Absolute URL for an application path, on this origin only."""
        return f"{self}{path if path.startswith('/') else '/' + path}"


def _looks_like_placeholder(host: str) -> bool:
    lowered = host.lower()
    return lowered in PLACEHOLDERS or any(token in lowered for token in ("replace_me", "<", ">"))


def parse_public_origin(value: str, *, require_https: bool = True) -> PublicOrigin:
    """Parse and validate a configured public origin.

    `require_https` is False only for local and test environments, where
    the origin is `http://127.0.0.1:3000` and always will be. Production
    never relaxes it.
    """
    if not value or not value.strip():
        raise InvalidOriginError("public origin is not configured")
    raw = value.strip()

    split = urlsplit(raw)
    if split.scheme not in ("http", "https"):
        raise InvalidOriginError("public origin must be an http(s) URL")
    if require_https and split.scheme != "https":
        raise InvalidOriginError("public origin must use https in production")
    if split.path not in ("", "/"):
        raise InvalidOriginError("public origin must not contain a path")
    if split.query or split.fragment:
        raise InvalidOriginError("public origin must not contain a query or fragment")
    if "@" in split.netloc:
        raise InvalidOriginError("public origin must not embed credentials")

    host = split.hostname or ""
    if not host:
        raise InvalidOriginError("public origin has no host")
    if "*" in raw:
        raise InvalidOriginError("public origin must be an exact host, not a wildcard")
    if _looks_like_placeholder(host):
        raise InvalidOriginError("public origin is still a placeholder value")

    if require_https:
        if host in ("localhost", "localhost.localdomain"):
            raise InvalidOriginError("public origin must not be localhost in production")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            if address.is_loopback:
                raise InvalidOriginError("public origin must not be a loopback address")
            raise InvalidOriginError("public origin must be a hostname, not a bare IP address")
        if "." not in host:
            raise InvalidOriginError("public origin must be a fully qualified domain name")

    for label in host.split("."):
        if not _LABEL.match(label):
            raise InvalidOriginError("public origin hostname is malformed")

    try:
        port = split.port
    except ValueError as error:
        raise InvalidOriginError("public origin port is malformed") from error

    return PublicOrigin(scheme=split.scheme, host=host.lower(), port=port)


# The application's externally visible routes. These are the ACTUAL paths
# the code serves; an operator pastes them into GitHub and into the OIDC
# provider, so they are derived here rather than written down twice.
OIDC_CALLBACK_PATH = "/v1/auth/callback"
GITHUB_WEBHOOK_PATH = "/v1/integrations/github/webhook"


def oidc_redirect_url(origin: PublicOrigin) -> str:
    return origin.url(OIDC_CALLBACK_PATH)


def github_webhook_url(origin: PublicOrigin) -> str:
    return origin.url(GITHUB_WEBHOOK_PATH)
