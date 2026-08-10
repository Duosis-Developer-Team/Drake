"""Alert, SLO and silence endpoints.

Reads are reads. No endpoint here ingests an alert, evaluates an SLO, or
opens an incident as a side effect of being called — that would make the
estate's history depend on who opened which page.

The mutations are deliberately few and deliberately narrow:

- **Silence** takes an alert, a bounded duration and a reason from a
  reviewed vocabulary. It does not take a matcher, a regex, a label, an
  Alertmanager address or a credential; matchers are composed server-side
  from values Drake already resolved.
- **Expire** takes a silence and cancels it.

There is no endpoint to write an alert, edit a rule, delete alert history,
change an SLO objective, or reach Alertmanager directly. A browser never
talks to Alertmanager: it talks to Drake, which talks to Alertmanager
through the audited outbox in `silences.py`.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from drake_api.alerting import repository as repo
from drake_api.alerting import silences
from drake_api.alerting.contracts import silence_reason_codes
from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.alerting")

router = APIRouter(prefix="/v1", tags=["alerting"])

# Uniform for anything the caller may not see. A 403 would confirm the
# alert exists, which is the enumeration this layer prevents.
_NOT_FOUND = "not found"


class SilenceRequestBody(BaseModel):
    """A source, a duration and a reason. Nothing else is accepted.

    `extra=forbid` is the point: there is no field for a matcher, a label, a
    regex, a URL, a comment template or an Alertmanager address, so none can
    arrive even by accident.
    """

    model_config = ConfigDict(extra="forbid")
    duration_seconds: int = Field(ge=60, le=604_800)
    reason_code: str = Field(min_length=1, max_length=64)
    reason_note: str | None = Field(default=None, max_length=280)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ExpireSilenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------


@router.get("/alerting/filters")
async def alerting_filters(_auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    """The accepted filter vocabulary — static, so it enumerates nothing."""
    return repo.filter_options()


@router.get("/alerts")
async def list_alerts(
    request: Request,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    cluster_id: uuid.UUID | None = None,
    status: str | None = Query(default=None, max_length=16),
    severity: str | None = Query(default=None, max_length=16),
    priority: str | None = Query(default=None, max_length=4),
    mapping_state: str | None = Query(default=None, max_length=16),
    silenced: bool | None = None,
    window: str | None = Query(default=None, max_length=8),
    limit: int = Query(default=repo.DEFAULT_PAGE_SIZE, ge=1, le=repo.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        try:
            return await repo.list_alerts(
                connection,
                auth.principal,
                project_id=project_id,
                environment_id=environment_id,
                service_id=service_id,
                cluster_id=cluster_id,
                status=status,
                severity=severity,
                priority=priority,
                mapping_state=mapping_state,
                silenced=silenced,
                window=window,
                limit=limit,
                offset=offset,
            )
        except repo.FilterError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/alerts/summary")
async def alerts_summary(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Counts over the caller's visible alerts, and only those."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        return await repo.alert_summary(connection, auth.principal)


@router.get("/alerts/{alert_id}")
async def get_alert(
    request: Request, alert_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        alert = await repo.get_alert(connection, auth.principal, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        # UI gating only. The mutation re-checks; hiding a button has never
        # been an authorization boundary.
        can_silence = await repo.can_silence(connection, auth.principal)
        silence_history = await repo.alert_silences(connection, auth.principal, alert_id)
    return {**alert, "can_silence": can_silence, "silences": silence_history}


@router.get("/alerts/{alert_id}/events")
async def alert_events(
    request: Request, alert_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        events = await repo.alert_events(connection, auth.principal, alert_id)
    if events is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"events": events}


# ---------------------------------------------------------------------------
# SLOs
# ---------------------------------------------------------------------------


@router.get("/slo")
async def list_slos(
    request: Request,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    indicator: str | None = Query(default=None, max_length=16),
    status: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=repo.DEFAULT_PAGE_SIZE, ge=1, le=repo.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        try:
            return await repo.list_slos(
                connection,
                auth.principal,
                project_id=project_id,
                environment_id=environment_id,
                service_id=service_id,
                indicator=indicator,
                status=status,
                limit=limit,
                offset=offset,
            )
        except repo.FilterError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/slo/{slo_id}")
async def get_slo(
    request: Request, slo_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        slo = await repo.get_slo(connection, auth.principal, slo_id)
        if slo is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        context = await repo.slo_context(connection, auth.principal, slo_id)
    return {**slo, "context": context}


@router.get("/slo/{slo_id}/evaluations")
async def slo_evaluations(
    request: Request,
    slo_id: uuid.UUID,
    limit: int = Query(default=30, ge=1, le=100),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Evaluation history, newest first.

    Each row carries the objective it was JUDGED against. Tightening a
    target today does not rewrite what last month was measured by.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        evaluations = await repo.slo_evaluations(connection, auth.principal, slo_id, limit=limit)
    if evaluations is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"evaluations": evaluations}


# ---------------------------------------------------------------------------
# silences
# ---------------------------------------------------------------------------


@router.get("/silences")
async def list_silences(
    request: Request,
    project_id: uuid.UUID | None = None,
    state: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=repo.DEFAULT_PAGE_SIZE, ge=1, le=repo.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        try:
            return await repo.list_silences(
                connection,
                auth.principal,
                project_id=project_id,
                state=state,
                limit=limit,
                offset=offset,
            )
        except repo.FilterError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/alerts/{alert_id}/silence", status_code=202)
async def request_silence(
    request: Request,
    alert_id: uuid.UUID,
    payload: SilenceRequestBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Ask Alertmanager to suppress this alert for a bounded time.

    `202`, not `200`: the silence has been RECORDED and audited, and the
    worker will call Alertmanager next. Answering `200` with an "active"
    silence before the provider agreed would tell an operator an alert is
    suppressed when it may not be.

    A silence does not acknowledge the incident, does not resolve it, does
    not delete history, and does not make an SLO healthy.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)

    from sqlalchemy import text

    async with engine.connect() as connection:
        alert = await repo.get_alert(connection, auth.principal, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        row = (
            await connection.execute(
                text(
                    """
                    SELECT a.integration_id, a.project_id, p.scope_id, p.project_key,
                           i.config_ref, a.incident_id
                    FROM alert_instances a
                    JOIN integrations i ON i.id = a.integration_id
                    LEFT JOIN projects p ON p.id = a.project_id
                    WHERE a.id = :id
                    """
                ),
                {"id": alert_id},
            )
        ).first()
        if row is None or row[1] is None:
            # An unmapped alert belongs to no project, so there is no scope
            # in which anyone could hold silence authority over it.
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        if not await repo.silence_authority(connection, auth.principal, row[2]):
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

    integration_key = str(row[4])
    integration = settings.alertmanager_integrations.get(integration_key)
    if integration is None:
        raise HTTPException(status_code=409, detail="integration_unavailable")
    if payload.reason_code not in silence_reason_codes():
        raise HTTPException(status_code=422, detail="unsupported reason")
    try:
        seconds = silences.clamp_duration(integration, payload.duration_seconds)
    except silences.SilenceError as error:
        raise HTTPException(status_code=422, detail=error.code) from error

    # Composed here, from values Drake resolved. There is no request field
    # this could have come from.
    matchers = silences.build_matchers(
        dict(alert["labels"]),
        project_key=str(row[3]),
        alert_name=str(alert["alert_name"]),
    )
    key = silences.idempotency_key(
        integration_key=integration_key,
        alert_id=alert_id,
        matchers=matchers,
        supplied=payload.idempotency_key,
    )

    async with engine.begin() as connection:
        silence_id, created = await silences.request_silence(
            connection,
            integration_id=uuid.UUID(str(row[0])),
            project_id=uuid.UUID(str(row[1])),
            alert_instance_id=alert_id,
            incident_id=uuid.UUID(str(row[5])) if row[5] else None,
            matchers=matchers,
            seconds=seconds,
            reason_code=payload.reason_code,
            reason_note=payload.reason_note,
            actor_identity_id=uuid.UUID(auth.session.identity_id),
            key=key,
        )
        if created and row[5]:
            await connection.execute(
                text(
                    """
                    INSERT INTO incident_events (incident_id, event_type, occurred_at, detail)
                    VALUES (:incident, 'silence_requested', now(), CAST(:detail AS jsonb))
                    """
                ),
                {
                    "incident": row[5],
                    "detail": '{"reason_code": "%s"}' % payload.reason_code,  # noqa: UP031
                },
            )

    if created:
        # Audited before the provider is called. A silence Drake attempted
        # is a fact whether or not Alertmanager accepted it.
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action="alert.silence.request",
                result="success",
                target_type="alert",
                target_id=str(alert_id),
                correlation_id=correlation_id_var.get(),
                metadata={
                    "reason_code": payload.reason_code,
                    "duration_seconds": seconds,
                    "matcher_count": len(matchers),
                },
            ),
        )
    return {"id": str(silence_id), "state": "pending", "created": created}


@router.post("/silences/{silence_id}/expire", status_code=202)
async def expire_silence(
    request: Request,
    silence_id: uuid.UUID,
    payload: ExpireSilenceBody,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Cancel a silence. Idempotent, audited, and provider-confirmed."""
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)

    from sqlalchemy import text

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    SELECT s.state, s.version, p.scope_id
                    FROM silence_requests s
                    JOIN projects p ON p.id = s.project_id
                    WHERE s.id = :id
                    """
                ),
                {"id": silence_id},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        if not await repo.silence_authority(connection, auth.principal, row[2]):
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

    if row[0] in ("expired", "cancelled", "failed"):
        return {"id": str(silence_id), "state": row[0], "changed": False}
    if payload.expected_version != row[1]:
        raise HTTPException(status_code=409, detail="the silence changed since it was read")

    async with engine.begin() as connection:
        updated = (
            await connection.execute(
                text(
                    """
                    UPDATE silence_requests
                    SET state = 'cancel_pending', next_attempt_at = now(),
                        version = version + 1, updated_at = now()
                    WHERE id = :id AND version = :expected
                      AND state IN ('pending', 'active')
                    RETURNING version
                    """
                ),
                {"id": silence_id, "expected": payload.expected_version},
            )
        ).first()
    if updated is None:
        raise HTTPException(status_code=409, detail="the silence changed since it was read")

    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="alert.silence.expire",
            result="success",
            target_type="silence",
            target_id=str(silence_id),
            correlation_id=correlation_id_var.get(),
            metadata={},
        ),
    )
    return {"id": str(silence_id), "state": "cancel_pending", "changed": True}
