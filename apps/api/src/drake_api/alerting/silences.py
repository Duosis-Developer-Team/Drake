"""Creating and expiring Alertmanager silences, safely.

A silence is the one outbound provider MUTATION in Drake, so it is built
the way the webhook sender was: the endpoint never comes from a request,
the target is validated on every attempt, and the request is sent to the
validated address rather than to a name that could resolve differently a
millisecond later.

What the caller supplies: an alert or incident, a bounded duration, and a
reason from a reviewed vocabulary. What the caller does NOT supply:

- a matcher, ever. Matchers are composed here from values Drake already
  resolved. A user-written matcher is how "silence this alert" becomes
  "silence this environment".
- a regex. `isRegex` is false on every matcher, enforced in the database
  as well as here.
- a URL, a token, or an Alertmanager address. Those live in settings.

And what a silence is NOT, stated because every one of these is a mistake
someone will otherwise make:

    a silence does not acknowledge an incident
    a silence does not resolve or close an incident
    a silence does not delete alert history
    a silence does not make an SLO healthy

It suppresses Alertmanager notifications for a bounded time. That is all it
does, and the UI says so.

**Pending is not active.** A silence Drake has not yet created at
Alertmanager, or failed to create, is never shown as active — an operator
who believes an alert is suppressed when it is not will not be watching it.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.alerting.ingest import load_token
from drake_api.notifications.webhook import (
    DestinationRefusedError,
    Resolver,
    validate_destination,
)
from drake_api.settings import AlertmanagerIntegration, Settings, WebhookDestination

logger = logging.getLogger("drake_api.alerting.silences")

# The labels a silence may match on. Every one of them is a value Drake
# resolved from the catalog or from the alert's own allowlisted labels —
# there is no path from a request body to this list.
SILENCEABLE_LABELS: tuple[str, ...] = (
    "alertname",
    "project",
    "environment",
    "service",
    "cluster",
    "namespace",
)

MAX_MATCHERS = 8


class SilenceError(ValueError):
    """A bounded, safe rejection code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SilenceAttempt:
    outcome: str  # active | retryable | terminal | refused
    provider_silence_id: str | None = None
    error_code: str | None = None


def build_matchers(
    alert_labels: dict[str, str], *, project_key: str, alert_name: str
) -> list[dict[str, Any]]:
    """Exact matchers, composed by the server from resolved values.

    Always anchored on the project, so a silence cannot escape the project
    the requester had authority over even if a label were somehow wrong.
    """
    matchers: list[dict[str, Any]] = [
        {"name": "project", "value": project_key, "isRegex": False, "isEqual": True},
        {"name": "alertname", "value": alert_name, "isRegex": False, "isEqual": True},
    ]
    for label in SILENCEABLE_LABELS:
        if label in ("project", "alertname"):
            continue
        value = alert_labels.get(label)
        if value:
            matchers.append(
                {"name": label, "value": value, "isRegex": False, "isEqual": True}
            )
        if len(matchers) >= MAX_MATCHERS:
            break
    return matchers


def clamp_duration(integration: AlertmanagerIntegration, requested: int) -> int:
    """Server-configured bounds. A silence is a pause, not an off switch."""
    low = max(60, integration.min_silence_seconds)
    high = min(604_800, integration.max_silence_seconds)
    if high < low:
        raise SilenceError("silence_duration_bounds_invalid")
    if requested < low or requested > high:
        raise SilenceError("silence_duration_out_of_range")
    return requested


def idempotency_key(
    *, integration_key: str, alert_id: Any, matchers: list[dict[str, Any]], supplied: str | None
) -> str:
    """Stable across a retried request, distinct across real requests.

    A client that lost the response and repeats the call must not create a
    second silence at Alertmanager — the first one is already suppressing
    the alert, and the second would outlive it.
    """
    if supplied:
        material = f"{integration_key}:{supplied}"
    else:
        canonical = json.dumps(matchers, sort_keys=True, separators=(",", ":"))
        material = f"{integration_key}:{alert_id}:{canonical}"
    return hashlib.sha256(material.encode()).hexdigest()[:48]


def _destination(
    integration: AlertmanagerIntegration, path: str
) -> WebhookDestination:
    """Reuse the webhook SSRF boundary for the Alertmanager API.

    Same validation, same pinning, same refusal to follow redirects. A
    second outbound HTTP path would be a second place for the boundary to
    be wrong.
    """
    base = integration.api_base_url.rstrip("/")
    return WebhookDestination(
        url=f"{base}{path}",
        allow_private=integration.allow_private,
        timeout_seconds=integration.api_timeout_seconds,
    )


