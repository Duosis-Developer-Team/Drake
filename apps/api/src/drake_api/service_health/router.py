"""Service health and workload binding endpoints.

The API answers with decisions, not with material to decide from: a caller
receives a status and reason codes, never a query, a threshold to apply, or
a datasource credential. That keeps one judgement in one place and stops
two clients disagreeing about what "healthy" means.
"""

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from drake_api.audit.service import AuditEventData, record_audit_event
from drake_api.auth.dependencies import AuthContext, require_auth, require_csrf
from drake_api.correlation import correlation_id_var
from drake_api.db import get_engine
from drake_api.service_health import bindings as binding_repo
from drake_api.service_health.cache import HealthCache
from drake_api.service_health.orchestrator import (
    HealthOrchestrator,
    preset_of,
    readable_signals,
    summarize_signals,
)
from drake_api.service_health.policy import get_policy, policy_keys
from drake_api.service_health.presets import DEFAULT_PRESET_KEY, describe_presets, preset_keys
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.service_health")

router = APIRouter(prefix="/v1", tags=["service-health"])

# Uniform for anything the caller may not see, exactly as the catalog does:
# a 403 would confirm the resource exists.
_NOT_FOUND = "not found"

# The only windows a chart may ask for. Fixed pairs rather than free
# range/step inputs: an operator cannot request a year at one-second
# resolution, so no request can be shaped into a provider denial-of-service.
SERIES_RANGES: dict[str, tuple[int, int]] = {
    "15m": (900, 30),
    "1h": (3600, 60),
    "6h": (21600, 300),
    "24h": (86400, 900),
}
DEFAULT_SERIES_RANGE = "1h"

# How many services' health may be computed at once for one list request.
_LIST_CONCURRENCY = 6


class BindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_service_id: uuid.UUID
    cluster_id: uuid.UUID
    namespace: str = Field(min_length=1, max_length=63)
    workload_kind: str = Field(min_length=1, max_length=32)
    workload_name: str = Field(min_length=1, max_length=253)
    preset_key: str = Field(default=DEFAULT_PRESET_KEY, max_length=64)
    health_policy_key: str = Field(default="default.v1", max_length=64)
    integration_id: uuid.UUID | None = None


class BindingLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lifecycle: str = Field(pattern="^(active|disabled)$")
    expected_revision: int | None = None


class BindingUpdate(BaseModel):
    """How a binding is read — never what it points at.

    `extra=forbid` is the load-bearing part: there is no field here for a
    selector, a label matcher, or a query, so none can arrive.
    """

    model_config = ConfigDict(extra="forbid")
    preset_key: str = Field(max_length=64)
    health_policy_key: str = Field(max_length=64)
    expected_revision: int | None = None


def _orchestrator(request: Request) -> HealthOrchestrator:
    settings: Settings = request.app.state.settings
    return HealthOrchestrator(
        get_engine(settings),
        request.app.state.telemetry_broker,
        request.app.state.telemetry_registry,
        HealthCache(request.app.state.telemetry_redis),
    )


