"""Measuring an SLO: curated queries in, one bounded evaluation row out.

Every SLI read goes through the Sprint 5 Query Broker, so an SLO
measurement inherits the whole read path already in place — the curated
template registry, the range and step budgets, the concurrency leases, the
cache, the last-good behaviour and the cancellation semantics. There is no
second query path, no raw PromQL, and no expression a definition can carry:
`sli_template_key` names a REVIEWED template and nothing else.

What is persisted is a summary — good, bad, total, compliance, budget,
burn rates. Raw samples stay in Prometheus. Copying them into Postgres
would make Drake a second, worse time-series database and a second copy of
whatever a metric series happens to carry.

Two measurement methods, both stated rather than implied:

**availability** weights the error ratio by the request rate, so a minute
with ten thousand requests counts for more than a minute with three. An
unweighted mean of a ratio flatters a service that fails only under load.

**latency** counts p95 samples over a curated threshold. It is a sample
count, not a request count, which is a real limitation and is reported as
the measurement method rather than hidden behind a percentage.
"""

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.alerting.contracts import latency_threshold_seconds
from drake_api.alerting.slo import (
    BURN_PROFILES,
    DEFAULT_BURN_PROFILE,
    SloVerdict,
    WindowObservation,
    evaluate_slo,
)
from drake_api.incidents.system_actor import ensure_system_evaluator
from drake_api.rbac.service import Principal
from drake_api.telemetry.broker import QueryInput, TelemetryBroker

logger = logging.getLogger("drake_api.alerting.slo")

LEASE_KEY = "slo:evaluation:cycle"

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# The rate template that supplies the weighting for an availability SLO.
# Server-controlled, exactly like the SLI template itself.
RATE_TEMPLATE = "service.request-rate.v1"

# Windows are aligned to whole minutes so repeated evaluations land on the
# same cache entries instead of shifting by a second each cycle.
_ALIGN_SECONDS = 60


@dataclass(frozen=True)
class SloDefinitionRow:
    id: uuid.UUID
    version: int
    project_id: uuid.UUID
    environment_service_id: uuid.UUID | None
    slo_key: str
    indicator: str
    objective_ratio: float
    window_seconds: int
    sli_template_key: str
    threshold_profile_key: str | None
    burn_profile_key: str


@dataclass
class SeriesRead:
    """One broker answer, reduced to what the arithmetic needs."""

    points: list[tuple[int, float]]
    failed: bool = False
    not_configured: bool = False
    stale: bool = False
    partial: bool = False
    newest_at: datetime | None = None


def _extract(envelope: dict[str, Any]) -> SeriesRead:
    data_state = str(envelope.get("data_state", "unavailable"))
    if data_state == "not_configured":
        return SeriesRead([], not_configured=True)
    if data_state == "stale":
        return SeriesRead([], stale=True)
    if data_state == "unavailable":
        return SeriesRead([], failed=True)

    points: list[tuple[int, float]] = []
    newest: datetime | None = None
    for series in envelope.get("series", []):
        for point in series.get("points", []):
            timestamp, raw = point[0], point[1]
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            at = int(timestamp)
            points.append((at, value))
            moment = datetime.fromtimestamp(at, tz=UTC)
            if newest is None or moment > newest:
                newest = moment
    points.sort()
    return SeriesRead(
        points,
        partial=bool(envelope.get("partial", False)),
        newest_at=newest,
    )


async def _read(
    broker: TelemetryBroker,
    principal: Principal,
    *,
    template_key: str,
    scope_id: uuid.UUID,
    window_start: datetime,
    window_end: datetime,
    step_seconds: int,
) -> SeriesRead:
    """One curated query. A broker refusal is a Drake fact, not an SLI value."""
    request = QueryInput(
        template_key=template_key,
        scope_type="service",
        scope_id=scope_id,
        from_dt=window_start,
        to_dt=window_end,
        step_seconds=step_seconds,
        parameters={},
    )
    try:
        envelope = await broker.query(principal, request)
    except asyncio.CancelledError:
        raise
    except HTTPException:
        # An exhausted budget, an unreachable provider, or a template this
        # scope cannot use. None of them says anything about the service.
        return SeriesRead([], failed=True)
    except Exception:
        return SeriesRead([], failed=True)
    return _extract(envelope)


