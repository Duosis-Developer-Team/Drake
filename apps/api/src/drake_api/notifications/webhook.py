"""Sending one webhook: the SSRF boundary, the signature, the classification.

The endpoint never comes from a request. A destination row holds an opaque
KEY; this module resolves that key against the operator's settings and
validates the target every single time, because a hostname that was public
when the destination was created can point somewhere else today.

What a receiver gets: a small, server-composed JSON body, a stable
`Idempotency-Key`, and — when a signing secret is configured — a versioned
HMAC over `timestamp.body`. What Drake keeps afterwards: an outcome code,
an HTTP status and a duration. Never the response body, never the URL,
never the secret.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import random
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from drake_api.settings import Settings, WebhookDestination

logger = logging.getLogger("drake_api.notifications.webhook")

SIGNATURE_VERSION = "v1"
SIGNATURE_HEADER = "X-Drake-Signature"
TIMESTAMP_HEADER = "X-Drake-Timestamp"
IDEMPOTENCY_HEADER = "Idempotency-Key"

# HTTP statuses worth trying again. Everything else in 4xx is the receiver
# telling us the request itself is wrong, and repeating it would only be
# louder.
_RETRYABLE_STATUSES = frozenset({408, 429})

# The longest `Retry-After` Drake will honour. A receiver asking for an
# hour does not get to schedule Drake's worker.
MAX_RETRY_AFTER_SECONDS = 300
_BASE_BACKOFF_SECONDS = 30
_MAX_BACKOFF_SECONDS = 3600


class DestinationRefusedError(RuntimeError):
    """The resolved target violates the SSRF boundary (terminal)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AttemptResult:
    """One attempt, reduced to what is safe to store."""

    outcome: str  # delivered | retryable | terminal | refused
    http_status: int | None = None
    error_code: str | None = None
    duration_ms: int = 0
    retry_after_seconds: int | None = None


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def _default_resolver(hostname: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _refuse(condition: bool, code: str) -> None:
    if condition:
        raise DestinationRefusedError(code)


def _check_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    settings: Settings,
    destination: WebhookDestination,
) -> bool:
    """Validate one resolved address; returns whether it is private."""
    # Refused everywhere, in every environment. 169.254.169.254 — the cloud
    # metadata endpoint — is link-local, and it is the single most valuable
    # target an SSRF can reach.
    _refuse(address.is_link_local, "destination_target_refused")
    _refuse(address.is_multicast, "destination_target_refused")
    _refuse(address.is_unspecified, "destination_target_refused")
    _refuse(address.is_reserved, "destination_target_refused")
    local_like = settings.env in ("local", "test")
    _refuse(address.is_loopback and not local_like, "destination_target_refused")
    if address.is_private and not address.is_loopback:
        _refuse(not (local_like or destination.allow_private), "destination_private_refused")
        return True
    return False


async def validate_destination(
    destination: WebhookDestination, settings: Settings, resolver: Resolver | None = None
) -> str:
    """The SSRF boundary, re-checked on every send.

    Validating once at configuration time would not help: DNS answers
    change, and the address a hostname resolved to last week is not a
    promise about today.
    """
    parts = urlsplit(destination.url)
    local_like = settings.env in ("local", "test")
    _refuse(parts.scheme not in ("http", "https"), "destination_scheme_refused")
    _refuse(parts.scheme == "http" and not local_like, "destination_plaintext_refused")
    # Credentials in a URL end up in logs and proxies, and they would also
    # be a way to smuggle a target past a hostname allowlist.
    _refuse(
        parts.username is not None or parts.password is not None,
        "destination_credentials_refused",
    )
    _refuse(bool(parts.fragment), "destination_url_shape_refused")
    _refuse(not parts.hostname, "destination_host_missing")

    hostname = str(parts.hostname)
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        _check_address(literal, settings, destination)
        return destination.url

    try:
        addresses = await (resolver or _default_resolver)(hostname, port)
    except socket.gaierror as error:
        raise DestinationRefusedError("destination_unresolvable") from error
    _refuse(not addresses, "destination_unresolvable")
    verdicts = [
        _check_address(ipaddress.ip_address(address), settings, destination)
        for address in addresses
    ]
    # A name answering with both public and private addresses is the shape
    # of a rebinding attack, not of a healthy endpoint.
    _refuse(any(verdicts) and not all(verdicts), "destination_mixed_answers_refused")
    return destination.url


