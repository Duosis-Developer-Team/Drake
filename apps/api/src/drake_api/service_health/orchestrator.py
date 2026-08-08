"""Read a bound workload's signals and turn them into a health verdict.

    binding → preset + policy → curated templates → Query Broker
            → typed signals → health engine → API response

Everything the broker is asked for comes from the binding row and the
preset. No caller-supplied expression, matcher or metric key reaches it,
which is what keeps this a read of reviewed queries rather than a query
console.

The distinctions the engine depends on are drawn here, once, and they are
the whole reason this layer exists:

- **Empty is not zero.** A series with no points leaves its signal `None`,
  so the engine reports it missing instead of judging it as 0.
- **A failed query is not a failing workload.** Broker errors and provider
  outages set telemetry state, never a signal value.
- **One missing optional metric is not a failure.** A single unavailable
  template marks the result partial; it cannot make a service critical.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.rbac.service import Principal
from drake_api.service_health.cache import (
    HealthCache,
    HealthCacheKeys,
    as_last_good,
    build_health_cache_keys,
    datasource_identity,
)
from drake_api.service_health.engine import (
    ApplicationSignals,
    AvailabilitySignals,
    BindingState,
    HealthResult,
    ResourceSignals,
    StabilitySignals,
    TelemetryState,
    compute_health,
)
from drake_api.service_health.policy import DEFAULT_POLICY_KEY, HealthStatus, get_policy
from drake_api.service_health.presets import DEFAULT_PRESET_KEY, MetricPreset, get_preset
from drake_api.telemetry.broker import QueryInput, TelemetryBroker
from drake_api.telemetry.registry import TelemetryRegistry

# How many of a preset's queries may be in flight at once. The broker keeps
# its own concurrency budget; this only stops one health read from spending
# all of it.
MAX_CONCURRENT_QUERIES = 4

# The signals a preset can name. The API speaks these names; the template
# keys behind them stay server-side.
SIGNAL_FIELDS: tuple[str, ...] = (
    "desired_replicas",
    "ready_replicas",
    "restarts",
    "cpu_usage",
    "cpu_limit",
    "memory_usage",
    "memory_limit",
    "cpu_throttling",
    "request_rate",
    "error_ratio",
    "latency_p95",
    "freshness",
)

# Series labels a health response may carry. The registry already bounds
# output labels per metric; this is the second, narrower gate — a label
# added to a metric later cannot start appearing in health responses
# without someone deciding that it should.
ALLOWED_SERIES_LABELS: frozenset[str] = frozenset(
    {"namespace", "workload_name", "workload_kind", "pod", "container", "cluster"}
)
MAX_SERIES_PER_RESPONSE = 12

# Datasource-side failures. They mean Drake could not look, which is not a
# statement about the workload — and specifically not a reason to overwrite
# or discard the last verdict that was real.
_READ_FAILURE_REASONS = frozenset({"datasource_unavailable", "query_failed"})


@dataclass(frozen=True)
class SignalRead:
    """One template's answer, with why it is missing when it is."""

    template_key: str
    value: float | None = None
    newest_sample_at: datetime | None = None
    data_state: str = "unavailable"
    cache_state: str = "none"
    partial: bool = False
    failed: bool = False
    not_configured: bool = False

    @property
    def available(self) -> bool:
        return self.value is not None

    @property
    def state(self) -> str:
        """The outcomes, kept apart.

        `not_configured`, `failed`, `empty`, `stale` and `ok` mean different
        things to whoever is looking, and collapsing any pair of them would
        hide the difference between "we did not look", "we could not look"
        and "we looked and there was nothing there".
        """
        if self.not_configured:
            return "not_configured"
        if self.failed:
            return "failed"
        if self.value is None:
            return "empty"
        if self.cache_state == "stale" or self.data_state == "stale":
            return "stale"
        return "ok"

    def to_payload(self) -> dict[str, Any]:
        """What a caller sees per signal — never a query, never a label set."""
        return {
            "value": self.value,
            "state": self.state,
            "newest_sample_at": (
                self.newest_sample_at.isoformat() if self.newest_sample_at else None
            ),
            "from_cache": self.cache_state in ("fresh_hit", "stale"),
        }