async def _visible_context(
    request: Request, auth: AuthContext, binding_id: uuid.UUID
) -> dict[str, Any]:
    """Resolve a binding the caller may see, or 404 exactly as if it were absent.

    Visibility is decided by the scope-filtered repository first; only then
    is the orchestrator's wider context read. Doing it the other way round
    would let a timing difference answer "does this binding exist".
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        visible = await binding_repo.get_binding(connection, auth.principal, binding_id)
    if visible is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    context = await _orchestrator(request).load_context(binding_id)
    if context is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return context


@router.get("/service-health/presets")
async def list_presets(auth: AuthContext = Depends(require_auth)) -> dict[str, Any]:
    """What a binding may be configured with.

    Template keys and descriptions only. The expressions behind them stay
    on the server; a caller has no reason to see one and no way to supply
    one.
    """
    return {
        "presets": describe_presets(),
        "policies": [
            {
                "key": key,
                "title": get_policy(key).title,
                "max_telemetry_age_seconds": get_policy(key).max_telemetry_age_seconds,
                "window_seconds": get_policy(key).window_seconds,
            }
            for key in policy_keys()
        ],
        "workload_kinds": sorted(binding_repo.WORKLOAD_KINDS),
    }


@router.get("/service-health/bindings")
async def list_bindings(
    request: Request,
    environment_service_id: uuid.UUID | None = None,
    limit: int = Query(default=binding_repo.DEFAULT_PAGE_SIZE, ge=1, le=binding_repo.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        return await binding_repo.list_bindings(
            connection,
            auth.principal,
            environment_service_id=environment_service_id,
            limit=limit,
            offset=offset,
        )


@router.get("/service-health/bindings/{binding_id}")
async def get_binding(
    request: Request, binding_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        binding = await binding_repo.get_binding(connection, auth.principal, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return binding


@router.post("/service-health/bindings", status_code=201)
async def create_binding(
    request: Request, payload: BindingCreate, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    target = binding_repo.BindingTarget(
        environment_service_id=payload.environment_service_id,
        cluster_id=payload.cluster_id,
        namespace=payload.namespace,
        workload_kind=payload.workload_kind,
        workload_name=payload.workload_name,
        preset_key=payload.preset_key,
        health_policy_key=payload.health_policy_key,
        integration_id=payload.integration_id,
    )

    async with engine.begin() as connection:
        try:
            created = await binding_repo.create_binding(
                connection,
                auth.principal,
                target,
                preset_keys(),
                uuid.UUID(auth.session.identity_id),
            )
        except binding_repo.BindingError as error:
            if error.code == "not_found":
                raise HTTPException(status_code=404, detail=_NOT_FOUND) from error
            status = 409 if error.code == "duplicate_binding" else 422
            raise HTTPException(status_code=status, detail=str(error)) from error

    # Audited only after the write actually happened.
    await record_audit_event(
        engine,
        AuditEventData(
            actor_type="user",
            actor_id=auth.session.identity_id,
            action="service_health.binding.create",
            result="success",
            target_type="service_workload_binding",
            target_id=created["id"],
            correlation_id=correlation_id_var.get(),
            metadata={
                "namespace": payload.namespace,
                "workload_kind": payload.workload_kind,
                "workload_name": payload.workload_name,
                "preset_key": payload.preset_key,
                "health_policy_key": payload.health_policy_key,
                "resolved": created["resolved"],
            },
        ),
    )
    return created


@router.post("/service-health/bindings/{binding_id}/lifecycle")
async def set_binding_lifecycle(
    request: Request,
    binding_id: uuid.UUID,
    payload: BindingLifecycle,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        try:
            result = await binding_repo.set_lifecycle(
                connection,
                auth.principal,
                binding_id,
                payload.lifecycle,
                payload.expected_revision,
                uuid.UUID(auth.session.identity_id),
            )
        except binding_repo.BindingError as error:
            if error.code == "not_found":
                raise HTTPException(status_code=404, detail=_NOT_FOUND) from error
            status = 409 if error.code == "revision_conflict" else 422
            raise HTTPException(status_code=status, detail=str(error)) from error

    # A no-op is not a change, so it produces no audit entry.
    if result["changed"]:
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action=f"service_health.binding.{payload.lifecycle}",
                result="success",
                target_type="service_workload_binding",
                target_id=str(binding_id),
                correlation_id=correlation_id_var.get(),
                metadata={"lifecycle": payload.lifecycle},
            ),
        )
    return result


@router.post("/service-health/bindings/{binding_id}/resolve")
async def resolve_binding(
    request: Request, binding_id: uuid.UUID, auth: AuthContext = Depends(require_csrf)
) -> dict[str, Any]:
    """Re-check the bound workload against cluster inventory.

    A workload that is not found leaves the binding in place and reports
    `unresolved`: an agent that has not reported yet is not a reason to
    discard an operator's configuration.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        visible = await binding_repo.get_binding(connection, auth.principal, binding_id)
    if visible is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    async with engine.begin() as connection:
        result = await binding_repo.refresh_resolution(connection, binding_id)
    if result is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    # Re-resolving to the SAME workload leaves the binding's cache identity
    # unchanged, so the cached verdict would survive an action the operator
    # took specifically to get a fresh one. Drop it.
    orchestrator = _orchestrator(request)
    context = await orchestrator.load_context(binding_id)
    if context is not None:
        await orchestrator.invalidate(context)
    return result


