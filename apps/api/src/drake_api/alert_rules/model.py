"""What an alert rule is, and how one is judged. Pure: no I/O, no clock.

A rule names a signal, a comparison, a threshold, how long the breach must
hold, and a severity (ADR-0031). It is judged on the same numbers the
service-health engine computed for the binding in that runner cycle, one
sample per cycle, so a rule never queries the provider itself.

One function decides a transition (`step`) and one replays a history
(`replay`). They share the same rule, so the screen's "this rule would
have fired N times in the last day" says exactly what the runner would
have done with the same samples.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# signal key -> (health payload section, field). Units are the engine's own:
# ratios 0..1, seconds, requests per second, counts.
SIGNALS: dict[str, tuple[str, str]] = {
    "error_ratio": ("application", "error_ratio"),
    "latency_p95_seconds": ("application", "latency_p95_seconds"),
    "request_rate": ("application", "request_rate"),
    "restarts": ("stability", "restarts_in_window"),
    "cpu_utilization": ("resources", "cpu_utilization"),
    "memory_utilization": ("resources", "memory_utilization"),
    "unavailable_replicas": ("availability", "unavailable_replicas"),
}
# Read from the logs and traces store over the last five minutes, not from
# the health verdict (alert_rules.telemetry, ADR-0034). Counts, and seconds.
LOG_SIGNALS = ("error_logs", "fatal_logs", "matching_logs")
TRACE_SIGNALS = ("slow_requests", "slowest_route_p95_seconds")
TELEMETRY_SIGNALS = (*LOG_SIGNALS, *TRACE_SIGNALS)
# `matching_logs` only exists with a text to match, so it is judged per
# rule and never stored; the others are columns of service_health_samples.
STORED_TELEMETRY = tuple(s for s in TELEMETRY_SIGNALS if s != "matching_logs")
STORED_SIGNALS = (*SIGNALS, *STORED_TELEMETRY)
# Every signal a rule may watch.
RULE_SIGNALS = (*SIGNALS, *TELEMETRY_SIGNALS)
RATIO_SIGNALS = ("error_ratio", "cpu_utilization", "memory_utilization")
# Counted per line or per request: a "normal" for them is a whole number.
COUNT_SIGNALS = (
    "restarts",
    "unavailable_replicas",
    "error_logs",
    "fatal_logs",
    "matching_logs",
    "slow_requests",
)
# Fixed thresholds, and two that judge against the signal's own normal
# (ADR-0035): the same time of day over the last week.
ANOMALY_OPERATORS = ("above_normal", "below_normal")
OPERATORS = (">", "<", *ANOMALY_OPERATORS)
MAX_FILTER_TEXT = 200
MAX_ROUTE = 300
# Sensitivity of an anomaly rule: how many spreads away from normal.
MIN_SENSITIVITY = 1.0
MAX_SENSITIVITY = 10.0
# A normal needs this many past samples at this time of day, or the rule
# has no judgement to make (no data, not a breach).
MIN_BASELINE = 30
BASELINE_DAYS = 7
BASELINE_HALF_WINDOW = timedelta(minutes=30)
SEVERITIES = ("critical", "warning")
MAX_FOR_SECONDS = 86400


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def sample_from_health(payload: dict[str, Any]) -> dict[str, float | None]:
    """The signal values one health verdict carries, keyed by signal.

    `unavailable_replicas` is derived: desired minus ready, never below 0,
    and unknown when either side is unknown.
    """
    out: dict[str, float | None] = {}
    for key, (section, field) in SIGNALS.items():
        if key == "unavailable_replicas":
            availability = payload.get("availability") or {}
            desired = _number(availability.get("desired_replicas"))
            ready = _number(availability.get("ready_replicas"))
            out[key] = None if desired is None or ready is None else max(0.0, desired - ready)
            continue
        out[key] = _number((payload.get(section) or {}).get(field))
    return out


@dataclass(frozen=True)
class RuleCondition:
    signal: str
    operator: str
    threshold: float
    for_seconds: int

    def breached(self, value: float) -> bool:
        return value < self.threshold if self.operator == "<" else value > self.threshold


@dataclass(frozen=True)
class RuleState:
    state: str = "ok"  # ok | pending | firing
    breach_since: datetime | None = None
    firing_since: datetime | None = None


@dataclass(frozen=True)
class Step:
    state: RuleState
    event: str | None  # "fired" | "resolved" | None


def step(condition: RuleCondition, value: float | None, previous: RuleState, now: datetime) -> Step:
    """One evaluation. A missing value changes nothing: no data is not a
    recovery, and it is not a breach either."""
    if value is None:
        return Step(previous, None)
    if not condition.breached(value):
        if previous.state == "firing":
            return Step(RuleState(), "resolved")
        return Step(RuleState(), None)
    if previous.state == "firing":
        return Step(previous, None)
    since = previous.breach_since if previous.state == "pending" and previous.breach_since else now
    if now - since >= timedelta(seconds=condition.for_seconds):
        return Step(RuleState("firing", since, now), "fired")
    return Step(RuleState("pending", since, None), None)


@dataclass(frozen=True)
class Episode:
    fired_at: datetime
    resolved_at: datetime | None
    peak: float


def replay(
    condition: RuleCondition,
    samples: list[tuple[datetime, float | None]],
    thresholds: list[float | None] | None = None,
) -> list[Episode]:
    """Every time the rule would have fired over these samples (oldest
    first), using `step` itself. *thresholds*, when given, is the threshold
    in force at each sample (an anomaly rule's moving normal); a sample
    without one has nothing to be judged against."""
    state = RuleState()
    episodes: list[Episode] = []
    current: dict[str, Any] | None = None
    for index, (at, value) in enumerate(samples):
        if thresholds is not None:
            bound = thresholds[index]
            if bound is None:
                value = None
            condition = RuleCondition(
                condition.signal, condition.operator, bound or 0.0, condition.for_seconds
            )
        result = step(condition, value, state, at)
        state = result.state
        if result.event == "fired":
            current = {"fired_at": at, "peak": value if value is not None else condition.threshold}
        elif current is not None and value is not None:
            worse = (
                value < current["peak"] if condition.operator == "<" else value > current["peak"]
            )
            if worse and result.event != "resolved":
                current["peak"] = value
        if result.event == "resolved" and current is not None:
            episodes.append(Episode(current["fired_at"], at, float(current["peak"])))
            current = None
    if current is not None:
        episodes.append(Episode(current["fired_at"], None, float(current["peak"])))
    return episodes


def validate(
    signal: str,
    operator: str,
    threshold: float,
    for_seconds: int,
    severity: str,
    log_contains: str | None = None,
    route: str | None = None,
) -> None:
    """Refuse a rule the evaluator could not judge honestly."""
    if signal not in RULE_SIGNALS:
        raise ValueError("unknown signal")
    if log_contains is not None:
        if signal not in LOG_SIGNALS:
            raise ValueError("only a log signal can match text")
        if not 1 <= len(log_contains) <= MAX_FILTER_TEXT or not log_contains.strip():
            raise ValueError("the text to match is 1 to 200 characters")
    if signal == "matching_logs" and log_contains is None:
        raise ValueError("matching_logs needs the text to match")
    if route is not None:
        if signal not in TRACE_SIGNALS:
            raise ValueError("only a trace signal can watch one endpoint")
        if not 1 <= len(route) <= MAX_ROUTE or not route.strip():
            raise ValueError("the endpoint is 1 to 300 characters")
    if operator in ANOMALY_OPERATORS:
        if log_contains is not None or route is not None:
            raise ValueError("an anomaly rule judges a stored signal, not a filtered one")
        if not MIN_SENSITIVITY <= threshold <= MAX_SENSITIVITY:
            raise ValueError("sensitivity is between 1 and 10")
    if operator not in OPERATORS:
        raise ValueError("unknown operator")
    if severity not in SEVERITIES:
        raise ValueError("unknown severity")
    if not 0 <= for_seconds <= MAX_FOR_SECONDS:
        raise ValueError("for_seconds out of range")
    if threshold != threshold or threshold in (float("inf"), float("-inf")):
        raise ValueError("threshold must be a number")
    if operator not in ANOMALY_OPERATORS and signal in RATIO_SIGNALS and not 0 <= threshold <= 1:
        raise ValueError("a ratio threshold is between 0 and 1")
    if threshold < 0:
        raise ValueError("threshold must not be negative")


# --- anomaly: a signal against its own normal (ADR-0035) ------------------


def _floor(signal: str, median: float) -> float:
    """The smallest spread a normal is given. A signal that never moved
    (zero errors all week) would otherwise call any change an anomaly."""
    if signal in COUNT_SIGNALS:
        return 1.0
    if signal in RATIO_SIGNALS:
        return 0.01
    if signal in ("latency_p95_seconds", "slowest_route_p95_seconds"):
        return max(0.05, 0.1 * abs(median))
    if signal == "request_rate":
        return max(0.1, 0.1 * abs(median))
    return max(1e-6, 0.1 * abs(median))


@dataclass(frozen=True)
class Band:
    low: float
    median: float
    high: float


def normal_parts(values: list[float], signal: str) -> tuple[float, float] | None:
    """(median, spread) of past values: the spread is the scaled median
    absolute deviation, never below the signal's floor. None when there are
    too few values to call anything normal."""
    if len(values) < MIN_BASELINE:
        return None
    ordered = sorted(values)
    median = _median(ordered)
    deviation = _median(sorted(abs(v - median) for v in ordered)) * 1.4826
    return median, max(deviation, _floor(signal, median))


def band_of(parts: tuple[float, float] | None, sensitivity: float) -> Band | None:
    if parts is None:
        return None
    median, spread = parts
    return Band(max(0.0, median - sensitivity * spread), median, median + sensitivity * spread)


def normal_band(values: list[float], signal: str, sensitivity: float) -> Band | None:
    """Median ± sensitivity x spread of past values (`normal_parts`)."""
    return band_of(normal_parts(values, signal), sensitivity)


def _median(ordered: list[float]) -> float:
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def baseline_windows(at: datetime) -> list[tuple[datetime, datetime]]:
    """The same time of day (± half an hour) on each of the previous
    BASELINE_DAYS days: what "normal at this hour" is drawn from."""
    return [
        (
            at - timedelta(days=d) - BASELINE_HALF_WINDOW,
            at - timedelta(days=d) + BASELINE_HALF_WINDOW,
        )
        for d in range(1, BASELINE_DAYS + 1)
    ]


def baseline_values(times: list[datetime], values: list[float | None], at: datetime) -> list[float]:
    """Past values inside `baseline_windows(at)`. *times* is sorted."""
    from bisect import bisect_left, bisect_right

    out: list[float] = []
    for start, end in baseline_windows(at):
        lo = bisect_left(times, start)
        hi = bisect_right(times, end)
        out.extend(v for v in values[lo:hi] if v is not None)
    return out


def anomaly_threshold(operator: str, band: Band | None) -> float | None:
    """The fixed-comparison threshold an anomaly rule applies right now."""
    if band is None:
        return None
    return band.high if operator == "above_normal" else band.low


def effective_operator(operator: str) -> str:
    return "<" if operator == "below_normal" else ">" if operator == "above_normal" else operator