def _align(moment: datetime) -> datetime:
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - epoch % _ALIGN_SECONDS, tz=UTC)


def _step_for(window_seconds: int) -> int:
    """A step that keeps a window inside the template's point budget."""
    return max(60, window_seconds // 500 // 60 * 60 or 60)


def _availability_window(
    ratios: SeriesRead, rates: SeriesRead, step_seconds: int
) -> WindowObservation | None:
    """Request-weighted good/bad counts from two curated series.

    An unweighted mean of an error ratio treats a quiet minute and a busy
    one as equals, which flatters exactly the service that only fails under
    load. Pairing each ratio with the rate at the same timestamp is what
    makes the number mean "of the requests served, this fraction failed".
    """
    by_time = dict(rates.points)
    good = bad = 0.0
    samples = 0
    for timestamp, ratio in ratios.points:
        rate = by_time.get(timestamp)
        if rate is None or rate <= 0:
            continue
        requests = rate * step_seconds
        clamped = min(max(ratio, 0.0), 1.0)
        bad += requests * clamped
        good += requests * (1.0 - clamped)
        samples += 1
    if samples == 0:
        return None
    return WindowObservation(
        seconds=step_seconds * samples,
        good=good,
        bad=bad,
        samples=samples,
        partial=ratios.partial or rates.partial,
    )


def _latency_window(
    read: SeriesRead, threshold_seconds: float, step_seconds: int
) -> WindowObservation | None:
    """Samples over a CURATED threshold. Never a frontend-supplied one."""
    good = bad = 0.0
    for _timestamp, value in read.points:
        if value > threshold_seconds:
            bad += 1
        else:
            good += 1
    samples = int(good + bad)
    if samples == 0:
        return None
    return WindowObservation(
        seconds=step_seconds * samples,
        good=good,
        bad=bad,
        samples=samples,
        partial=read.partial,
    )


async def measure(
    broker: TelemetryBroker,
    principal: Principal,
    definition: SloDefinitionRow,
    *,
    now: datetime,
) -> tuple[SloVerdict, datetime, datetime]:
    """Read every window this definition needs and produce one verdict."""
    window_end = _align(now)
    window_start = window_end - timedelta(seconds=definition.window_seconds)

    if definition.environment_service_id is None:
        # A definition with nothing to measure against. `not_configured`,
        # not `healthy` — nothing is being observed.
        return (
            evaluate_slo(
                objective_ratio=definition.objective_ratio,
                window=None,
                not_configured=True,
                profile_key=definition.burn_profile_key,
                evaluated_at=now,
            ),
            window_start,
            window_end,
        )

    scope_id = definition.environment_service_id
    step = _step_for(definition.window_seconds)

    async def observe(seconds: int) -> tuple[WindowObservation | None, SeriesRead]:
        start = window_end - timedelta(seconds=seconds)
        window_step = _step_for(seconds)
        primary = await _read(
            broker,
            principal,
            template_key=definition.sli_template_key,
            scope_id=scope_id,
            window_start=start,
            window_end=window_end,
            step_seconds=window_step,
        )
        if primary.failed or primary.not_configured or primary.stale:
            return None, primary
        if definition.indicator == "latency":
            threshold = latency_threshold_seconds(definition.threshold_profile_key)
            return _latency_window(primary, threshold, window_step), primary
        rates = await _read(
            broker,
            principal,
            template_key=RATE_TEMPLATE,
            scope_id=scope_id,
            window_start=start,
            window_end=window_end,
            step_seconds=window_step,
        )
        if rates.failed:
            return None, rates
        return _availability_window(primary, rates, window_step), primary

    main_window, main_read = await observe(definition.window_seconds)
    if main_read.not_configured or main_read.failed or main_read.stale:
        verdict = evaluate_slo(
            objective_ratio=definition.objective_ratio,
            window=None,
            profile_key=definition.burn_profile_key,
            query_failed=main_read.failed,
            served_stale=main_read.stale,
            not_configured=main_read.not_configured,
            evaluated_at=now,
        )
        return verdict, window_start, window_end

    # Only the windows the profile actually uses, deduplicated — a level
    # sharing a window with another must not double the query load.
    levels = BURN_PROFILES.get(definition.burn_profile_key, BURN_PROFILES[DEFAULT_BURN_PROFILE])
    needed = sorted(
        {seconds for level in levels for seconds in (level.long_seconds, level.short_seconds)}
    )
    burn_windows: dict[int, WindowObservation] = {}
    for seconds in needed:
        observation, _read_result = await observe(seconds)
        if observation is not None:
            burn_windows[seconds] = observation

    verdict = evaluate_slo(
        objective_ratio=definition.objective_ratio,
        window=main_window,
        burn_windows=burn_windows,
        profile_key=definition.burn_profile_key,
        evaluated_at=now,
        data_as_of=main_read.newest_at,
    )
    _ = step
    return verdict, window_start, window_end


async def store_evaluation(
    engine: AsyncEngine,
    definition: SloDefinitionRow,
    verdict: SloVerdict,
    *,
    window_start: datetime,
    window_end: datetime,
    evaluated_for: datetime,
    data_as_of: datetime | None = None,
) -> uuid.UUID | None:
    """Persist one evaluation. Idempotent on (definition, period, version).

    The objective and the burn profile are written onto the ROW. Tightening
    a target tomorrow must not silently rewrite what last month's compliance
    was measured against — a historical evaluation is a record of a
    judgement, not a view over current configuration.
    """
    import json

    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO slo_evaluations
                        (slo_definition_id, definition_version, objective_ratio,
                         burn_profile_key, evaluated_for, window_start, window_end,
                         good_observations, bad_observations, total_observations,
                         sample_count, compliance_ratio, error_budget_total,
                         error_budget_consumed, error_budget_remaining, burn_rates,
                         status, data_quality, freshness_seconds, error_code,
                         source_event_at)
                    VALUES (:definition, :version, :objective, :profile, :evaluated_for,
                            :start, :end, :good, :bad, :total, :samples, :compliance,
                            :budget_total, :budget_consumed, :budget_remaining,
                            CAST(:burn AS jsonb), :status, :quality, :freshness, :error,
                            :source_at)
                    ON CONFLICT (slo_definition_id, evaluated_for, definition_version)
                    DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "definition": definition.id,
                    "version": definition.version,
                    "objective": definition.objective_ratio,
                    "profile": definition.burn_profile_key,
                    "evaluated_for": evaluated_for,
                    "start": window_start,
                    "end": window_end,
                    "good": verdict.good,
                    "bad": verdict.bad,
                    "total": verdict.total,
                    "samples": verdict.sample_count,
                    "compliance": verdict.compliance_ratio,
                    "budget_total": verdict.error_budget_total,
                    "budget_consumed": verdict.error_budget_consumed,
                    "budget_remaining": verdict.error_budget_remaining,
                    "burn": json.dumps(verdict.burn_payload()),
                    "status": verdict.status,
                    "quality": verdict.data_quality,
                    "freshness": verdict.freshness_seconds,
                    "error": verdict.error_code,
                    "source_at": data_as_of,
                },
            )
        ).first()
    return None if row is None else uuid.UUID(str(row[0]))


