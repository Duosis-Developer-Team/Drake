"""Sending one incident e-mail through Microsoft Graph (ADR-0030).

E-mail rides the webhook outbox. The planner freezes the same small,
server-composed payload for an e-mail destination as for a webhook, and the
delivery worker hands it here instead of to `send_webhook` when the key
belongs to the e-mail registry. Retries, the bounded budget, dead letters,
the attempt audit and the at-least-once contract are therefore exactly the
webhook ones, and nothing about them is re-implemented.

What is sent is composed here from that frozen payload: a subject and a
plain-text body. Plain text on purpose — there is no HTML for a title or a
service name to break out of.

Credentials: the client-credentials flow, with the secret read from its
file reference at token time. A token is cached in memory until shortly
before it expires, so a burst of incidents asks Microsoft for one token, not
one per message. Nothing about the token, the secret or Graph's response
body is stored or logged; an attempt keeps an outcome, a status and a code.

At-least-once means a crash between Graph accepting a message and Drake
recording it sends that message again. Graph has no idempotency key, so the
stable delivery key travels as an `X-Drake-Idempotency-Key` message header,
where a mail rule or a person can see two copies are one event.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from drake_api.notifications.webhook import AttemptResult, parse_retry_after
from drake_api.settings import EmailDestination, GraphMailSender, Settings

logger = logging.getLogger("drake_api.notifications.email")

IDEMPOTENCY_MESSAGE_HEADER = "X-Drake-Idempotency-Key"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
# Refresh this long before Microsoft says the token expires, so a token
# never runs out between being read from the cache and being used.
_TOKEN_EARLY_SECONDS = 120

_EVENT_SUBJECT: dict[str, str] = {
    "opened": "Incident opened",
    "acknowledged": "Incident acknowledged",
    "auto_resolved": "Incident resolved",
}

_EVENT_LINE: dict[str, str] = {
    "opened": "Drake opened an incident after consecutive trustworthy critical evaluations.",
    "acknowledged": "A responder acknowledged this incident. Monitoring continues.",
    "auto_resolved": (
        "The service reported healthy again, so Drake resolved this incident automatically."
    ),
}


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    body: str


def _one_line(value: Any, limit: int) -> str:
    """A header-safe single line: no CR/LF, bounded length."""
    text = " ".join(str(value or "").split())
    return text[:limit]


def compose_message(payload: dict[str, Any], *, public_origin: str = "") -> EmailMessage:
    """Subject and plain-text body from the frozen delivery payload.

    Only fields the planner put there. A relative incident link is made
    absolute with the canonical public origin when one is configured, so the
    reader can click it from a mail client.
    """
    incident = payload.get("incident") or {}
    service = payload.get("service") or {}
    event_type = str(payload.get("event_type") or "")
    headline = _EVENT_SUBJECT.get(event_type, "Incident update")
    title = _one_line(incident.get("title"), 160)
    severity = _one_line(incident.get("severity"), 20)
    where = "/".join(
        part
        for part in (
            _one_line(service.get("project_key"), 64),
            _one_line(service.get("environment_key"), 64),
            _one_line(service.get("service_key"), 64),
        )
        if part
    )
    workload = "/".join(
        part
        for part in (
            _one_line(service.get("cluster_ref"), 64),
            _one_line(service.get("namespace"), 64),
            _one_line(service.get("workload_name"), 64),
        )
        if part
    )
    link = _one_line(incident.get("url"), 500)
    if link.startswith("/") and public_origin:
        link = f"{public_origin.rstrip('/')}{link}"

    prefix = f"[Drake][{severity.upper()}]" if severity else "[Drake]"
    subject = _one_line(f"{prefix} {headline}: {title}" if title else f"{prefix} {headline}", 230)

    lines = [
        f"{headline}: {title}" if title else headline,
        "",
        _EVENT_LINE.get(event_type, "This incident changed state."),
        "",
    ]
    if severity:
        lines.append(f"Severity:  {severity}")
    if where:
        lines.append(f"Service:   {where}")
    if workload:
        lines.append(f"Workload:  {workload}")
    occurred = _one_line(payload.get("occurred_at"), 40)
    if occurred:
        lines.append(f"When:      {occurred}")
    if link:
        lines += ["", f"Open in Drake: {link}"]
    lines += ["", "-- ", "Sent by Drake. Routing is managed under Notification policies."]
    return EmailMessage(subject=subject, body="\n".join(lines)[:8000])


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------


class _TokenError(Exception):
    def __init__(self, result: AttemptResult) -> None:
        super().__init__(result.error_code or "")
        self.result = result


_token_cache: dict[tuple[str, str, str], tuple[str, float]] = {}
_token_lock = asyncio.Lock()


def clear_token_cache() -> None:
    """Tests, and a 401 from Graph: the next send fetches a fresh token."""
    _token_cache.clear()


def _cache_key(sender: GraphMailSender) -> tuple[str, str, str]:
    return (sender.login_base_url.rstrip("/"), sender.tenant_id, sender.client_id)


def _read_secret(sender: GraphMailSender) -> str | None:
    try:
        secret = Path(sender.client_secret_file).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return secret or None


async def _access_token(client: httpx.AsyncClient, sender: GraphMailSender, started: float) -> str:
    key = _cache_key(sender)
    async with _token_lock:
        cached = _token_cache.get(key)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        secret = _read_secret(sender)
        if secret is None:
            # Retryable: the usual cause is a secret that is being rotated
            # or not mounted yet, and fixing it should not cost the message.
            logger.warning("e-mail client secret reference could not be read")
            raise _TokenError(
                AttemptResult("retryable", None, "email_secret_unavailable", elapsed())
            )
        url = (
            f"{sender.login_base_url.rstrip('/')}/"
            f"{quote(sender.tenant_id, safe='')}/oauth2/v2.0/token"
        )
        try:
            response = await client.post(
                url,
                data={
                    "client_id": sender.client_id,
                    "client_secret": secret,
                    "scope": GRAPH_SCOPE,
                    "grant_type": "client_credentials",
                },
            )
        except httpx.TimeoutException as error:
            raise _TokenError(
                AttemptResult("retryable", None, "email_auth_timeout", elapsed())
            ) from error
        except httpx.HTTPError as error:
            raise _TokenError(
                AttemptResult("retryable", None, "email_auth_unavailable", elapsed())
            ) from error
        finally:
            del secret

        status = response.status_code
        if status == 429 or status >= 500:
            raise _TokenError(
                AttemptResult(
                    "retryable",
                    status,
                    "email_auth_unavailable",
                    elapsed(),
                    parse_retry_after(response.headers.get("retry-after")),
                )
            )
        if status != 200:
            # A wrong tenant, client or secret. Retrying the same values
            # cannot succeed; the operator has to fix the configuration.
            raise _TokenError(AttemptResult("terminal", status, "email_auth_refused", elapsed()))
        try:
            body = response.json()
            token = str(body["access_token"])
            lifetime = int(body.get("expires_in", 0))
        except (ValueError, KeyError, TypeError) as error:
            raise _TokenError(
                AttemptResult("retryable", status, "email_auth_malformed", elapsed())
            ) from error
        if not token:
            raise _TokenError(AttemptResult("retryable", status, "email_auth_malformed", elapsed()))
        if lifetime > _TOKEN_EARLY_SECONDS:
            _token_cache[key] = (token, time.monotonic() + lifetime - _TOKEN_EARLY_SECONDS)
        return token


# ---------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------


def _classify_send(status: int) -> tuple[str, str | None]:
    if status == 202 or 200 <= status < 300:
        return "delivered", None
    if 300 <= status < 400:
        return "terminal", "destination_redirect_refused"
    if status in (401, 408, 429) or status >= 500:
        # 401 is retryable because the cached token is dropped below: one
        # that expired early, or was revoked, is replaced on the next try.
        return "retryable", f"http_{status}"
    # 403: the app lacks Mail.Send or may not send as this mailbox.
    # 400/404: the request or the mailbox is wrong. None of these change by
    # asking again.
    return "terminal", f"http_{status}"


async def send_email(
    destination: EmailDestination,
    sender: GraphMailSender,
    settings: Settings,
    *,
    payload: dict[str, Any],
    idempotency_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AttemptResult:
    """One attempt. Never raises for a delivery failure; classifies it."""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    message = compose_message(payload, public_origin=settings.public_origin)
    timeout = httpx.Timeout(
        min(sender.timeout_seconds, 30.0), connect=min(sender.timeout_seconds, 5.0)
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        transport=transport,
        headers={"User-Agent": "Drake/1.0"},
    ) as client:
        try:
            token = await _access_token(client, sender, started)
        except _TokenError as error:
            return error.result

        url = (
            f"{sender.graph_base_url.rstrip('/')}/v1.0/users/"
            f"{quote(sender.sender, safe='@.')}/sendMail"
        )
        body = {
            "message": {
                "subject": message.subject,
                "body": {"contentType": "Text", "content": message.body},
                "toRecipients": [
                    {"emailAddress": {"address": address}} for address in destination.recipients
                ],
                "internetMessageHeaders": [
                    {"name": IDEMPOTENCY_MESSAGE_HEADER, "value": idempotency_key}
                ],
            },
            # Drake's audit is the attempt table. A Sent Items copy per
            # incident would only fill the mailbox.
            "saveToSentItems": False,
        }
        try:
            response = await client.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.TimeoutException:
            return AttemptResult("retryable", None, "timeout", elapsed())
        except httpx.ConnectError:
            return AttemptResult("retryable", None, "connect_failed", elapsed())
        except httpx.HTTPError:
            return AttemptResult("retryable", None, "transport_error", elapsed())

    outcome, error_code = _classify_send(response.status_code)
    if response.status_code == 401:
        clear_token_cache()
    retry_after = (
        parse_retry_after(response.headers.get("retry-after")) if outcome == "retryable" else None
    )
    return AttemptResult(outcome, response.status_code, error_code, elapsed(), retry_after)


__all__ = [
    "IDEMPOTENCY_MESSAGE_HEADER",
    "EmailMessage",
    "clear_token_cache",
    "compose_message",
    "send_email",
]
