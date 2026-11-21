"""Log and trace signals for alert rules (ADR-0034): the statements narrow
to the bound workloads, and an unanswered store is unknown, never zero."""

from datetime import UTC, datetime

import pytest
from drake_api.alert_rules.model import RULE_SIGNALS, STORED_TELEMETRY, TELEMETRY_SIGNALS, validate
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
    assert out["b1"] == dict.fromkeys(STORED_TELEMETRY)


def test_new_signals_are_rule_signals_and_counts_are_not_ratios() -> None:
    assert set(TELEMETRY_SIGNALS) <= set(RULE_SIGNALS)
    validate("error_logs", ">", 10, 0, "critical")
    validate("slowest_route_p95_seconds", ">", 1.5, 300, "warning")
    with pytest.raises(ValueError):
        validate("error_logs", ">", -1, 0, "critical")


# --- rules that filter (ADR-0035) ---------------------------------------


def test_filters_become_typed_parameters_in_one_statement_per_store() -> None:
    from drake_api.alert_rules.telemetry import RuleFilter, build_filtered

    filters = [
        RuleFilter("r1", "matching_logs", "payment failed", None),
        RuleFilter("r2", "error_logs", "timeout", None),
        RuleFilter("r3", "matching_logs", "payment failed", None),  # same text: one column
        RuleFilter("r4", "slowest_route_p95_seconds", None, "POST /checkout"),
    ]
    plan = build_filtered(
        database="drake",
        bindings=[binding(1)],
        filters=filters,
        start=NOW - WINDOW,
        end=NOW,
        bucket_seconds=None,
    )
    assert plan.columns == {
        ("matching_logs", "payment failed"): "c0",
        ("error_logs", "timeout"): "c1",
    }
    assert plan.parameters["text0"] == "payment failed"
    assert plan.parameters["routes"] == "['POST /checkout']"
    assert plan.logs is not None and plan.traces is not None
    # The texts are parameters, never part of the SQL.
    assert "payment failed" not in plan.logs and "POST /checkout" not in plan.traces
    assert "SeverityNumber >= 17" in plan.logs and "{text1:String}" in plan.logs
    assert "__" not in plan.logs and "__" not in plan.traces


def test_filtered_values_count_zero_when_quiet_and_unknown_when_unread() -> None:
    from drake_api.alert_rules.telemetry import RuleFilter, build_filtered, summarize_filtered

    filters = [
        RuleFilter("r1", "matching_logs", "payment failed", None),
        RuleFilter("r4", "slowest_route_p95_seconds", None, "POST /checkout"),
        RuleFilter("r5", "slow_requests", None, "POST /checkout"),
    ]
    plan = build_filtered(
        database="drake",
        bindings=[binding(1), binding(2)],
        filters=filters,
        start=NOW - WINDOW,
        end=NOW,
        bucket_seconds=None,
    )
    wk1 = plan.keys["b1"]
    out = summarize_filtered(
        plan,
        [{"wk": wk1, "c0": "3"}],
        [
            {
                "wk": wk1,
                "route": "POST /checkout",
                "timed": "4",
                "slow": "2",
                "p95_ns": 1_500_000_000,
            }
        ],
    )
    assert out["b1"] == {"r1": 3.0, "r4": 1.5, "r5": 2.0}
    assert out["b2"] == {"r1": 0.0, "r4": None, "r5": 0.0}
    unread = summarize_filtered(plan, None, None)
    assert unread["b1"] == {"r1": None, "r4": None, "r5": None}


def test_a_series_fills_every_bucket_of_the_day() -> None:
    from datetime import timedelta

    from drake_api.alert_rules.telemetry import (
        SERIES_BUCKET,
        RuleFilter,
        build_filtered,
        series_from_rows,
    )

    rule = RuleFilter("draft", "fatal_logs", "db", None)
    end = NOW
    start = end - timedelta(hours=1)
    plan = build_filtered(
        database="drake",
        bindings=[binding(1)],
        filters=[rule],
        start=start,
        end=end,
        bucket_seconds=SERIES_BUCKET,
    )
    t0 = int(start.timestamp())
    rows = [{"wk": plan.keys["b1"], "t": t0 + 600, "c0": "2"}]
    points = series_from_rows(plan, rule, rows, start, end)["b1"]
    assert len(points) == 12
    assert [v for _, v in points][:4] == [0.0, 0.0, 2.0, 0.0]


def test_an_endpoint_past_the_filter_cap_is_unknown_not_zero() -> None:
    """More endpoints than one statement asks about: the rest have no row.

    A slow-request rule on one of them used to read 0.0 — "nothing slow" —
    and so could never fire, and resolved any incident it held.
    """
    from drake_api.alert_rules.telemetry import (
        MAX_FILTERS,
        RuleFilter,
        build_filtered,
        series_from_rows,
        summarize_filtered,
    )

    filters = [
        RuleFilter(f"r{i}", "slow_requests", None, f"GET /route/{i:02d}")
        for i in range(MAX_FILTERS + 1)
    ]
    plan = build_filtered(
        database="drake",
        bindings=[binding(1)],
        filters=filters,
        start=NOW - WINDOW,
        end=NOW,
        bucket_seconds=None,
    )
    assert len(plan.routes) == MAX_FILTERS
    out = summarize_filtered(plan, None, [])["b1"]
    assert out["r0"] == 0.0  # asked, and quiet
    assert out[f"r{MAX_FILTERS}"] is None  # never asked

    series = series_from_rows(plan, filters[-1], [], NOW - WINDOW, NOW)
    assert all(value is None for _, value in series["b1"])