async def _call(
    integration: AlertmanagerIntegration,
    settings: Settings,
    *,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[int | None, dict[str, Any] | None, str | None]:
    """One bounded call to Alertmanager API v2.

    `ssl_context` exists so a test can trust a private CA. It can only ADD a
    trust anchor: a context that does not verify certificates or hostnames
    is refused outright.
    """
    if ssl_context is not None and (
        not ssl_context.check_hostname or ssl_context.verify_mode != ssl.CERT_REQUIRED
    ):
        raise ValueError("an Alertmanager TLS context must verify certificates and hostnames")
    if not integration.api_base_url:
        return None, None, "alertmanager_api_not_configured"

    destination = _destination(integration, path)
    try:
        # Re-resolved and re-checked on EVERY attempt: a name that was public
        # when the silence was requested is not a promise about now.
        target = await validate_destination(destination, settings, resolver)
    except DestinationRefusedError as error:
        return None, None, error.code

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # The URL points at the pinned IP, so Alertmanager needs the real
        # name to route the request.
        "Host": target.host,
    }
    token = load_token(integration.api_token_file)
    if token is not None:
        headers["Authorization"] = f"Bearer {token.decode(errors='ignore')}"

    timeout = httpx.Timeout(
        min(integration.api_timeout_seconds, 30.0),
        connect=min(integration.api_timeout_seconds, 5.0),
    )
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            # A redirect is how a validated endpoint sends Drake somewhere
            # it was not allowed to go.
            follow_redirects=False,
            transport=transport,
            verify=ssl_context if ssl_context is not None else True,
        ) as client:
            response = await client.request(
                method,
                target.url,
                json=body,
                headers=headers,
                extensions={"sni_hostname": target.sni},
            )
    except httpx.TimeoutException:
        return None, None, "timeout"
    except httpx.HTTPError:
        return None, None, "transport_error"

    if 300 <= response.status_code < 400:
        return response.status_code, None, "provider_redirect_refused"
    payload: dict[str, Any] | None = None
    if 200 <= response.status_code < 300:
        try:
            decoded = response.json()
            payload = decoded if isinstance(decoded, dict) else None
        except ValueError:
            payload = None
    return response.status_code, payload, None