@router.post("/service-health/bindings/{binding_id}")
async def update_binding(
    request: Request,
    binding_id: uuid.UUID,
    payload: BindingUpdate,
    auth: AuthContext = Depends(require_csrf),
) -> dict[str, Any]:
    """Change the preset or policy a binding is evaluated with.

    `expected_revision` is how two operators editing the same binding find
    out about each other: the second write is refused with 409 rather than
    silently overwriting the first.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.begin() as connection:
        try:
            result = await binding_repo.update_binding(
                connection,
                auth.principal,
                binding_id,
                payload.preset_key,
                payload.health_policy_key,
                payload.expected_revision,
                preset_keys(),
                uuid.UUID(auth.session.identity_id),
            )
        except binding_repo.BindingError as error:
            if error.code == "not_found":
                raise HTTPException(status_code=404, detail=_NOT_FOUND) from error
            status = 409 if error.code == "revision_conflict" else 422
            raise HTTPException(status_code=status, detail=str(error)) from error

    if result["changed"]:
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type="user",
                actor_id=auth.session.identity_id,
                action="service_health.binding.update",
                result="success",
                target_type="service_workload_binding",
                target_id=str(binding_id),
                correlation_id=correlation_id_var.get(),
                metadata={
                    "preset_key": payload.preset_key,
                    "health_policy_key": payload.health_policy_key,
                },
            ),
        )
    # The revision bump already makes every verdict computed under the old
    # preset or policy unaddressable — revision is part of the cache
    # identity. Nothing to delete.
    return result


# ---------------------------------------------------------------------------
# read path
# ---------------------------------------------------------------------------


@router.get("/service-health/bindings/{binding_id}/health")
async def binding_health(
    request: Request,
    binding_id: uuid.UUID,
    refresh: bool = Query(default=False),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The current verdict for one bound workload.

    The response carries the decision and the reasons for it. It carries no
    thresholds to apply and no queries to run, because a second consumer
    re-deriving "healthy" from raw numbers is how two screens start
    disagreeing about the same service.
    """
    context = await _visible_context(request, auth, binding_id)
    health = await _orchestrator(request).current_health(auth.principal, context, refresh=refresh)
    return {**health, "binding": _binding_summary(context)}


@router.get("/service-health/bindings/{binding_id}/metrics")
async def binding_metrics(
    request: Request, binding_id: uuid.UUID, auth: AuthContext = Depends(require_auth)
) -> dict[str, Any]:
    """Per-signal values behind the verdict.

    Every value may be `null`, and `null` is never rendered as `0`: a signal
    that was not collected, one that came back empty, and one that is
    genuinely zero are three different facts and stay three different states.
    """
    context = await _visible_context(request, auth, binding_id)
    orchestrator = _orchestrator(request)
    result, signals = await orchestrator.compute(auth.principal, context)
    return {
        "binding": _binding_summary(context),
        "status": str(result.status),
        "computed_at": result.computed_at.isoformat(),
        "partial": result.partial,
        "missing_signals": result.missing_signals,
        "metrics": summarize_signals(signals, preset_of(context)),
        "readable_signals": readable_signals(context),
    }