def _latest_value(envelope: dict[str, Any]) -> tuple[float | None, datetime | None]:
    """The newest finite point across the returned series.

    Returns `(None, None)` for an empty result — deliberately not `0.0`. An
    absent metric and a metric that is genuinely zero are different facts,
    and only one of them means "nothing is running".
    """
    best_ts: int | None = None
    best_value: float | None = None
    for series in envelope.get("series", []):
        for point in series.get("points", []):
            if len(point) != 2:
                continue
            timestamp, value = point
            if value is None:
                continue
            if best_ts is None or timestamp > best_ts:
                best_ts = int(timestamp)
                best_value = float(value)
    if best_ts is None:
        return None, None
    return best_value, datetime.fromtimestamp(best_ts, tz=UTC)


def _as_int(value: float | None) -> int | None:
    return None if value is None else round(value)


def step_for(window_seconds: int) -> int:
    """A step that keeps a health read to a bounded number of points."""
    return max(30, window_seconds // 10)


class HealthOrchestrator:
    def __init__(
        self,
        engine: AsyncEngine,
        broker: TelemetryBroker,
        registry: TelemetryRegistry,
        cache: HealthCache,
    ) -> None:
        self._engine = engine
        self._broker = broker
        self._registry = registry
        self._cache = cache

    # ------------------------------------------------------------------
    # binding context
    # ------------------------------------------------------------------

    async def load_context(self, binding_id: uuid.UUID) -> dict[str, Any] | None:
        """Everything a verdict depends on, in one read.

        Includes the datasource's configuration state, so that reconfiguring
        the datasource changes the cache key. The config ref is read only to
        be hashed — it is never returned and never stored.

        Visibility is NOT checked here; callers must have already resolved
        the binding through the scope-filtered repository.
        """
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        SELECT b.id, b.lifecycle, b.resolved_resource_uid, b.resolved_at,
                               b.preset_key, b.health_policy_key, b.revision,
                               b.namespace, b.workload_kind, b.workload_name,
                               c.cluster_ref, c.id,
                               p.project_key, e.environment_key, sd.service_key,
                               b.environment_service_id,
                               i.id, i.config_ref, i.configuration_state
                        FROM service_workload_bindings b
                        JOIN clusters c ON c.id = b.cluster_id
                        JOIN projects p ON p.id = b.project_id
                        JOIN environments e ON e.id = b.environment_id
                        JOIN service_definitions sd ON sd.id = b.service_id
                        LEFT JOIN integrations i
                               ON i.scope_id = p.scope_id
                              AND i.integration_type = 'prometheus'
                              AND i.lifecycle = 'active'
                        WHERE b.id = :id
                        """
                    ),
                    {"id": binding_id},
                )
            ).first()
        if row is None:
            return None
        return {
            "id": str(row[0]),
            "lifecycle": row[1],
            "resolved_resource_uid": row[2],
            "resolved": row[2] is not None,
            "resolved_at": row[3].isoformat() if row[3] else None,
            "preset_key": row[4],
            "health_policy_key": row[5],
            "revision": row[6],
            "namespace": row[7],
            "workload_kind": row[8],
            "workload_name": row[9],
            "cluster_ref": row[10],
            "cluster_id": str(row[11]),
            "project_key": row[12],
            "environment_key": row[13],
            "service_key": row[14],
            "environment_service_id": str(row[15]),
            "datasource_configured": row[18] == "configured",
            # Hashed on the way in; the ref itself stops here.
            "datasource_identity": datasource_identity(
                str(row[16]) if row[16] else None, row[17], str(row[18] or "")
            ),
        }

    # ------------------------------------------------------------------
    # one curated query
    # ------------------------------------------------------------------

    async def _read_signal(
        self,
        principal: Principal,
        binding_id: uuid.UUID,
        template_key: str,
        from_dt: datetime,
        to_dt: datetime,
        step_seconds: int,
        semaphore: asyncio.Semaphore,
    ) -> SignalRead:
        request = QueryInput(
            template_key=template_key,
            # The binding id IS the scope. Every matcher value is resolved
            # server-side from that row.
            scope_type="workload",
            scope_id=binding_id,
            from_dt=from_dt,
            to_dt=to_dt,
            step_seconds=step_seconds,
            parameters={},
        )
        async with semaphore:
            try:
                envelope = await self._broker.query(principal, request)
            except asyncio.CancelledError:
                # The caller went away. Propagate, so no query outlives the
                # request that asked for it.
                raise
            except HTTPException:
                # Every broker refusal is a Drake-side fact: an exhausted
                # budget, an unreachable provider, a template this scope
                # cannot use. None of them says anything about the workload.
                return SignalRead(template_key=template_key, failed=True)
            except Exception:  # a provider failure is "failed", not "unhealthy"
                return SignalRead(template_key=template_key, failed=True)

        data_state = str(envelope.get("data_state", "unavailable"))
        if data_state == "not_configured":
            return SignalRead(template_key=template_key, not_configured=True)

        value, newest = _latest_value(envelope)
        return SignalRead(
            template_key=template_key,
            value=value,
            newest_sample_at=newest,
            data_state=data_state,
            cache_state=str(envelope.get("cache_state", "none")),
            partial=bool(envelope.get("partial", False)),
        )

    async def read_signals(
        self,
        principal: Principal,
        binding_id: uuid.UUID,
        preset: MetricPreset,
        window_seconds: int,
        step_seconds: int,
    ) -> dict[str, SignalRead]:
        """Every template in the preset, concurrently and bounded."""
        now = datetime.now(UTC)
        from_dt = now - timedelta(seconds=window_seconds)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)
        fields = {
            name: getattr(preset, name)
            for name in SIGNAL_FIELDS
            if getattr(preset, name) is not None
        }
        tasks = {
            name: asyncio.create_task(
                self._read_signal(
                    principal, binding_id, key, from_dt, now, step_seconds, semaphore
                )
            )
            for name, key in fields.items()
        }
        try:
            results = await asyncio.gather(*tasks.values())
        except asyncio.CancelledError:
            # No orphans: a cancelled read cancels every query it started and
            # awaits them, so leases and provider streams are released before
            # this frame unwinds.
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            raise
        return dict(zip(tasks.keys(), results, strict=True))

    # ------------------------------------------------------------------
    # signals → verdict
    # ------------------------------------------------------------------

    async def compute(
        self,
        principal: Principal,
        context: dict[str, Any] | None,
        *,
        now: datetime | None = None,
        previous_status: HealthStatus | None = None,
    ) -> tuple[HealthResult, dict[str, SignalRead]]:
        """The whole read path, from a binding to a verdict."""
        moment = now or datetime.now(UTC)

        if context is None:
            return (
                compute_health(
                    now=moment,
                    policy=get_policy(DEFAULT_POLICY_KEY),
                    binding=BindingState(exists=False),
                    telemetry=TelemetryState(),
                    availability=AvailabilitySignals(),
                    stability=StabilitySignals(),
                    resources=ResourceSignals(),
                    application=ApplicationSignals(),
                    previous_status=previous_status,
                ),
                {},
            )

        policy = get_policy(context.get("health_policy_key") or DEFAULT_POLICY_KEY)
        preset = get_preset(context.get("preset_key") or DEFAULT_PRESET_KEY)
        state = BindingState(
            exists=True,
            enabled=context["lifecycle"] == "active",
            resolved=context["resolved"],
        )

        # A binding that cannot produce an answer is not queried: the verdict
        # is already determined, and a provider round-trip would only cost
        # time to reach the same place.
        if not (state.enabled and state.resolved):
            return (
                compute_health(
                    now=moment,
                    policy=policy,
                    binding=state,
                    telemetry=TelemetryState(),
                    availability=AvailabilitySignals(),
                    stability=StabilitySignals(),
                    resources=ResourceSignals(),
                    application=ApplicationSignals(),
                    binding_id=context["id"],
                    previous_status=previous_status,
                ),
                {},
            )

        signals = await self.read_signals(
            principal,
            uuid.UUID(context["id"]),
            preset,
            policy.window_seconds,
            step_seconds=step_for(policy.window_seconds),
        )

        def value(name: str) -> float | None:
            entry = signals.get(name)
            return entry.value if entry else None

        reads = list(signals.values())
        answered = [r for r in reads if not r.not_configured]
        # A datasource that reported "not configured" for everything is not
        # configured; one that failed everything is unreachable. The engine
        # turns each into its own terminal state — neither becomes a health
        # verdict about the service.
        telemetry = TelemetryState(
            datasource_configured=bool(answered),
            datasource_available=not (bool(answered) and all(r.failed for r in answered)),
            query_failed=False,
            # A single failed or empty optional query makes the answer
            # partial. It can never make it critical.
            partial=any(r.failed for r in answered) or any(r.partial for r in reads),
            newest_sample_at=max(
                (r.newest_sample_at for r in reads if r.newest_sample_at is not None),
                default=None,
            ),
            served_from_last_good=any(r.cache_state == "stale" for r in reads),
        )

        availability = AvailabilitySignals(
            desired_replicas=_as_int(value("desired_replicas")),
            ready_replicas=_as_int(value("ready_replicas")),
        )
        stability = StabilitySignals(restarts_in_window=value("restarts"))
        resources = ResourceSignals(
            cpu_cores_used=value("cpu_usage"),
            cpu_limit_cores=value("cpu_limit"),
            memory_bytes_used=value("memory_usage"),
            memory_limit_bytes=value("memory_limit"),
            cpu_throttled_ratio=value("cpu_throttling"),
        )

        # Golden signals count as present only when the preset asks for them
        # AND the application answered. A service that publishes none is
        # reported as not publishing them, not as unhealthy.
        application = ApplicationSignals(
            request_rate=value("request_rate"),
            error_ratio=value("error_ratio"),
            latency_p95_seconds=value("latency_p95"),
            metrics_present=any(
                signals[name].available
                for name in ("request_rate", "error_ratio", "latency_p95")
                if name in signals
            ),
        )

        result = compute_health(
            now=moment,
            policy=policy,
            binding=state,
            telemetry=telemetry,
            availability=availability,
            stability=stability,
            resources=resources,
            application=application,
            binding_id=context["id"],
            previous_status=previous_status,
        )
        return result, signals

    # ------------------------------------------------------------------
    # cached read path
    # ------------------------------------------------------------------

    def cache_keys(self, context: dict[str, Any]) -> HealthCacheKeys:
        policy = get_policy(context.get("health_policy_key") or DEFAULT_POLICY_KEY)
        return build_health_cache_keys(
            registry_hash=self._registry.content_hash,
            binding_id=context["id"],
            revision=context["revision"],
            resolved_resource_uid=context["resolved_resource_uid"],
            preset_key=context["preset_key"],
            policy_key=context["health_policy_key"],
            datasource_identity=context["datasource_identity"],
            project_key=context["project_key"],
            environment_key=context["environment_key"],
            service_key=context["service_key"],
            window_seconds=policy.window_seconds,
            step_seconds=step_for(policy.window_seconds),
        )

    async def current_health(
        self,
        principal: Principal,
        context: dict[str, Any] | None,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """A verdict: from cache when it is current, honest when it is not."""
        now = datetime.now(UTC)
        if context is None:
            result, _ = await self.compute(principal, None, now=now)
            return {**result.to_payload(), "served_from_last_good": False, "cached": False}

        keys = self.cache_keys(context)
        if not refresh:
            cached = await self._cache.get(keys.fresh)
            if cached is not None:
                return {**cached, "cached": True}

        result, signals = await self.compute(principal, context, now=now)
        payload: dict[str, Any] = {
            **result.to_payload(),
            "served_from_last_good": False,
            "cached": False,
            "signals": {name: read.to_payload() for name, read in signals.items()},
        }

        # A read that failed for datasource reasons is not stored, and — the
        # part that matters — does not delete what is stored. The last good
        # answer survives the outage that would otherwise erase it.
        if result.status is HealthStatus.UNKNOWN and any(
            reason in _READ_FAILURE_REASONS for reason in payload["reasons"]
        ):
            last_good = await self._cache.get(keys.last_good)
            if last_good is not None:
                return {**as_last_good(last_good, now=now), "cached": True}
            return payload

        # A verdict already built on stale telemetry is cached, but not
        # promoted to the fallback: otherwise each outage would inherit the
        # previous one's staleness while looking freshly computed.
        await self._cache.put(
            keys, payload, last_good="telemetry_stale" not in payload["reasons"]
        )
        return payload

    async def invalidate(self, context: dict[str, Any]) -> None:
        """Drop the fresh verdict for a binding's *current* identity.

        Mutations already make the previous entry unreachable by changing the
        key — revision and resolved uid are part of it. This exists for what
        identity alone does not cover: an operator who has just re-resolved
        or reconfigured should see the effect now, not after the TTL.
        """
        keys = self.cache_keys(context)
        await self._cache.drop(keys.fresh)

    # ------------------------------------------------------------------
    # bounded time-series
    # ------------------------------------------------------------------

    async def read_series(
        self,
        principal: Principal,
        context: dict[str, Any],
        signal: str,
        range_seconds: int,
        step_seconds: int,
    ) -> dict[str, Any]:
        """One signal over time, bounded on every axis.

        The caller names a *signal*, not a template and not a metric. The
        preset decides which curated template answers for it, so the set of
        readable queries is exactly the set someone reviewed.
        """
        preset = preset_of(context)
        template_key = getattr(preset, signal, None) if signal in SIGNAL_FIELDS else None
        if template_key is None:
            raise HTTPException(status_code=404, detail="not found")

        now = datetime.now(UTC)
        envelope = await self._broker.query(
            principal,
            QueryInput(
                template_key=template_key,
                scope_type="workload",
                scope_id=uuid.UUID(context["id"]),
                from_dt=now - timedelta(seconds=range_seconds),
                to_dt=now,
                step_seconds=step_seconds,
                parameters={},
            ),
        )

        # Second gate on labels, and a hard cap on series count: a workload
        # with hundreds of pods must not turn one chart request into an
        # unbounded response.
        raw_series = envelope.get("series", [])
        series = [
            {
                "labels": {
                    key: value
                    for key, value in entry.get("labels", {}).items()
                    if key in ALLOWED_SERIES_LABELS
                },
                "points": entry.get("points", []),
            }
            for entry in raw_series[:MAX_SERIES_PER_RESPONSE]
        ]
        truncated = len(raw_series) > MAX_SERIES_PER_RESPONSE

        return {
            "signal": signal,
            "unit": envelope.get("unit"),
            "series": series,
            "series_truncated": truncated,
            "range": envelope.get("range"),
            "data_range": envelope.get("data_range"),
            "data_state": envelope.get("data_state"),
            "cache_state": envelope.get("cache_state"),
            "partial": bool(envelope.get("partial")) or truncated,
            "warnings": envelope.get("warnings", []),
            "as_of": envelope.get("as_of"),
        }


def preset_of(context: dict[str, Any]) -> MetricPreset:
    return get_preset(context.get("preset_key") or DEFAULT_PRESET_KEY)


def readable_signals(context: dict[str, Any]) -> list[str]:
    """Which signals this binding's preset can chart."""
    preset = preset_of(context)
    return [name for name in SIGNAL_FIELDS if getattr(preset, name) is not None]


def summarize_signals(signals: dict[str, SignalRead], preset: MetricPreset) -> dict[str, Any]:
    """Per-signal values and freshness for the metric summary view.

    An absent value stays `null`. Substituting `0` here would be the same
    mistake the engine refuses to make, one layer further out — and it is
    the one that makes a dashboard report a service as idle when it is
    actually unobserved.
    """

    def entry(name: str) -> dict[str, Any]:
        read = signals.get(name)
        if read is None:
            # `not_collected` means the preset does not read this signal at
            # all — distinct from reading it and getting nothing back.
            return {
                "value": None,
                "state": "not_collected" if getattr(preset, name) is None else "empty",
                "newest_sample_at": None,
                "from_cache": False,
            }
        return read.to_payload()

    def ratio(used: str, limit: str) -> float | None:
        numerator = signals.get(used)
        denominator = signals.get(limit)
        if numerator is None or denominator is None:
            return None
        if numerator.value is None or denominator.value is None or denominator.value == 0:
            return None
        return numerator.value / denominator.value

    return {
        "availability": {
            "desired_replicas": entry("desired_replicas"),
            "ready_replicas": entry("ready_replicas"),
        },
        "stability": {"restarts": entry("restarts")},
        "resources": {
            "cpu_usage": entry("cpu_usage"),
            "cpu_limit": entry("cpu_limit"),
            "cpu_utilization": ratio("cpu_usage", "cpu_limit"),
            "memory_usage": entry("memory_usage"),
            "memory_limit": entry("memory_limit"),
            "memory_utilization": ratio("memory_usage", "memory_limit"),
            "cpu_throttling": entry("cpu_throttling"),
        },
        "application": {
            "request_rate": entry("request_rate"),
            "error_ratio": entry("error_ratio"),
            "latency_p95": entry("latency_p95"),
        },
        "freshness": entry("freshness"),
    }
