"""Cross-service error signals: statements, parameters, matching back.

No database and no ClickHouse. What is asserted is what would go on the
wire and which numbers the API would attach to which binding.
"""

from datetime import UTC, datetime

import pytest
from drake_api.observability.scope import ScopeError
from drake_api.observability.signals import (
    MAX_BINDINGS,
    SignalQueryError,
    build_signal_query,
    summarize_logs,
    summarize_traces,
)
from drake_api.observability.store import StoreContractError

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)


def binding(bid: str, name: str, *, kind: str = "Deployment", ns: str = "hermes-dev"):
    return {
        "id": bid,
        "namespace": ns,
        "workload_kind": kind,
        "workload_name": name,
        "cluster": {"cluster_ref": "duosis-prod-1"},
    }


CORE = binding("b1", "core-service")
DB = binding("b2", "postgres", kind="StatefulSet", ns="hermes")


def test_statements_narrow_by_parameters_only() -> None:
    query = build_signal_query(database="otel", bindings=[CORE, DB], range_key="1h", now=NOW)
    assert query.parameters["clusters"] == "['duosis-prod-1']"
    assert query.parameters["namespaces"] == "['hermes','hermes-dev']"
    for sql in (query.logs, query.traces):
        for value in ("duosis-prod-1", "hermes-dev", "core-service", "postgres"):
            assert value not in sql
        assert "otel.otel_" in sql
        assert "__DATABASE__" not in sql
    assert "SpanKind = 'Server' AND ParentSpanId = ''" in query.traces
    assert query.parameters["start"] == "2026-11-04 11:00:00.000000"


def test_array_parameter_cannot_break_out_of_its_literal() -> None:
    hostile = binding("b3", "x", ns="a'],['b\\")
    query = build_signal_query(database="otel", bindings=[hostile], range_key="1h", now=NOW)
    assert query.parameters["namespaces"] == "['a\\'],[\\'b\\\\']"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"range_key": "30d"}, "unsupported range"),
        ({"bindings": []}, "no bindings"),
        ({"bindings": [CORE] * (MAX_BINDINGS + 1)}, "too many bindings"),
    ],
)
def test_refuses_what_it_cannot_bound(kwargs, message) -> None:
    values = {"database": "otel", "bindings": [CORE], "range_key": "1h", "now": NOW, **kwargs}
    with pytest.raises(SignalQueryError, match=message):
        build_signal_query(**values)


def test_unknown_kind_is_refused_not_guessed() -> None:
    with pytest.raises(ScopeError):
        build_signal_query(
            database="otel", bindings=[binding("b", "x", kind="CronJob")], range_key="1h", now=NOW
        )


def test_hostile_database_name_is_refused() -> None:
    with pytest.raises(StoreContractError):
        build_signal_query(database="otel; DROP", bindings=[CORE], range_key="1h", now=NOW)


def _group(**values):
    return {
        "cluster": "duosis-prod-1",
        "namespace": "hermes-dev",
        "deployment": "",
        "statefulset": "",
        "daemonset": "",
        "newest": "2026-11-04 11:59:00.123456789",
        **values,
    }


def test_log_groups_attach_to_their_binding_and_strangers_are_dropped() -> None:
    rows = [
        _group(deployment="core-service", lines="120", errors="3", warnings="7"),
        # Same namespace, a workload the caller did not ask for.
        _group(deployment="secret-service", lines="999", errors="99", warnings="0"),
        _group(namespace="hermes", statefulset="postgres", lines="5", errors="0", warnings="1"),
    ]
    out = summarize_logs(rows, [CORE, DB])
    assert out == {
        "b1": {
            "lines": 120,
            "errors": 3,
            "warnings": 7,
            "newest_at": "2026-11-04T11:59:00.123456+00:00",
        },
        "b2": {
            "lines": 5,
            "errors": 0,
            "warnings": 1,
            "newest_at": "2026-11-04T11:59:00.123456+00:00",
        },
    }


def test_kind_decides_the_column_a_name_is_matched_in() -> None:
    # A Deployment named "postgres" must not answer for the StatefulSet.
    rows = [_group(namespace="hermes", deployment="postgres", lines="9", errors="9", warnings="0")]
    assert summarize_logs(rows, [DB])["b2"]["lines"] == 0


def test_silence_is_zero_lines_and_no_timestamp() -> None:
    assert summarize_logs([], [CORE])["b1"] == {
        "lines": 0,
        "errors": 0,
        "warnings": 0,
        "newest_at": None,
    }


def test_traces_ratio_and_p95_and_no_requests_means_no_ratio() -> None:
    rows = [_group(deployment="core-service", requests="200", errors="5", p95_ns=14843000.0)]
    out = summarize_traces(rows, [CORE, DB])
    assert out["b1"]["requests"] == 200
    assert out["b1"]["error_ratio"] == 0.025
    assert out["b1"]["p95_ms"] == 14.843
    assert out["b2"] == {
        "requests": 0,
        "errors": 0,
        "error_ratio": None,
        "p95_ms": None,
        "newest_at": None,
    }


def test_nan_p95_is_none() -> None:
    rows = [_group(deployment="core-service", requests="1", errors="0", p95_ns=float("nan"))]
    assert summarize_traces(rows, [CORE])["b1"]["p95_ms"] is None


@pytest.mark.parametrize("bad", ["-1", "1.5", True, None, "abc"])
def test_malformed_counts_are_refused(bad) -> None:
    with pytest.raises(ValueError):
        summarize_logs(
            [_group(deployment="core-service", lines=bad, errors="0", warnings="0")], [CORE]
        )
