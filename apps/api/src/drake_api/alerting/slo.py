"""Error budget and burn-rate arithmetic, in one place and in ratios.

Pure: numbers in, a verdict out. No I/O, no clock beyond the one it is
handed, and every window in UTC.

    allowed_bad_ratio     = 1 - objective_ratio
    observed_bad_ratio    = bad / total
    burn_rate             = observed_bad_ratio / allowed_bad_ratio
    error_budget_consumed = observed_bad / allowed_bad

Everything is a RATIO — 0.995, never 99.5. One representation, so nothing
in the pipeline can be off by a factor of a hundred. The UI formats a
percentage at the last moment and never computes one.

Four things this module refuses to do, each of which would be a comforting
lie:

**Report 100% for an empty window.** `total = 0` is `insufficient_data`.
A service with no traffic has not proved anything, and 0/0 = perfect is how
a dead scrape target looks healthy.

**Report healthy when the query failed.** A failure is `query_failed` and a
last-good result past its lifetime is `stale`. Neither is zero errors.

**Clamp a negative budget to zero.** A service that has burned 180% of its
budget is 80% past the objective, and rendering that as "0 left" hides how
far past.

**Divide by zero at a 100% objective.** A zero-error policy has no budget
to burn, so burn rate is undefined rather than infinite. Any error at all
exhausts it immediately, which is what the objective actually says.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class SloStatus(StrEnum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    EXHAUSTED = "exhausted"
    INSUFFICIENT_DATA = "insufficient_data"
    STALE = "stale"
    QUERY_FAILED = "query_failed"
    NOT_CONFIGURED = "not_configured"


class DataQuality(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    STALE = "stale"
    EMPTY = "empty"
    FAILED = "failed"


# The standard multi-window profile for a 30-day objective. Each level pairs
# a LONG window (is this a real trend) with a SHORT one (is it still
# happening now) — a level fires only when both are over the threshold, so a
# burst that has already stopped does not page and a slow burn that started
# an hour ago is not missed.
#
# Server-controlled: there is no request field that selects or edits a
# profile, and no frontend threshold anywhere.
@dataclass(frozen=True)
class BurnLevel:
    name: str
    factor: float
    long_seconds: int
    short_seconds: int
    severity: str


BURN_PROFILE_30D: tuple[BurnLevel, ...] = (
    BurnLevel("page_fast", 14.4, 3_600, 300, "critical"),
    BurnLevel("page_slow", 6.0, 21_600, 1_800, "critical"),
    BurnLevel("ticket_fast", 3.0, 86_400, 7_200, "warning"),
    BurnLevel("ticket_slow", 1.0, 259_200, 21_600, "warning"),
)

BURN_PROFILES: dict[str, tuple[BurnLevel, ...]] = {"standard.30d.v1": BURN_PROFILE_30D}

DEFAULT_BURN_PROFILE = "standard.30d.v1"

# How long an evaluation may stand before it stops describing now.
DEFAULT_FRESHNESS_LIMIT = timedelta(minutes=30)

# Tolerance for the exhausted boundary. `1 - 0.999` is not exactly 0.001 in
# binary floating point, so an exactly-at-budget window can land a few parts
# in 10^16 either side of 1.0.
_BUDGET_EPSILON = 1e-9


@dataclass(frozen=True)
class WindowObservation:
    """One measured window: how much was good, how much was bad.

    `total` is the denominator the objective is measured against — requests,
    probes, samples. `samples` is how many data points backed it, which is
    what tells `insufficient_data` from a genuinely quiet service.
    """

    seconds: int
    good: float
    bad: float
    samples: int
    partial: bool = False

    @property
    def total(self) -> float:
        return self.good + self.bad


@dataclass(frozen=True)
class BurnRate:
    """One level of the profile, evaluated."""

    name: str
    factor: float
    long_seconds: int
    short_seconds: int
    severity: str
    long_burn: float | None
    short_burn: float | None
    # True only when BOTH windows exceed the factor. One window alone is a
    # spike or a memory, and paging on either would flap.
    active: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "factor": self.factor,
            "long_window_seconds": self.long_seconds,
            "short_window_seconds": self.short_seconds,
            "severity": self.severity,
            "long_burn_rate": self.long_burn,
            "short_burn_rate": self.short_burn,
            "active": self.active,
        }


@dataclass
class SloVerdict:
    status: str
    data_quality: str
    compliance_ratio: float | None
    observed_bad_ratio: float | None
    error_budget_total: float | None
    error_budget_consumed: float | None
    error_budget_remaining: float | None
    good: float | None
    bad: float | None
    total: float | None
    sample_count: int
    burn_rates: list[BurnRate]
    freshness_seconds: int | None
    error_code: str | None = None
    # A 100% objective has no budget to spend, so a burn RATE is undefined
    # rather than infinite. Stated explicitly so the UI can say so.
    zero_error_policy: bool = False

    def burn_payload(self) -> list[dict[str, Any]]:
        return [level.to_payload() for level in self.burn_rates]

    @property
    def active_burn(self) -> BurnRate | None:
        """The most severe active level, or nothing."""
        active = [level for level in self.burn_rates if level.active]
        return max(active, key=lambda level: level.factor) if active else None


def burn_rate(bad_ratio: float | None, objective_ratio: float) -> float | None:
    """Observed bad ratio expressed in budgets-per-window.

    `None` when the objective leaves no budget: dividing by zero would
    produce `inf`, which serializes as invalid JSON and reads as a number.
    """
    allowed = 1.0 - objective_ratio
    if allowed <= 0:
        return None
    if bad_ratio is None:
        return None
    return bad_ratio / allowed


def _ratio(observation: WindowObservation | None) -> float | None:
    if observation is None or observation.total <= 0:
        return None
    return observation.bad / observation.total


def evaluate_slo(
    *,
    objective_ratio: float,
    window: WindowObservation | None,
    burn_windows: dict[int, WindowObservation] | None = None,
    profile_key: str = DEFAULT_BURN_PROFILE,
    query_failed: bool = False,
    served_stale: bool = False,
    not_configured: bool = False,
    evaluated_at: datetime | None = None,
    data_as_of: datetime | None = None,
    freshness_limit: timedelta = DEFAULT_FRESHNESS_LIMIT,
) -> SloVerdict:
    """The single place an SLO verdict is decided.

    Order matters, and it is the order of trust: could Drake measure at all,
    then was there anything to measure, then how does it compare. Reversing
    any two of those is how "we could not look" becomes "everything is
    fine".
    """
    levels = BURN_PROFILES.get(profile_key, BURN_PROFILE_30D)
    zero_error = objective_ratio >= 1.0
    freshness = (
        int((evaluated_at - data_as_of).total_seconds())
        if evaluated_at is not None and data_as_of is not None
        else None
    )

    # --- could Drake measure at all? -------------------------------------
    if not_configured:
        # No SLI mapping. Not a failure and not zero errors: nothing is
        # being measured, and the screen must say exactly that.
        return _empty(SloStatus.NOT_CONFIGURED, DataQuality.EMPTY, levels, freshness, None)
    if query_failed:
        return _empty(
            SloStatus.QUERY_FAILED, DataQuality.FAILED, levels, freshness, "sli_query_failed"
        )
    if served_stale or (freshness is not None and freshness > int(freshness_limit.total_seconds())):
        # Last-good past its lifetime. It described a window that has moved.
        return _empty(SloStatus.STALE, DataQuality.STALE, levels, freshness, None)

    # --- was there anything to measure? ----------------------------------
    if window is None or window.total <= 0 or window.samples <= 0:
        # 0/0 is not 100%. A service with no traffic has proved nothing.
        return _empty(SloStatus.INSUFFICIENT_DATA, DataQuality.EMPTY, levels, freshness, None)

    # --- the actual arithmetic -------------------------------------------
    observed_bad_ratio = window.bad / window.total
    compliance = 1.0 - observed_bad_ratio
    allowed_bad_ratio = 1.0 - objective_ratio
    allowed_bad = allowed_bad_ratio * window.total
    consumed = None if allowed_bad <= 0 else window.bad / allowed_bad
    remaining = None if consumed is None else 1.0 - consumed

    burn_rates = _evaluate_burn(levels, burn_windows or {}, objective_ratio)
    status = _status(
        zero_error=zero_error,
        bad=window.bad,
        consumed=consumed,
        burn_rates=burn_rates,
    )

    return SloVerdict(
        status=str(status),
        data_quality=str(DataQuality.PARTIAL if window.partial else DataQuality.OK),
        compliance_ratio=compliance,
        observed_bad_ratio=observed_bad_ratio,
        error_budget_total=allowed_bad if allowed_bad > 0 else 0.0,
        error_budget_consumed=consumed,
        # Deliberately NOT clamped at zero — see the module docstring.
        error_budget_remaining=remaining,
        good=window.good,
        bad=window.bad,
        total=window.total,
        sample_count=window.samples,
        burn_rates=burn_rates,
        freshness_seconds=freshness,
        zero_error_policy=zero_error,
    )


def _evaluate_burn(
    levels: tuple[BurnLevel, ...],
    windows: dict[int, WindowObservation],
    objective_ratio: float,
) -> list[BurnRate]:
    """A level is active only when BOTH of its windows exceed the factor."""
    results: list[BurnRate] = []
    for level in levels:
        long_ratio = _ratio(windows.get(level.long_seconds))
        short_ratio = _ratio(windows.get(level.short_seconds))
        long_burn = burn_rate(long_ratio, objective_ratio)
        short_burn = burn_rate(short_ratio, objective_ratio)
        active = (
            long_burn is not None
            and short_burn is not None
            and long_burn >= level.factor
            and short_burn >= level.factor
        )
        results.append(
            BurnRate(
                name=level.name,
                factor=level.factor,
                long_seconds=level.long_seconds,
                short_seconds=level.short_seconds,
                severity=level.severity,
                long_burn=long_burn,
                short_burn=short_burn,
                active=active,
            )
        )
    return results


def _status(
    *,
    zero_error: bool,
    bad: float,
    consumed: float | None,
    burn_rates: list[BurnRate],
) -> SloStatus:
    if zero_error:
        # No budget exists, so any error at all is already past the
        # objective. There is nothing to be "warning" about.
        return SloStatus.EXHAUSTED if bad > 0 else SloStatus.HEALTHY
    # A hair under 1.0 from floating-point arithmetic on a ratio is still a
    # spent budget. Reporting `warning` there would mean an objective of
    # exactly 99.9% never reads as exhausted at exactly 99.9%.
    if consumed is not None and consumed >= 1.0 - _BUDGET_EPSILON:
        return SloStatus.EXHAUSTED
    active = [level for level in burn_rates if level.active]
    if any(level.severity == "critical" for level in active):
        return SloStatus.CRITICAL
    if active:
        return SloStatus.WARNING
    # Burning nothing dangerous, but most of the budget already spent is
    # still worth naming.
    if consumed is not None and consumed >= 0.75:
        return SloStatus.WARNING
    return SloStatus.HEALTHY


def _empty(
    status: SloStatus,
    quality: DataQuality,
    levels: tuple[BurnLevel, ...],
    freshness: int | None,
    error_code: str | None,
) -> SloVerdict:
    """A verdict with no numbers — deliberately `None`, never zero.

    Zeros would be indistinguishable from a perfectly healthy window, which
    is the exact confusion this module exists to prevent.
    """
    return SloVerdict(
        status=str(status),
        data_quality=str(quality),
        compliance_ratio=None,
        observed_bad_ratio=None,
        error_budget_total=None,
        error_budget_consumed=None,
        error_budget_remaining=None,
        good=None,
        bad=None,
        total=None,
        sample_count=0,
        burn_rates=[
            BurnRate(
                name=level.name,
                factor=level.factor,
                long_seconds=level.long_seconds,
                short_seconds=level.short_seconds,
                severity=level.severity,
                long_burn=None,
                short_burn=None,
                active=False,
            )
            for level in levels
        ],
        freshness_seconds=freshness,
        error_code=error_code,
    )