async def create_silence(
    integration: AlertmanagerIntegration,
    settings: Settings,
    *,
    matchers: list[dict[str, Any]],
    starts_at: datetime,
    ends_at: datetime,
    comment: str,
    created_by: str,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> SilenceAttempt:
    """POST /api/v2/silences. Never raises for a provider failure."""
    status, payload, error = await _call(
        integration,
        settings,
        method="POST",
        path="/api/v2/silences",
        body={
            "matchers": matchers,
            "startsAt": starts_at.astimezone(UTC).isoformat(),
            "endsAt": ends_at.astimezone(UTC).isoformat(),
            "createdBy": created_by,
            "comment": comment,
        },
        transport=transport,
        resolver=resolver,
        ssl_context=ssl_context,
    )
    if error is not None:
        retryable = error in ("timeout", "transport_error")
        return SilenceAttempt("retryable" if retryable else "refused", None, error)
    assert status is not None
    if 200 <= status < 300:
        silence_id = str((payload or {}).get("silenceID") or (payload or {}).get("id") or "")
        if not silence_id:
            # Accepted but unidentifiable. Without an id the silence cannot
            # be expired later, so it is not treated as active.
            return SilenceAttempt("terminal", None, "provider_response_incomplete")
        return SilenceAttempt("active", silence_id[:128], None)
    if status in (408, 429) or status >= 500:
        return SilenceAttempt("retryable", None, f"http_{status}")
    # The provider's own body is deliberately not read into anything Drake
    # keeps: an error page can contain its configuration or its own secrets.
    return SilenceAttempt("terminal", None, f"http_{status}")


async def expire_silence(
    integration: AlertmanagerIntegration,
    settings: Settings,
    *,
    provider_silence_id: str,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> SilenceAttempt:
    """DELETE /api/v2/silence/{id}. Idempotent: a 404 means already gone."""
    status, _payload, error = await _call(
        integration,
        settings,
        method="DELETE",
        path=f"/api/v2/silence/{provider_silence_id}",
        body=None,
        transport=transport,
        resolver=resolver,
        ssl_context=ssl_context,
    )
    if error is not None:
        retryable = error in ("timeout", "transport_error")
        return SilenceAttempt("retryable" if retryable else "refused", None, error)
    assert status is not None
    if 200 <= status < 300 or status == 404:
        # Already expired at the provider is the outcome we wanted.
        return SilenceAttempt("active", provider_silence_id, None)
    if status in (408, 429) or status >= 500:
        return SilenceAttempt("retryable", None, f"http_{status}")
    return SilenceAttempt("terminal", None, f"http_{status}")


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


async def request_silence(
    connection: AsyncConnection,
    *,
    integration_id: uuid.UUID,
    project_id: uuid.UUID,
    alert_instance_id: uuid.UUID | None,
    incident_id: uuid.UUID | None,
    matchers: list[dict[str, Any]],
    seconds: int,
    reason_code: str,
    reason_note: str | None,
    actor_identity_id: uuid.UUID,
    key: str,
) -> tuple[uuid.UUID, bool]:
    """Record the request. Returns `(id, created)`.

    Written as `pending`: the provider has not been called yet, and a row
    that claimed otherwise would be a lie for as long as the worker took.
    """
    row = (
        await connection.execute(
            text(
                """
                INSERT INTO silence_requests
                    (integration_id, project_id, alert_instance_id, incident_id, matchers,
                     requested_seconds, reason_code, reason_note, actor_identity_id,
                     state, idempotency_key, next_attempt_at)
                VALUES (:integration, :project, :alert, :incident, CAST(:matchers AS jsonb),
                        :seconds, :reason, :note, :actor, 'pending', :key, now())
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "integration": integration_id,
                "project": project_id,
                "alert": alert_instance_id,
                "incident": incident_id,
                "matchers": json.dumps(matchers),
                "seconds": seconds,
                "reason": reason_code,
                "note": reason_note,
                "actor": actor_identity_id,
                "key": key,
            },
        )
    ).first()
    if row is not None:
        return uuid.UUID(str(row[0])), True
    existing = (
        await connection.execute(
            text("SELECT id FROM silence_requests WHERE idempotency_key = :key"), {"key": key}
        )
    ).scalar_one()
    return uuid.UUID(str(existing)), False


async def _claim(engine: AsyncEngine, limit: int) -> list[dict[str, Any]]:
    """Take a bounded batch of due silence work."""
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT s.id, s.state, s.matchers, s.requested_seconds, s.reason_code,
                           s.reason_note, s.provider_silence_id, s.attempts, i.config_ref,
                           s.alert_instance_id, s.incident_id
                    FROM silence_requests s
                    JOIN integrations i ON i.id = s.integration_id
                    WHERE s.state IN ('pending', 'cancel_pending')
                      AND (s.next_attempt_at IS NULL OR s.next_attempt_at <= now())
                    ORDER BY s.requested_at
                    LIMIT :limit
                    FOR UPDATE OF s SKIP LOCKED
                    """
                ),
                {"limit": limit},
            )
        ).all()
        claimed = [
            {
                "id": uuid.UUID(str(row[0])),
                "state": row[1],
                "matchers": row[2],
                "seconds": row[3],
                "reason_code": row[4],
                "reason_note": row[5],
                "provider_silence_id": row[6],
                # The number this attempt WILL be: the claim below increments
                # it, and the retry budget is about attempts made, not
                # attempts already finished.
                "attempts": int(row[7]) + 1,
                "config_ref": row[8],
                "alert_instance_id": row[9],
                "incident_id": row[10],
            }
            for row in rows
        ]
        if claimed:
            await connection.execute(
                text(
                    "UPDATE silence_requests SET attempts = attempts + 1, "
                    "next_attempt_at = now() + interval '60 seconds' WHERE id = ANY(:ids)"
                ),
                {"ids": [item["id"] for item in claimed]},
            )
    return claimed


async def _finish(
    engine: AsyncEngine,
    silence_id: uuid.UUID,
    *,
    state: str,
    provider_silence_id: str | None,
    error_code: str | None,
    seconds: int,
    incident_id: Any,
) -> None:
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE silence_requests
                SET state = :state,
                    provider_silence_id = COALESCE(:provider, provider_silence_id),
                    error_code = :error,
                    starts_at = CASE WHEN :state = 'active' THEN COALESCE(starts_at, :now)
                                     ELSE starts_at END,
                    ends_at = CASE WHEN :state = 'active'
                                   THEN COALESCE(ends_at, :now + make_interval(secs => :seconds))
                                   ELSE ends_at END,
                    next_attempt_at = NULL,
                    version = version + 1,
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": silence_id,
                "state": state,
                "provider": provider_silence_id,
                "error": error_code,
                "now": now,
                "seconds": seconds,
            },
        )
        if state == "active" and provider_silence_id is not None:
            await connection.execute(
                text(
                    "UPDATE alert_instances SET silenced = true "
                    "WHERE id = (SELECT alert_instance_id FROM silence_requests WHERE id = :id)"
                ),
                {"id": silence_id},
            )
        if incident_id is not None:
            await connection.execute(
                text(
                    """
                    INSERT INTO incident_events (incident_id, event_type, occurred_at, detail)
                    VALUES (:incident, :type, :at, CAST(:detail AS jsonb))
                    """
                ),
                {
                    "incident": incident_id,
                    "type": "silence_active" if state == "active" else "silence_failed",
                    "at": now,
                    # A bounded code, never the provider's own message.
                    "detail": json.dumps({"error_code": error_code} if error_code else {}),
                },
            )


