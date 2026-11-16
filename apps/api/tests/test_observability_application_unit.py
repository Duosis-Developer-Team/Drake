"""Golden signals from traces: the statement and what it means.

No database and no ClickHouse.
"""

from datetime import UTC, datetime

import pytest
from drake_api.observability.application import (
    build_application_query,
    normalize_application,
)
from drake_api.observability.scope import ScopeError

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
CONTEXT = {
    "namespace": "hermes-dev",
    "workload_kind": "Deployment",
    "workload_name": "core-service",
    "cluster_ref": "duosis-prod-1",
}


def test_the_statement_reads_server_spans_scoped_by_parameters() -> None:
    query = build_application_query(database="otel", context=CONTEXT, window_seconds=300, now=NOW)
    assert "SpanKind = 'Server'" in query.statement
    # A called service's server span has a parent in its caller (ADR-0029).
    assert "ParentSpanId" not in query.statement
    for value in ("duosis-prod-1", "hermes-dev", "core-service"):
        assert value not in query.statement
    assert query.parameters["window_start"] == "2026-11-15 11:55:00.000000"
    assert query.parameters["seen_start"] == "2026-11-14 12:00:00.000000"
    assert query.parameters["workload"] == "core-service"


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ScopeError):
        build_application_query(
            database="otel",
            context={**CONTEXT, "workload_kind": "CronJob"},
            window_seconds=300,
            now=NOW,
        )


def test_a_workload_never_traced_is_not_a_quiet_healthy_one() -> None:
    assert normalize_application([], 300) is None
    assert (
        normalize_application([{"seen": "0", "requests": "0", "errors": "0", "p95_ns": None}], 300)
        is None
    )


def test_an_idle_traced_workload_has_zero_requests_and_no_ratio() -> None:
    signals = normalize_application(
        [{"seen": "120", "requests": "0", "errors": "0", "p95_ns": float("nan")}], 300
    )
    assert signals is not None
    assert signals.requests == 0
    assert signals.request_rate == 0
    assert signals.error_ratio is None
    assert signals.latency_p95_seconds is None


def test_a_busy_workload_gives_rate_ratio_and_p95_in_seconds() -> None:
    signals = normalize_application(
        [{"seen": "9000", "requests": "600", "errors": "30", "p95_ns": 1_840_000_000.0}], 300
    )
    assert signals is not None
    assert signals.request_rate == 2.0
    assert signals.error_ratio == 0.05
    assert signals.latency_p95_seconds == pytest.approx(1.84)


def test_too_few_requests_are_counted_but_not_judged() -> None:
    signals = normalize_application(
        [{"seen": "500", "requests": "10", "errors": "1", "p95_ns": 9e8}], 300
    )
    assert signals is not None
    assert signals.requests == 10
    assert signals.error_ratio is None
    assert signals.latency_p95_seconds is None


@pytest.mark.parametrize("bad", [{"seen": "-1"}, {"requests": True}, {"errors": None}])
def test_malformed_rows_are_refused(bad) -> None:
    row = {"seen": "10", "requests": "5", "errors": "0", "p95_ns": 1.0, **bad}
    with pytest.raises(ValueError):
        normalize_application([row], 300)