def load_signing_secret(destination: WebhookDestination) -> bytes | None:
    """Read the signing secret from its file reference, or None.

    Held for the lifetime of one signature and never returned, logged, or
    stored. A missing file is treated as "unsigned" rather than an error
    the receiver would see as an outage.
    """
    if not destination.signing_secret_file:
        return None
    try:
        material = Path(destination.signing_secret_file).read_bytes().strip()
    except OSError:
        logger.warning("webhook signing secret reference could not be read")
        return None
    return material or None


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    """Versioned HMAC over `timestamp.body`.

    The timestamp is inside the signed material on purpose: signing the
    body alone would let a captured request be replayed forever.
    """
    mac = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"{SIGNATURE_VERSION}={mac.hexdigest()}"


def classify_status(status: int) -> tuple[str, str | None]:
    """HTTP status → (outcome, error code)."""
    if 200 <= status < 300:
        return "delivered", None
    if 300 <= status < 400:
        # Never followed: a redirect is how a validated public endpoint
        # sends Drake somewhere it was not allowed to go.
        return "terminal", "destination_redirect_refused"
    if status in _RETRYABLE_STATUSES or status >= 500:
        return "retryable", f"http_{status}"
    return "terminal", f"http_{status}"


def parse_retry_after(value: str | None) -> int | None:
    """Honour `Retry-After` only when it is a small number of seconds."""
    if not value:
        return None
    try:
        seconds = int(value.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def next_backoff_seconds(attempt: int, retry_after: int | None = None) -> int:
    """Exponential with bounded jitter, or the receiver's bounded request.

    Jitter matters when a receiver comes back after an outage: without it
    every queued delivery retries in the same instant and knocks it over
    again.
    """
    if retry_after is not None:
        return retry_after
    base = min(_BASE_BACKOFF_SECONDS * (2 ** max(0, attempt - 1)), _MAX_BACKOFF_SECONDS)
    return int(base + random.uniform(0, base * 0.25))  # noqa: S311 - jitter, not crypto


async def send_webhook(
    destination: WebhookDestination,
    settings: Settings,
    *,
    payload: dict[str, Any],
    idempotency_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> AttemptResult:
    """One attempt. Never raises for a delivery failure; classifies it."""
    started = datetime.now(UTC)

    def elapsed() -> int:
        return int((datetime.now(UTC) - started).total_seconds() * 1000)

    try:
        url = await validate_destination(destination, settings, resolver)
    except DestinationRefusedError as error:
        # A misconfigured or hostile target is not something retrying fixes.
        return AttemptResult("refused", None, error.code, elapsed())

    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    timestamp = str(int(started.timestamp()))
    headers = {
        "Content-Type": "application/json",
        IDEMPOTENCY_HEADER: idempotency_key,
        TIMESTAMP_HEADER: timestamp,
        "User-Agent": "Drake/1.0",
    }
    secret = load_signing_secret(destination)
    if secret is not None:
        headers[SIGNATURE_HEADER] = sign(secret, timestamp, body)

    timeout = httpx.Timeout(
        min(destination.timeout_seconds, 30.0), connect=min(destination.timeout_seconds, 5.0)
    )
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            # Redirects are refused, not followed — see classify_status.
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.TimeoutException:
        return AttemptResult("retryable", None, "timeout", elapsed())
    except httpx.ConnectError:
        return AttemptResult("retryable", None, "connect_failed", elapsed())
    except httpx.HTTPError:
        return AttemptResult("retryable", None, "transport_error", elapsed())

    outcome, error_code = classify_status(response.status_code)
    retry_after = (
        parse_retry_after(response.headers.get("retry-after")) if outcome == "retryable" else None
    )
    # The body is deliberately not read into anything we keep. A receiver's
    # error page can contain anything, including its own secrets.
    return AttemptResult(outcome, response.status_code, error_code, elapsed(), retry_after)
