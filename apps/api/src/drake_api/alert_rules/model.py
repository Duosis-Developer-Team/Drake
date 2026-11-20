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
LOG_SIGNALS = ("error_logs", "fatal_logs")
TRACE_SIGNALS = ("slow_requests", "slowest_route_p95_seconds")
TELEMETRY_SIGNALS = (*LOG_SIGNALS, *TRACE_SIGNALS)
# Every signal a rule may watch, and a column of service_health_samples.
RULE_SIGNALS = (*SIGNALS, *TELEMETRY_SIGNALS)
RATIO_SIGNALS = ("error_ratio", "cpu_utilization", "memory_utilization")
OPERATORS = (">", "<")
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


def replay(condition: RuleCondition, samples: list[tuple[datetime, float | None]]) -> list[Episode]:
    """Every time the rule would have fired over these samples (oldest
    first), using `step` itself."""
    state = RuleState()
    episodes: list[Episode] = []
    current: dict[str, Any] | None = None
    for at, value in samples:
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


def validate(signal: str, operator: str, threshold: float, for_seconds: int, severity: str) -> None:
    """Refuse a rule the evaluator could not judge honestly."""
    if signal not in RULE_SIGNALS:
        raise ValueError("unknown signal")
    if operator not in OPERATORS:
        raise ValueError("unknown operator")
    if severity not in SEVERITIES:
        raise ValueError("unknown severity")
    if not 0 <= for_seconds <= MAX_FOR_SECONDS:
        raise ValueError("for_seconds out of range")
    if threshold != threshold or threshold in (float("inf"), float("-inf")):
        raise ValueError("threshold must be a number")
    if signal in RATIO_SIGNALS and not 0 <= threshold <= 1:
        raise ValueError("a ratio threshold is between 0 and 1")
    if threshold < 0:
        raise ValueError("threshold must not be negative")
