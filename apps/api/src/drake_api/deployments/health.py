"""How health looked either side of a rollout.

Temporal correlation, and nothing more. Drake reports what the same
curated signals said before and after a deployment; it does not claim the
deployment caused the difference. Those are different statements, and only
one of them is supportable from two time windows.

Signals come from the Sprint 5 broker and the curated registry — the same
templates the health engine reads — so there is no second query path and
no PromQL composed here.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.deployments.model import ComparisonVerdict
from drake_api.rbac.service import Principal
from drake_api.service_health.orchestrator import _latest_value
from drake_api.telemetry.broker import QueryInput, TelemetryBroker

logger = logging.getLogger("drake_api.deployments.health")

# Bounded windows either side of the rollout. Long enough to average out a
# quiet minute, short enough that the "after" window is about this release
# and not the next one.
WINDOW = timedelta(minutes=30)
# The gap skipped immediately after the rollout starts: during a rolling
# update both versions serve, so those samples describe neither.
SETTLE = timedelta(minutes=2)
STEP_SECONDS = 60

# Which curated templates feed the comparison, and which direction is good.
# `lower_is_better` is what turns a delta into a verdict without anyone
# hard-coding a threshold per service.
SIGNALS: tuple[tuple[str, str, bool], ...] = (
    ("request_rate", "workload.request-rate.v1", False),
    ("error_ratio", "workload.error-ratio.v1", True),
    ("latency_p95", "workload.latency-p95.v1", True),
    ("restarts", "workload.restarts-delta.v1", True),
    ("availability", "workload.telemetry-freshness.v1", False),
)

# How much a signal must move before it counts as movement rather than
# noise. A 3% wobble in latency between two half-hour windows is weather.
RELATIVE_TOLERANCE = 0.20


@dataclass(frozen=True)
class SignalComparison:
    before: float | None
    after: float | None
    lower_is_better: bool

    @property
    def measured(self) -> bool:
        return self.before is not None and self.after is not None

    @property
    def direction(self) -> str:
        """`improved`, `regressed` or `stable` for this one signal."""
        if not self.measured:
            return "unknown"
        assert self.before is not None and self.after is not None
        baseline = abs(self.before)
        delta = self.after - self.before
        if baseline == 0:
            # From zero, any movement is real but its RATIO is undefined —
            # so judge on the absolute change instead of dividing by zero.
            if delta == 0:
                return "stable"
            worse = delta > 0 if self.lower_is_better else delta < 0
            return "regressed" if worse else "improved"
        if abs(delta) / baseline < RELATIVE_TOLERANCE:
            return "stable"
        worse = delta > 0 if self.lower_is_better else delta < 0
        return "regressed" if worse else "improved"

    def to_payload(self) -> dict[str, Any]:
        return {
            "before": self.before,
            "after": self.after,
            "delta": (
                None if not self.measured else (self.after or 0.0) - (self.before or 0.0)
            ),
            "direction": self.direction,
            "lower_is_better": self.lower_is_better,
        }


@dataclass
class Comparison:
    verdict: ComparisonVerdict
    signals: dict[str, SignalComparison]
    missing: list[str]
    before_from: datetime
    before_to: datetime
    after_from: datetime
    after_to: datetime
    incident_count: int = 0


def decide(signals: dict[str, SignalComparison], incident_count: int) -> ComparisonVerdict:
    """Combine per-signal directions into one honest verdict.

    A regression anywhere outranks improvement elsewhere: a service that
    got faster and started erroring has not improved. And with nothing
    measured, the answer is `insufficient_data` — never `stable`, which
    would read as "we checked and it was fine".
    """
    directions = [signal.direction for signal in signals.values() if signal.measured]
    if not directions and incident_count == 0:
        return ComparisonVerdict.INSUFFICIENT_DATA
    if incident_count > 0 or "regressed" in directions:
        return ComparisonVerdict.REGRESSED
    if "improved" in directions:
        return ComparisonVerdict.IMPROVED
    return ComparisonVerdict.STABLE


async def _read(
    broker: TelemetryBroker,
    principal: Principal,
    binding_id: uuid.UUID,
    template_key: str,
    from_dt: datetime,
    to_dt: datetime,
) -> float | None:
    """One curated query over one window. Failures read as "not measured"."""
    try:
        envelope = await broker.query(
            principal,
            QueryInput(
                template_key=template_key,
                scope_type="workload",
                scope_id=binding_id,
                from_dt=from_dt,
                to_dt=to_dt,
                step_seconds=STEP_SECONDS,
                parameters={},
            ),
        )
    except Exception:
        # A datasource problem is not a regression. It is a missing signal,
        # and the verdict degrades to `insufficient_data` rather than
        # blaming the release.
        return None
    if envelope.get("data_state") in ("not_configured", "empty"):
        return None
    value, _ = _latest_value(envelope)
    return value


async def compare_health(
    engine: AsyncEngine,
    broker: TelemetryBroker,
    principal: Principal,
    *,
    binding_id: uuid.UUID | None,
    environment_service_id: uuid.UUID | None,
    rollout_at: datetime,
) -> Comparison:
    """Read the same signals either side of a rollout."""
    before_to = rollout_at
    before_from = before_to - WINDOW
    after_from = rollout_at + SETTLE
    after_to = after_from + WINDOW

    signals: dict[str, SignalComparison] = {}
    missing: list[str] = []

    if binding_id is not None:
        for name, template_key, lower_is_better in SIGNALS:
            before = await _read(
                broker, principal, binding_id, template_key, before_from, before_to
            )
            after = await _read(
                broker, principal, binding_id, template_key, after_from, after_to
            )
            comparison = SignalComparison(before, after, lower_is_better)
            signals[name] = comparison
            if not comparison.measured:
                missing.append(name)
    else:
        # No binding means no curated workload scope to query. The
        # deployment is still recorded; only the comparison is unavailable.
        missing = [name for name, _, _ in SIGNALS]

    incident_count = 0
    if environment_service_id is not None:
        async with engine.connect() as connection:
            incident_count = int(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT count(*) FROM incidents
                            WHERE environment_service_id = :es
                              AND opened_at >= :from_dt AND opened_at <= :to_dt
                            """
                        ),
                        {"es": environment_service_id, "from_dt": after_from, "to_dt": after_to},
                    )
                ).scalar_one()
            )

    return Comparison(
        verdict=decide(signals, incident_count),
        signals=signals,
        missing=missing,
        before_from=before_from,
        before_to=before_to,
        after_from=after_from,
        after_to=after_to,
        incident_count=incident_count,
    )