async def load_definitions(
    engine: AsyncEngine, *, limit: int = 50, definition_id: uuid.UUID | None = None
) -> list[SloDefinitionRow]:
    clause = "AND d.id = :definition" if definition_id is not None else ""
    params: dict[str, Any] = {"limit": limit}
    if definition_id is not None:
        params["definition"] = definition_id
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    f"""
                    SELECT d.id, d.version, d.project_id, d.environment_service_id,
                           d.slo_key, d.indicator, d.objective_ratio, d.window_seconds,
                           d.sli_template_key, d.threshold_profile_key, d.burn_profile_key
                    FROM slo_definitions d
                    WHERE d.enabled {clause}
                    ORDER BY d.created_at
                    LIMIT :limit
                    """  # noqa: S608 - the only variable fragment is fixed text
                ),
                params,
            )
        ).all()
    return [
        SloDefinitionRow(
            id=uuid.UUID(str(row[0])),
            version=int(row[1]),
            project_id=uuid.UUID(str(row[2])),
            environment_service_id=(uuid.UUID(str(row[3])) if row[3] is not None else None),
            slo_key=str(row[4]),
            indicator=str(row[5]),
            objective_ratio=float(row[6]),
            window_seconds=int(row[7]),
            sli_template_key=str(row[8]),
            threshold_profile_key=row[9],
            burn_profile_key=str(row[10]),
        )
        for row in rows
    ]


