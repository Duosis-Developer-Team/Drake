"""Alert rule judgement (ADR-0031): one step, and a replay that is the
same step applied to a history."""

from datetime import UTC, datetime, timedelta

import pytest
from drake_api.alert_rules.model import (
    RuleCondition,
    RuleState,
    replay,
    sample_from_health,
    step,
    validate,
)

T0 = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
CYCLE = timedelta(minutes=3)
ERR = RuleCondition("error_ratio", ">", 0.05, 300)


def at(n: int) -> datetime:
    return T0 + n * CYCLE


def test_a_breach_must_hold_for_its_duration_before_it_fires() -> None:
    first = step(ERR, 0.08, RuleState(), at(0))
    assert first.state.state == "pending" and first.event is None
    second = step(ERR, 0.09, first.state, at(1))  # 3 min held: not yet 5
    assert second.state.state == "pending" and second.event is None
    third = step(ERR, 0.07, second.state, at(2))  # 6 min held
    assert third.state.state == "firing" and third.event == "fired"
    assert third.state.breach_since == at(0)


def test_a_dip_below_the_threshold_resets_the_clock() -> None:
    pending = step(ERR, 0.08, RuleState(), at(0)).state
    reset = step(ERR, 0.01, pending, at(1))
    assert reset.state == RuleState() and reset.event is None
    again = step(ERR, 0.08, reset.state, at(2))
    assert again.state.breach_since == at(2)


def test_firing_resolves_once_the_value_is_back() -> None:
    firing = RuleState("firing", at(0), at(2))
    still = step(ERR, 0.2, firing, at(3))
    assert still.state == firing and still.event is None
    back = step(ERR, 0.01, firing, at(4))
    assert back.state.state == "ok" and back.event == "resolved"


def test_no_data_is_neither_a_breach_nor_a_recovery() -> None:
    firing = RuleState("firing", at(0), at(2))
    assert step(ERR, None, firing, at(3)) == step(ERR, None, firing, at(3))
    assert step(ERR, None, firing, at(3)).state == firing
    assert step(ERR, None, firing, at(3)).event is None


def test_a_zero_duration_rule_fires_on_the_first_breach() -> None:
    rule = RuleCondition("restarts", ">", 3, 0)
    assert step(rule, 4, RuleState(), at(0)).event == "fired"


def test_below_rules_fire_when_the_value_drops() -> None:
    rule = RuleCondition("request_rate", "<", 1.0, 0)
    assert step(rule, 0.2, RuleState(), at(0)).event == "fired"
    assert step(rule, 2.0, RuleState(), at(0)).event is None


def test_replay_finds_each_episode_with_its_peak() -> None:
    values = [0.01, 0.08, 0.09, 0.12, 0.02, 0.01, 0.07, 0.08, 0.09]
    episodes = replay(ERR, [(at(i), v) for i, v in enumerate(values)])
    assert len(episodes) == 2
    assert episodes[0].fired_at == at(3) and episodes[0].resolved_at == at(4)
    assert episodes[0].peak == pytest.approx(0.12)
    # Still firing at the end of the history.
    assert episodes[1].fired_at == at(8) and episodes[1].resolved_at is None


def test_the_sample_reads_the_health_payload_and_derives_unavailable_replicas() -> None:
    payload = {
        "application": {"error_ratio": 0.02, "latency_p95_seconds": 0.4, "request_rate": 5.0},
        "stability": {"restarts_in_window": 1},
        "resources": {"cpu_utilization": 0.5, "memory_utilization": None},
        "availability": {"desired_replicas": 3, "ready_replicas": 1},
    }
    sample = sample_from_health(payload)
    assert sample["error_ratio"] == 0.02
    assert sample["restarts"] == 1.0
    assert sample["memory_utilization"] is None
    assert sample["unavailable_replicas"] == 2.0
    assert sample_from_health({})["unavailable_replicas"] is None


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("nope", ">", 1, 0, "critical"), "unknown signal"),
        (("error_ratio", ">=", 0.1, 0, "critical"), "unknown operator"),
        (("error_ratio", ">", 0.1, 0, "page"), "unknown severity"),
        (("error_ratio", ">", 5, 0, "critical"), "between 0 and 1"),
        (("restarts", ">", 3, 90000, "critical"), "for_seconds"),
        (("restarts", ">", -1, 0, "warning"), "negative"),
    ],
)
def test_validate_refuses_what_cannot_be_judged(args: tuple, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate(*args)
