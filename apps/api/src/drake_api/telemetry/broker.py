"""Query Broker orchestration.

The strict order (ADR-0015): session → shape → authoritative scope lookup →
telemetry.query grants → catalog relationships → template compatibility →
provider integration resolution → cache → budgets → provider call →
normalization/cache → safe response. Nothing provider- or cache-shaped
happens before authorization succeeds.
"""

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.catalog.authz import visible_scope_ids
from drake_api.correlation import correlation_id_var
from drake_api.rbac.service import Principal
from drake_api.settings import Settings
from drake_api.telemetry.budgets import (
    BudgetError,
    BudgetUnavailableError,
    ConcurrencyLeases,
    ConcurrencyRejectedError,
    resolve_range,
)
from drake_api.telemetry.cache import TelemetryCache, build_cache_keys
from drake_api.telemetry.compiler import CompileError, compile_query
from drake_api.telemetry.metrics import BrokerMetrics
from drake_api.telemetry.normalize import normalize_matrix
from drake_api.telemetry.observations import record_provider_observation
from drake_api.telemetry.provider import (
    ConnectorRefusedError,
    PrometheusAdapter,
    ProviderContractError,
    ProviderUnavailableError,
)
from drake_api.telemetry.registry import QueryTemplate, TelemetryRegistry, find_template


@dataclass(frozen=True)
class ResolvedScope:
    scope_id: uuid.UUID
    scope_ref: str
    source_values: dict[str, str]
    provider_scope_id: uuid.UUID


@dataclass(frozen=True)
class QueryInput:
    template_key: str
    scope_type: str
    scope_id: uuid.UUID
    from_dt: datetime
    to_dt: datetime
    step_seconds: int
    parameters: dict[str, str]