async def evaluate_definition(
    engine: AsyncEngine,
    broker: TelemetryBroker,
    principal: Principal,
    definition: SloDefinitionRow,
    *,
    now: datetime | None = None,
) -> SloVerdict:
    moment = now or datetime.now(UTC)
    verdict, window_start, window_end = await measure(broker, principal, definition, now=moment)
    await store_evaluation(
        engine,
        definition,
        verdict,
        window_start=window_start,
        window_end=window_end,
        evaluated_for=_align(moment),
    )
    return verdict


async def run_cycle(
    engine: AsyncEngine,
    broker: TelemetryBroker,
    redis: aioredis.Redis,
    *,
    batch_size: int = 25,
    lease_seconds: int = 300,
    principal: Principal | None = None,
) -> int:
    """One bounded sweep, guarded by a distributed lease.

    Two replicas both evaluating would double the query load against
    someone's Prometheus for no benefit, so a replica that does not hold the
    lease does nothing and tries again next tick.
    """
    token = uuid.uuid4().hex
    try:
        acquired = await redis.set(LEASE_KEY, token, nx=True, ex=lease_seconds)
    except Exception:
        logger.warning("slo evaluator: lease unavailable, skipping cycle")
        return 0
    if not acquired:
        return 0

    actor = principal or await ensure_system_evaluator(engine)
    evaluated = 0
    try:
        for definition in await load_definitions(engine, limit=batch_size):
            try:
                await evaluate_definition(engine, broker, actor, definition)
                evaluated += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                # One SLO's failure is one SLO's failure. The message
                # carries no query, no URL and no credential.
                logger.warning("slo evaluator: evaluation failed for one definition")
    finally:
        with contextlib.suppress(Exception):
            await redis.eval(_RELEASE, 1, LEASE_KEY, token)
    return evaluated


