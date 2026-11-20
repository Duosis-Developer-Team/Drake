"""Rules against a signal's own normal, and rules that filter (ADR-0035):
the band, where its history comes from, and what a rule may combine."""

from datetime import UTC, datetime, timedelta

import pytest
from drake_api.alert_rules.model import (
    MIN_BASELINE,
    RuleCondition,
    anomaly_threshold,
    baseline_values,
    baseline_windows,
    effective_operator,
    normal_band,
    replay,
    validate,
)

AT = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)


def test_the_band_is_median_plus_sensitivity_spreads() -> None:
    values = [0.2 + 0.01 * (i % 5) for i in range(60)]  # 0.20 .. 0.24
    band = normal_band(values, "latency_p95_seconds", 3)
    assert band is not None
    assert band.median == pytest.approx(0.22)
    # The spread never goes below the signal's floor (50 ms for latency).
    assert band.high == pytest.approx(0.22 + 3 * 0.05)
    assert band.low == pytest.approx(0.22 - 3 * 0.05)


def test_a_signal_that_never_moved_still_needs_a_real_change() -> None:
    band = normal_band([0.0] * 100, "error_logs", 3)
    assert band is not None
    assert (band.low, band.high) == (0.0, 3.0)  # three lines, not one


def test_too_little_history_judges_nothing() -> None:
    assert normal_band([1.0] * (MIN_BASELINE - 1), "error_logs", 3) is None
    assert anomaly_threshold("above_normal", None) is None


def test_history_is_the_same_time_of_day_on_the_past_week() -> None:
    windows = baseline_windows(AT)
    assert len(windows) == 7
    assert windows[0] == (
        AT - timedelta(days=1, minutes=30),
        AT - timedelta(days=1) + timedelta(minutes=30),
    )
    times = [AT - timedelta(days=1, minutes=20), AT - timedelta(hours=3), AT - timedelta(days=2)]
    values = [5.0, 99.0, 7.0]
    # Three hours ago is not this time of day; yesterday and two days ago are.
    assert sorted(
        baseline_values(sorted(times), [v for _, v in sorted(zip(times, values, strict=True))], AT)
    ) == [5.0, 7.0]


def test_replay_follows_a_moving_threshold() -> None:
    condition = RuleCondition("error_logs", effective_operator("above_normal"), 0, 0)
    samples = [(AT + timedelta(minutes=2 * i), v) for i, v in enumerate([5.0, 5.0, 9.0, 9.0])]
    # The normal rose with the traffic: 9 is a breach at first, then not.
    assert len(replay(condition, samples, [6.0, 6.0, 6.0, 10.0])) == 1
    # No normal to judge against: nothing fires.
    assert replay(condition, samples, [None, None, None, None]) == []


def test_what_a_rule_may_combine() -> None:
    validate("matching_logs", ">", 0, 0, "critical", log_contains="payment failed")
    validate("error_logs", ">", 5, 0, "warning", log_contains="timeout")
    validate("slowest_route_p95_seconds", ">", 1, 300, "warning", route="POST /checkout")
    validate("error_ratio", "above_normal", 3, 600, "warning")
    refused = [
        dict(signal="matching_logs", operator=">", threshold=0),  # needs a text
        dict(signal="error_ratio", operator=">", threshold=0.1, log_contains="x"),
        dict(signal="error_logs", operator=">", threshold=1, route="GET /"),
        dict(signal="error_logs", operator="above_normal", threshold=3, log_contains="x"),
        dict(signal="error_ratio", operator="above_normal", threshold=11),
        dict(signal="error_logs", operator=">", threshold=1, log_contains="   "),
    ]
    for rule in refused:
        with pytest.raises(ValueError):
            validate(
                rule["signal"],
                rule["operator"],
                rule["threshold"],
                0,
                "warning",
                rule.get("log_contains"),
                rule.get("route"),
            )