@router.get("/service-health/bindings/{binding_id}/series")
async def binding_series(
    request: Request,
    binding_id: uuid.UUID,
    signal: str = Query(max_length=32),
    # Shadows the builtin on purpose: the query-string name is the contract.
    range: str = Query(default=DEFAULT_SERIES_RANGE, max_length=8),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """One signal over a bounded window.

    `signal` names a slot in the binding's preset, not a metric and not a
    template. `range` picks from a fixed set of window/step pairs. Between
    them there is no input that reaches the provider unshaped.
    """
    if range not in SERIES_RANGES:
        raise HTTPException(status_code=422, detail="unsupported range")
    context = await _visible_context(request, auth, binding_id)
    range_seconds, step_seconds = SERIES_RANGES[range]
    series = await _orchestrator(request).read_series(
        auth.principal, context, signal, range_seconds, step_seconds
    )
    return {**series, "range_key": range, "binding": _binding_summary(context)}


@router.get("/service-health/services")
async def service_health_list(
    request: Request,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    limit: int = Query(default=binding_repo.DEFAULT_PAGE_SIZE, ge=1, le=binding_repo.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """Services in scope with their health.

    Unbound services are listed with a `not_configured` verdict rather than
    omitted: a list that hid them would make an unobserved estate look like
    a healthy one.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        page = await binding_repo.list_service_rows(
            connection,
            auth.principal,
            project_id=project_id,
            environment_id=environment_id,
            limit=limit,
            offset=offset,
        )

    orchestrator = _orchestrator(request)
    semaphore = asyncio.Semaphore(_LIST_CONCURRENCY)

    async def health_for(item: dict[str, Any]) -> dict[str, Any]:
        binding = item.get("binding")
        async with semaphore:
            context = (
                None
                if binding is None
                else await orchestrator.load_context(uuid.UUID(binding["id"]))
            )
            health = await orchestrator.current_health(auth.principal, context)
        # The row keeps only what a list needs to be read at a glance. The
        # full section breakdown lives on the detail screen.
        return {
            **item,
            "health": {
                "status": health["status"],
                "computed_at": health["computed_at"],
                "newest_sample_at": health.get("newest_sample_at"),
                "freshness_age_seconds": health.get("freshness_age_seconds"),
                "partial": health.get("partial", False),
                "served_from_last_good": health.get("served_from_last_good", False),
                "reasons": health.get("reasons", []),
                "availability": health.get("availability", {}),
                "stability": health.get("stability", {}),
                "resources": health.get("resources", {}),
            },
        }

    items = await asyncio.gather(*(health_for(item) for item in page["items"]))
    return {**page, "items": list(items)}


@router.get("/service-health/binding-options")
async def binding_form_options(
    request: Request,
    environment_service_id: uuid.UUID | None = None,
    cluster_id: uuid.UUID | None = None,
    namespace: str | None = Query(default=None, max_length=63),
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The choices a binding form may offer.

    Dependent by construction: namespaces only appear once a cluster is
    chosen, workloads only once a namespace is. Every option is a row the
    caller can already see in cluster inventory, which is what lets the form
    be selects rather than a text field.
    """
    settings: Settings = request.app.state.settings
    engine = get_engine(settings)
    async with engine.connect() as connection:
        options = await binding_repo.binding_options(
            connection, auth.principal, cluster_id=cluster_id, namespace=namespace
        )
        datasource = (
            None
            if environment_service_id is None
            else await binding_repo.datasource_state(connection, environment_service_id)
        )
    return {
        **options,
        "datasource": datasource,
        "presets": describe_presets(),
        "policies": [{"key": key, "title": get_policy(key).title} for key in policy_keys()],
        "ranges": sorted(SERIES_RANGES),
    }


def _binding_summary(context: dict[str, Any]) -> dict[str, Any]:
    """What the UI shows about the binding a verdict came from."""
    return {
        "id": context["id"],
        "lifecycle": context["lifecycle"],
        "resolved": context["resolved"],
        "resolved_at": context["resolved_at"],
        "revision": context["revision"],
        "namespace": context["namespace"],
        "workload_kind": context["workload_kind"],
        "workload_name": context["workload_name"],
        "cluster_ref": context["cluster_ref"],
        "cluster_id": context["cluster_id"],
        "preset_key": context["preset_key"],
        "health_policy_key": context["health_policy_key"],
        "project_key": context["project_key"],
        "environment_key": context["environment_key"],
        "service_key": context["service_key"],
        "environment_service_id": context["environment_service_id"],
        # State only. No URL, no credential, no config reference.
        "datasource_configured": context["datasource_configured"],
    }
