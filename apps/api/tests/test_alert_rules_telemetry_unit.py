"""Log and trace signals for alert rules (ADR-0034): the statements narrow
to the bound workloads, and an unanswered store is unknown, never zero."""

from datetime import UTC, datetime

import pytest
from drake_api.alert_rules.model import RULE_SIGNALS, TELEMETRY_SIGNALS, validate
from drake_api.alert_rules.telemetry import WINDOW, build_statements, summarize

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


def binding(i: int, kind: str = "Deployment") -> dict[str, object]:
    return {
        "id": f"b{i}",
        "cluster": {"cluster_ref": "prod"},
        "namespace": "hermes-test",
        "workload_kind": kind,
        "workload_name": f"svc-{i}",
        "http_probes": [],
    }


def test_statements_narrow_to_the_bound_workloads_over_five_minutes() -> None:
    statements, parameters, keys = build_statements(
        database="drake", bindings=[binding(1), binding(2)], now=NOW
    )
    assert keys == {
        "b1": "prod|hermes-test|deployment|svc-1",
        "b2": "prod|hermes-test|deployment|svc-2",
    }
    assert parameters["workloads"] == (
        "['prod|hermes-test|deployment|svc-1','prod|hermes-test|deployment|svc-2']"
    )
    assert parameters["start"].startswith((NOW - WINDOW).strftime("%Y-%m-%d %H:%M"))
    for sql in statements.values():
        assert "has({workloads:Array(String)}" in sql
        assert "__" not in sql  # every placeholder was filled
    assert "drake.otel_logs" in statements["logs"]
    assert "SpanKind = 'Server'" in statements["traces"]


def test_a_workload_the_store_cannot_name_is_left_out() -> None:
    _, _, keys = build_statements(
        database="drake", bindings=[binding(1), binding(2, kind="CronJob")], now=NOW
    )
    assert list(keys) == ["b1"]


def test_answers_become_values_and_a_quiet_service_counts_zero() -> None:
    keys = {"b1": "w1", "b2": "w2"}
    out = summarize(
        keys,
        [{"wk": "w1", "errors": "12", "fatal": "1"}],
        [{"wk": "w1", "slow": "4", "routes": "3", "route_p95_ns": 1_250_000_000}],
    )
    assert out["b1"] == {
        "error_logs": 12.0,
        "fatal_logs": 1.0,
        "slow_requests": 4.0,
        "slowest_route_p95_seconds": 1.25,
    }
    # Nothing in the window: zero lines, but no route to give a p95 for.
    assert out["b2"] == {
        "error_logs": 0.0,
        "fatal_logs": 0.0,
        "slow_requests": 0.0,
        "slowest_route_p95_seconds": None,
    }


def test_a_store_that_did_not_answer_is_unknown_not_zero() -> None:
    out = summarize({"b1": "w1"}, None, None)
    assert out["b1"] == dict.fromkeys(TELEMETRY_SIGNALS)


def test_new_signals_are_rule_signals_and_counts_are_not_ratios() -> None:
    assert set(TELEMETRY_SIGNALS) <= set(RULE_SIGNALS)
    validate("error_logs", ">", 10, 0, "critical")
    validate("slowest_route_p95_seconds", ">", 1.5, 300, "warning")
    with pytest.raises(ValueError):
        validate("error_logs", ">", -1, 0, "critical")