async def process_pending(
    engine: AsyncEngine,
    settings: Settings,
    *,
    limit: int = 20,
    max_attempts: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> int:
    """One bounded pass over due silence requests.

    The provider call happens here, outside any request transaction: an
    Alertmanager that is slow must not hold a database lock, and one that
    fails must not roll back the audited request that caused it.
    """
    processed = 0
    for item in await _claim(engine, limit):
        integration = settings.alertmanager_integrations.get(str(item["config_ref"]))
        if integration is None:
            await _finish(
                engine,
                item["id"],
                state="failed",
                provider_silence_id=None,
                error_code="integration_unknown",
                seconds=item["seconds"],
                incident_id=item["incident_id"],
            )
            continue

        now = datetime.now(UTC)
        if item["state"] == "cancel_pending":
            provider_id = item["provider_silence_id"]
            if not provider_id:
                # Nothing was ever created at the provider, so there is
                # nothing to expire. Cancelled is the honest end state.
                await _finish(
                    engine,
                    item["id"],
                    state="cancelled",
                    provider_silence_id=None,
                    error_code=None,
                    seconds=item["seconds"],
                    incident_id=item["incident_id"],
                )
                processed += 1
                continue
            attempt = await expire_silence(
                integration,
                settings,
                provider_silence_id=str(provider_id),
                transport=transport,
                resolver=resolver,
                ssl_context=ssl_context,
            )
            state = "expired" if attempt.outcome == "active" else _retry_state(
                attempt.outcome, item["attempts"], max_attempts, "cancel_pending"
            )
        else:
            attempt = await create_silence(
                integration,
                settings,
                matchers=list(item["matchers"]),
                starts_at=now,
                ends_at=now + timedelta(seconds=int(item["seconds"])),
                comment=_comment(item),
                # A stable internal reference, never an email or a name: the
                # comment field ends up in every Alertmanager UI and API.
                created_by=f"drake:{item['id']}",
                transport=transport,
                resolver=resolver,
                ssl_context=ssl_context,
            )
            state = "active" if attempt.outcome == "active" else _retry_state(
                attempt.outcome, item["attempts"], max_attempts, "pending"
            )

        if state in ("pending", "cancel_pending"):
            # Still retrying. The claim already scheduled the next attempt.
            continue
        await _finish(
            engine,
            item["id"],
            state=state,
            provider_silence_id=attempt.provider_silence_id,
            error_code=attempt.error_code,
            seconds=item["seconds"],
            incident_id=item["incident_id"],
        )
        processed += 1
    return processed


def _retry_state(outcome: str, attempts: int, max_attempts: int, pending_state: str) -> str:
    """Retry a transient failure; never dress a failure up as success."""
    if outcome == "retryable" and attempts < max_attempts:
        return pending_state
    return "failed"


def _comment(item: dict[str, Any]) -> str:
    reason = str(item["reason_code"])
    note = item.get("reason_note")
    text_value = f"Drake silence: {reason}"
    if note:
        text_value = f"{text_value} — {note}"
    return text_value[:280]


class SilenceWorker:
    """Lifespan-owned loop. Started only when the feature flag is on."""

    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._transport = transport
        self._interval = max(10.0, settings.silence_worker_interval_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="silence-worker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await process_pending(
                    self._engine,
                    self._settings,
                    limit=self._settings.silence_worker_batch_size,
                    max_attempts=self._settings.silence_max_attempts,
                    transport=self._transport,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("silence worker: cycle failed")
            await asyncio.sleep(self._interval)