async def store_comparison(
    engine: AsyncEngine, revision_id: uuid.UUID, comparison: Comparison
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO deployment_health_comparisons
                    (deployment_revision_id, verdict, before_from, before_to,
                     after_from, after_to, signals, incident_count, missing_signals,
                     computed_at)
                VALUES (:id, :verdict, :before_from, :before_to, :after_from, :after_to,
                        CAST(:signals AS jsonb), :incidents, CAST(:missing AS jsonb), now())
                ON CONFLICT (deployment_revision_id) DO UPDATE
                SET verdict = EXCLUDED.verdict,
                    signals = EXCLUDED.signals,
                    incident_count = EXCLUDED.incident_count,
                    missing_signals = EXCLUDED.missing_signals,
                    computed_at = now()
                """
            ),
            {
                "id": revision_id,
                "verdict": str(comparison.verdict),
                "before_from": comparison.before_from,
                "before_to": comparison.before_to,
                "after_from": comparison.after_from,
                "after_to": comparison.after_to,
                "signals": json.dumps(
                    {name: signal.to_payload() for name, signal in comparison.signals.items()}
                ),
                "incidents": comparison.incident_count,
                "missing": json.dumps(comparison.missing),
            },
        )


def utcnow() -> datetime:
    return datetime.now(UTC)