class SloEvaluator:
    """Lifespan-owned loop. Started only when the feature flag is on."""

    def __init__(
        self,
        engine: AsyncEngine,
        broker: TelemetryBroker,
        redis: aioredis.Redis,
        *,
        interval_seconds: float = 300.0,
        batch_size: int = 25,
        lease_seconds: int = 300,
    ) -> None:
        self._engine = engine
        self._broker = broker
        self._redis = redis
        # A floor, not a suggestion: a misconfigured interval cannot turn
        # this into a load generator pointed at someone's Prometheus.
        self._interval = max(60.0, interval_seconds)
        self._batch = max(1, min(batch_size, 200))
        self._lease = max(60, lease_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="slo-evaluation")

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
                await run_cycle(
                    self._engine,
                    self._broker,
                    self._redis,
                    batch_size=self._batch,
                    lease_seconds=self._lease,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("slo evaluator: cycle failed")
            await asyncio.sleep(self._interval)


async def ensure_definition(
    engine: AsyncEngine,
    *,
    project_key: str,
    slo_key: str,
    display_name: str,
    indicator: str,
    objective_ratio: float,
    window_seconds: int,
    environment_key: str | None = None,
    service_key: str | None = None,
    threshold_profile_key: str | None = None,
    burn_profile_key: str | None = None,
) -> uuid.UUID | None:
    """Create or update one SLO definition from catalog identifiers.

    The catalog stays the source of truth for what exists: a definition
    naming a project, environment or service that is not in it is skipped
    rather than invented. There is no API endpoint that creates an SLO,
    because an objective is a promise the business made, not a field a user
    types into a form.

    The SLI template is chosen HERE from the reviewed contract, by
    indicator. A definition cannot carry an expression, and there is no
    parameter through which one could arrive.
    """
    from drake_api.alerting.contracts import (
        default_burn_profile,
        indicator_template,
    )

    template_key = indicator_template(indicator)
    if not template_key:
        raise ValueError("unsupported SLO indicator")

    async with engine.begin() as connection:
        project_id = (
            await connection.execute(
                text("SELECT id FROM projects WHERE project_key = :key"), {"key": project_key}
            )
        ).scalar_one_or_none()
        if project_id is None:
            return None

        environment_id = None
        if environment_key:
            environment_id = (
                await connection.execute(
                    text(
                        "SELECT id FROM environments WHERE project_id = :project "
                        "AND environment_key = :key"
                    ),
                    {"project": project_id, "key": environment_key},
                )
            ).scalar_one_or_none()
            if environment_id is None:
                return None

        service_id = environment_service_id = None
        if service_key:
            service_id = (
                await connection.execute(
                    text(
                        "SELECT id FROM service_definitions WHERE project_id = :project "
                        "AND service_key = :key"
                    ),
                    {"project": project_id, "key": service_key},
                )
            ).scalar_one_or_none()
            if service_id is None:
                return None
            environment_service_id = (
                await connection.execute(
                    text(
                        "SELECT id FROM environment_services WHERE project_id = :project "
                        "AND service_id = :service "
                        "AND (CAST(:environment AS uuid) IS NULL OR environment_id = :environment)"
                    ),
                    {
                        "project": project_id,
                        "service": service_id,
                        "environment": environment_id,
                    },
                )
            ).scalar_one_or_none()

        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO slo_definitions
                        (project_id, environment_id, service_id, environment_service_id,
                         slo_key, display_name, indicator, objective_ratio, window_seconds,
                         sli_template_key, threshold_profile_key, burn_profile_key)
                    VALUES (:project, :environment, :service, :es, :key, :name, :indicator,
                            :objective, :window, :template, :threshold, :burn)
                    ON CONFLICT (project_id, slo_key) DO UPDATE
                    SET display_name = EXCLUDED.display_name,
                        environment_id = EXCLUDED.environment_id,
                        service_id = EXCLUDED.service_id,
                        environment_service_id = EXCLUDED.environment_service_id,
                        indicator = EXCLUDED.indicator,
                        objective_ratio = EXCLUDED.objective_ratio,
                        window_seconds = EXCLUDED.window_seconds,
                        sli_template_key = EXCLUDED.sli_template_key,
                        threshold_profile_key = EXCLUDED.threshold_profile_key,
                        burn_profile_key = EXCLUDED.burn_profile_key,
                        -- A changed objective is a NEW version. Historical
                        -- evaluations keep the version and objective they
                        -- were judged against; nothing is rewritten.
                        version = CASE
                            WHEN slo_definitions.objective_ratio <> EXCLUDED.objective_ratio
                              OR slo_definitions.window_seconds <> EXCLUDED.window_seconds
                            THEN slo_definitions.version + 1
                            ELSE slo_definitions.version
                        END,
                        catalog_revision = slo_definitions.catalog_revision + 1,
                        updated_at = now()
                    RETURNING id
                    """
                ),
                {
                    "project": project_id,
                    "environment": environment_id,
                    "service": service_id,
                    "es": environment_service_id,
                    "key": slo_key,
                    "name": display_name,
                    "indicator": indicator,
                    "objective": objective_ratio,
                    "window": window_seconds,
                    "template": template_key,
                    "threshold": threshold_profile_key,
                    "burn": burn_profile_key or default_burn_profile(),
                },
            )
        ).first()
    return None if row is None else uuid.UUID(str(row[0]))