class TelemetryBroker:
    def __init__(
        self,
        *,
        settings: Settings,
        engine: AsyncEngine,
        redis: aioredis.Redis,
        registry: TelemetryRegistry,
        adapter: PrometheusAdapter,
        metrics: BrokerMetrics,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._registry = registry
        self._adapter = adapter
        self._metrics = metrics
        self._cache = TelemetryCache(redis)
        self._leases = ConcurrencyLeases(redis)

    async def query(self, principal: Principal, request: QueryInput) -> dict[str, Any]:
        started = time.monotonic()
        correlation_id = correlation_id_var.get() or ""

        async with self._engine.connect() as connection:
            # ADR-0015 order: authoritative scope lookup + grant resolution
            # come FIRST — template existence/compatibility must not act as
            # an oracle for unauthorized callers, and no cache, connector,
            # or integration-config access happens before authorization.
            resolved = await self._resolve_scope(connection, request.scope_type, request.scope_id)
            if resolved is None:
                raise HTTPException(status_code=404, detail="not found")
            visible = await visible_scope_ids(connection, principal, "telemetry.query")
            if resolved.scope_id not in visible:
                raise HTTPException(status_code=404, detail="not found")

            # Only an AUTHORIZED caller reaches template resolution:
            template = find_template(self._registry, request.template_key)
            if template is None:
                raise HTTPException(status_code=404, detail="not found")
            if request.scope_type not in template.scope_types:
                raise HTTPException(
                    status_code=422, detail="template does not support this scope type"
                )

            integration = await self._lookup_integration(connection, resolved.provider_scope_id)

        try:
            effective = resolve_range(
                template, request.from_dt, request.to_dt, request.step_seconds
            )
        except BudgetError as error:
            self._metrics.record_rejection("range_budget")
            raise HTTPException(status_code=422, detail=str(error)) from error

        try:
            compiled = compile_query(
                template,
                resolved.source_values,
                request.parameters,
                effective.effective_step_seconds,
            )
        except CompileError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        range_payload: dict[str, Any] = {
            "from": datetime.fromtimestamp(effective.from_ts, tz=UTC).isoformat(),
            "to": datetime.fromtimestamp(effective.to_ts, tz=UTC).isoformat(),
            "requested_step_seconds": effective.requested_step_seconds,
            "effective_step_seconds": effective.effective_step_seconds,
            "step_adjusted": effective.step_adjusted,
        }
        base_envelope = {
            "template_key": template.key,
            "template_version": template.version,
            "metric_key": template.metric_key,
            "scope": {"type": request.scope_type, "ref": resolved.scope_ref},
            "unit": template.output_unit,
            "result_type": "timeseries",
            "range": range_payload,
            "source_type": "prometheus",
        }

        if integration is None or integration[2] != "configured":
            envelope = {
                **base_envelope,
                "series": [],
                "data_state": "not_configured",
                "cache_state": "none",
                "partial": False,
                "warnings": [],
                "as_of": datetime.now(UTC).isoformat(),
            }
            self._record(template, "not_configured", "none", started, 0)
            return {**envelope, "correlation_id": correlation_id}

        # Align the window to the effective step so close-together requests
        # share cache entries deterministically: `from` floors, `to` CEILS —
        # flooring the end would drop up to one step of the freshest samples
        # (visible as a false "empty" against a recently started provider).
        step = effective.effective_step_seconds
        aligned_from = (effective.from_ts // step) * step
        aligned_to = -(-effective.to_ts // step) * step
        # Cache identity follows the integration's CONFIGURATION (id +
        # hashed config_ref) — reconfiguration invalidates cache entries,
        # observation-projection version bumps do not. The ref itself is
        # hashed away and never stored.
        integration_identity = hashlib.sha256(
            f"{integration[0]}:{integration[1]}".encode()
        ).hexdigest()
        # A query whose end sits within ~2 steps of now is a moving
        # dashboard window; anything older is a historical absolute window.
        near_now = int(time.time()) - effective.to_ts <= max(2 * step, 120)
        keys = build_cache_keys(
            registry_hash=self._registry.content_hash,
            template_key=template.key,
            template_version=template.version,
            integration_identity=integration_identity,
            scope_type=request.scope_type,
            scope_id=str(request.scope_id),
            matcher_set=compiled.matcher_set,
            parameters=request.parameters,
            from_ts=aligned_from,
            to_ts=aligned_to,
            effective_step_seconds=step,
            historical=not near_now,
        )

        fresh = await self._cache.get(keys.fresh)
        if fresh is not None:
            self._record(template, "ok", "fresh_hit", started, _point_count(fresh))
            return {**fresh, "cache_state": "fresh_hit", "correlation_id": correlation_id}

        connector = self._adapter.resolve_connector(str(integration[1]))
        if connector is None:
            # Configured integration whose connector the server cannot
            # resolve: treat as provider unavailability (last-good may serve).
            return await self._serve_stale_or_unavailable(
                template,
                keys.last_good,
                started,
                correlation_id,
                "connector_unresolved",
                current_range=range_payload,
            )

        lease_keys = [
            f"telemetry:lease:principal:{principal.identity_id}",
            f"telemetry:lease:target:{resolved.provider_scope_id}",
        ]
        try:
            held = await self._leases.acquire(lease_keys, int(time.time() * 1000))
        except ConcurrencyRejectedError as error:
            self._metrics.record_rejection("concurrency")
            raise HTTPException(
                status_code=429, detail="telemetry_concurrency_exhausted"
            ) from error
        except BudgetUnavailableError as error:
            # Redis down: budgets are never bypassed — refuse, typed retryable.
            self._metrics.record_rejection("budget_unavailable")
            raise HTTPException(status_code=503, detail="telemetry_budget_unavailable") from error

        try:
            raw = await self._adapter.query_range(
                connector,
                compiled.query,
                start=aligned_from,
                end=aligned_to,
                step_seconds=step,
                timeout_seconds=min(
                    template.timeout_seconds, self._settings.telemetry_max_timeout_seconds
                ),
                correlation_id=correlation_id,
            )
            series, partial, warnings = normalize_matrix(template, raw)
        except ProviderUnavailableError as error:
            await record_provider_observation(
                self._engine, str(integration[0]), outcome="failure", error_code=error.code
            )
            self._metrics.record_rejection("provider_unavailable")
            return await self._serve_stale_or_unavailable(
                template,
                keys.last_good,
                started,
                correlation_id,
                error.code,
                current_range=range_payload,
            )
        except (ProviderContractError, ConnectorRefusedError) as error:
            await record_provider_observation(
                self._engine, str(integration[0]), outcome="failure", error_code=error.code
            )
            self._metrics.record_rejection("provider_contract")
            return await self._serve_stale_or_unavailable(
                template,
                keys.last_good,
                started,
                correlation_id,
                error.code,
                contract_error=True,
                current_range=range_payload,
            )
        finally:
            await self._leases.release(held)

        await record_provider_observation(self._engine, str(integration[0]), outcome="success")
        data_state = "empty" if not series else "ok"
        envelope = {
            **base_envelope,
            "series": series,
            "data_state": data_state,
            "cache_state": "miss",
            "partial": partial,
            "warnings": warnings,
            "as_of": datetime.now(UTC).isoformat(),
        }
        fresh_ttl = template.fresh_ttl_seconds
        if self._settings.telemetry_fresh_ttl_override_seconds and self._settings.env in (
            "local",
            "test",
        ):
            fresh_ttl = self._settings.telemetry_fresh_ttl_override_seconds
        cacheable = {k: v for k, v in envelope.items() if k != "cache_state"}
        await self._cache.put(
            keys,
            cacheable,
            fresh_ttl=fresh_ttl,
            stale_ttl=template.stale_ttl_seconds,
        )
        self._record(template, data_state, "miss", started, _point_count(envelope))
        return {**envelope, "correlation_id": correlation_id}

    async def _serve_stale_or_unavailable(
        self,
        template: QueryTemplate,
        last_good_key: str,
        started: float,
        correlation_id: str,
        code: str,
        *,
        contract_error: bool = False,
        current_range: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_good = await self._cache.get(last_good_key)
        if last_good is not None:
            # Honest staleness: `range` is what the caller REQUESTED now,
            # `data_range` is what the cached payload actually covers, and
            # as_of stays the payload's true production time — old data is
            # never presented as if it covered the new window.
            stale = {
                **last_good,
                "range": current_range or last_good.get("range"),
                "data_range": last_good.get("range"),
                "data_state": "stale",
                "cache_state": "stale",
                "warnings": sorted({*last_good.get("warnings", []), "provider_unavailable"}),
            }
            self._record(template, "stale", "stale", started, _point_count(stale))
            return {**stale, "correlation_id": correlation_id}
        self._record(template, "unavailable", "none", started, 0)
        if contract_error:
            raise HTTPException(status_code=502, detail="telemetry_provider_contract")
        raise HTTPException(status_code=503, detail="telemetry_provider_unavailable")

    def _record(
        self,
        template: QueryTemplate,
        outcome: str,
        cache_state: str,
        started: float,
        points: int,
    ) -> None:
        self._metrics.record_query(
            template_key=template.key,
            provider_type=template.provider_type,
            outcome=outcome,
            cache_state=cache_state,
            duration_seconds=time.monotonic() - started,
            returned_points=points,
        )

    async def _lookup_integration(self, connection: Any, provider_scope_id: uuid.UUID) -> Any:
        return (
            await connection.execute(
                text(
                    """
                    SELECT id, config_ref, configuration_state, version
                    FROM integrations
                    WHERE scope_id = :scope AND integration_type = 'prometheus'
                      AND lifecycle = 'active'
                    """
                ),
                {"scope": provider_scope_id},
            )
        ).first()

    async def _resolve_scope(
        self, connection: Any, scope_type: str, scope_id: uuid.UUID
    ) -> ResolvedScope | None:
        if scope_type == "environment":
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT e.scope_id, e.environment_key, p.project_key, p.scope_id
                        FROM environments e JOIN projects p ON p.id = e.project_id
                        WHERE e.id = :id AND e.lifecycle = 'active'
                        """
                    ),
                    {"id": scope_id},
                )
            ).first()
            if row is None:
                return None
            return ResolvedScope(
                scope_id=row[0],
                scope_ref=f"{row[2]}/{row[1]}",
                source_values={"project_key": str(row[2]), "environment_key": str(row[1])},
                provider_scope_id=row[3],
            )
        if scope_type == "service":
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT b.scope_id, s.service_key, e.environment_key,
                               p.project_key, p.scope_id
                        FROM environment_services b
                        JOIN environments e ON e.id = b.environment_id
                        JOIN projects p ON p.id = e.project_id
                        JOIN service_definitions s ON s.id = b.service_id
                        WHERE b.id = :id AND b.lifecycle = 'active'
                        """
                    ),
                    {"id": scope_id},
                )
            ).first()
            if row is None:
                return None
            return ResolvedScope(
                scope_id=row[0],
                scope_ref=f"{row[3]}/{row[2]}/{row[1]}",
                source_values={
                    "project_key": str(row[3]),
                    "environment_key": str(row[2]),
                    "service_key": str(row[1]),
                },
                provider_scope_id=row[4],
            )
        if scope_type == "cluster":
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT c.scope_id, c.cluster_ref
                        FROM clusters c WHERE c.id = :id AND c.lifecycle = 'active'
                        """
                    ),
                    {"id": scope_id},
                )
            ).first()
            if row is None:
                return None
            return ResolvedScope(
                scope_id=row[0],
                scope_ref=str(row[1]),
                source_values={"cluster_ref": str(row[1])},
                provider_scope_id=row[0],
            )
        return None


def _point_count(envelope: dict[str, Any]) -> int:
    return sum(len(series.get("points", [])) for series in envelope.get("series", []))
