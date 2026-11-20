"""Alert rule endpoints (ADR-0031).

Reads need `alert.view` at the rule's project, writes `alert_rule.manage`.
Every write records its audit row in the same transaction, so a rule never
exists, changes or disappears without a record of who did it.

No endpoint here queries the metrics provider: lists, history and previews
all read the samples the incident runner already stored.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from drake_api.alert_rules import repository as repo
from drake_api.alert_rules.model import MAX_FOR_SECONDS, OPERATORS, SEVERITIES, SIGNALS
from drake_api.audit.service import AuditEventData, record_audit_event_in
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.settings import Settings

router = APIRouter(prefix="/v1", tags=["alert-rules"])

_STATUS_BY_CODE: dict[str, int] = {"not_found": 404, "version_conflict": 409}
Signal = Literal[
    "error_ratio",
    "latency_p95_seconds",
    "request_rate",
    "restarts",
    "cpu_utilization",
    "memory_utilization",
    "unavailable_replicas",
]
Operator = Literal[">", "<"]
Severity = Literal["critical", "warning"]


# The literals above are the model's vocabularies, spelled out for the schema.
assert set(get_args(Signal)) == set(SIGNALS)
assert set(get_args(Operator)) == set(OPERATORS)
assert set(get_args(Severity)) == set(SEVERITIES)


def _fail(error: repo.RuleError) -> HTTPException:
    status = _STATUS_BY_CODE.get(error.code, 422)
    return HTTPException(status_code=status, detail="not found" if status == 404 else str(error))


def _now() -> datetime:
    return datetime.now(UTC)


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal: Signal
    operator: Operator
    threshold: float = Field(ge=0)
    for_seconds: int = Field(ge=0, le=MAX_FOR_SECONDS)
    severity: Severity


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID
    environment_id: uuid.UUID | None = None
    service_id: uuid.UUID | None = None


class RuleCreate(Condition, Target):
    name: str = Field(min_length=1, max_length=120)


class RulePreview(Condition, Target):
    pass


class RuleUpdate(BaseModel):
    """The target is fixed once a rule exists: moving a rule to another
    service would silently rewrite what its history means."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    signal: Signal | None = None
    operator: Operator | None = None
    threshold: float | None = Field(default=None, ge=0)
    for_seconds: int | None = Field(default=None, ge=0, le=MAX_FOR_SECONDS)
    severity: Severity | None = None
    enabled: bool | None = None
    expected_version: int = Field(ge=1)


class MuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(ge=0, le=repo.MAX_MUTE_MINUTES)


def _audit(
    auth: AuthContext, action: str, rule_id: str, metadata: dict[str, Any]
) -> AuditEventData:
    return AuditEventData(
        actor_type="user",
        actor_id=auth.session.identity_id,
        action=action,
        result="success",
        target_type="alert_rule",
        target_id=rule_id,
        correlation_id=correlation_id_var.get(),
        metadata=metadata,
    )


@router.get("/alert-rules/options")
async def rule_options(
    request: Request, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        body = await repo.options(connection, auth.principal)
    # How often the runner judges a rule, so the screen says what is true
    # for this deployment rather than a default (120 s in prod).
    return {
        **body,
        "evaluation_interval_seconds": max(15, int(settings.incident_runner_interval_seconds)),
    }


@router.get("/alert-rules")
async def list_rules(
    request: Request,
    project_id: uuid.UUID | None = Query(default=None),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        return await repo.list_rules(connection, auth.principal, project_id=project_id, now=_now())


@router.post("/alert-rules/preview")
async def preview_rule(
    request: Request, payload: RulePreview, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """A read, but posted: the draft rule is a body, not a query string."""
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        try:
            return await repo.preview(connection, auth.principal, payload.model_dump(), _now())
        except repo.RuleError as error:
            raise _fail(error) from error


@router.post("/alert-rules", status_code=201)
async def create_rule(
    request: Request, payload: RuleCreate, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    actor = uuid.UUID(auth.session.identity_id)
    async with get_engine(settings).begin() as connection:
        try:
            created = await repo.create_rule(
                connection, auth.principal, payload.model_dump(), actor
            )
        except repo.RuleError as error:
            raise _fail(error) from error
        await record_audit_event_in(
            connection,
            _audit(
                auth,
                "alert_rule.create",
                created["id"],
                {"signal": payload.signal, "severity": payload.severity},
            ),
        )
    return created


@router.post("/alert-rules/{rule_id}")
async def update_rule(
    request: Request,
    rule_id: uuid.UUID,
    payload: RuleUpdate,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    values = payload.model_dump(exclude_none=True, exclude={"expected_version"})
    actor = uuid.UUID(auth.session.identity_id)
    async with get_engine(settings).begin() as connection:
        try:
            updated = await repo.update_rule(
                connection, auth.principal, rule_id, values, payload.expected_version, actor
            )
        except repo.RuleError as error:
            raise _fail(error) from error
        await record_audit_event_in(
            connection, _audit(auth, "alert_rule.update", str(rule_id), {"fields": sorted(values)})
        )
    return updated


@router.post("/alert-rules/{rule_id}/mute")
async def mute_rule(
    request: Request,
    rule_id: uuid.UUID,
    payload: MuteRequest,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """`minutes: 0` unmutes. A mute stops a critical rule opening incidents;
    the rule is still judged and its history still recorded."""
    settings: Settings = request.app.state.settings
    actor = uuid.UUID(auth.session.identity_id)
    async with get_engine(settings).begin() as connection:
        try:
            muted = await repo.mute_rule(
                connection, auth.principal, rule_id, payload.minutes, _now(), actor
            )
        except repo.RuleError as error:
            raise _fail(error) from error
        await record_audit_event_in(
            connection, _audit(auth, "alert_rule.mute", str(rule_id), {"minutes": payload.minutes})
        )
    return muted


@router.delete("/alert-rules/{rule_id}")
async def delete_rule(
    request: Request, rule_id: uuid.UUID, auth: AuthContext = Depends(require_csrf)
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).begin() as connection:
        try:
            await repo.delete_rule(connection, auth.principal, rule_id)
        except repo.RuleError as error:
            raise _fail(error) from error
        await record_audit_event_in(connection, _audit(auth, "alert_rule.delete", str(rule_id), {}))
    return {"deleted": str(rule_id)}


@router.get("/alert-rules/{rule_id}/history")
async def rule_history(
    request: Request, rule_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with get_engine(settings).connect() as connection:
        try:
            return await repo.rule_history(connection, auth.principal, rule_id, _now())
        except repo.RuleError as error:
            raise _fail(error) from error
