"""ADR-0029 trace read path: statements, parameters, normalization.

No database and no ClickHouse. What is asserted is what would go on the
wire and what the API would hand back.
"""

from datetime import UTC, datetime, timedelta

import pytest
from drake_api.observability.store import StoreContractError
from drake_api.observability.traces import (
    MAX_LIMIT,
    TraceQueryError,
    build_trace_detail,
    build_trace_list,
    normalize_requests,
    normalize_routes,
    normalize_spans,
    normalize_totals,
)
from drake_api.rbac.catalog import PERMISSIONS, ROLE_TEMPLATES
from drake_api.settings import ObservabilityStoreConnector
from drake_api.testing import make_settings

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"

BINDING = {
    "namespace": "hermes-dev",
    "workload_kind": "Deployment",
    "workload_name": "core-service",
    "cluster": {"cluster_ref": "duosis-prod-1"},
}


def _list(**overrides):
    values = {
        "database": "otel",
        "binding": BINDING,
        "range_key": "1h",
        "status": "any",
        "sort": "recent",
        "limit": 50,
        "now": NOW,
    }
    values.update(overrides)
    return build_trace_list(**values)


# --- statements ----------------------------------------------------------------


def test_every_statement_is_scoped_to_the_binding_by_parameters_only() -> None:
    query = _list()
    for statement in (query.routes, query.totals, query.requests):
        assert statement.parameters["cluster"] == "duosis-prod-1"
        assert statement.parameters["namespace"] == "hermes-dev"
        assert statement.parameters["workload"] == "core-service"
        assert statement.parameters["workload_attribute"] == "k8s.deployment.name"
        for value in ("duosis-prod-1", "hermes-dev", "core-service"):
            assert value not in statement.sql
        assert "FROM otel.otel_traces" in statement.sql
        assert "{namespace:String}" in statement.sql
        assert "__" not in statement.sql


def test_one_row_means_one_request() -> None:
    query = _list()
    for statement in (query.routes, query.totals, query.requests):
        assert "SpanKind = 'Server' AND ParentSpanId = ''" in statement.sql


def test_window_and_page_are_bounded() -> None:
    query = _list(range_key="6h", limit=10)
    assert query.end - query.start == timedelta(hours=6)
    assert query.requests.parameters["limit"] == "10"
    assert query.requests.max_rows == 10


def test_errors_only_and_sort_are_server_chosen_not_caller_text() -> None:
    assert _list(status="error").requests.parameters["errors_only"] == "1"
    assert _list(status="any").requests.parameters["errors_only"] == "0"
    assert "ORDER BY Duration DESC" in _list(sort="slowest").requests.sql
    assert "ORDER BY Timestamp DESC" in _list(sort="recent").requests.sql


@pytest.mark.parametrize(
    "overrides",
    [
        {"range_key": "30d"},
        {"status": "warn"},
        {"sort": "Duration; DROP TABLE x"},
        {"limit": 0},
        {"limit": MAX_LIMIT + 1},
        {"binding": {**BINDING, "workload_kind": "CronJob"}},
    ],
)
def test_out_of_contract_requests_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(TraceQueryError):
        _list(**overrides)


def test_a_database_name_that_is_not_an_identifier_never_reaches_sql() -> None:
    # Server configuration, not caller input: a contract failure, not a 422.
    with pytest.raises(StoreContractError):
        _list(database="otel; DROP")


def test_trace_detail_takes_only_a_real_trace_id() -> None:
    statement = build_trace_detail(database="otel", binding=BINDING, trace_id=TRACE.upper())
    assert statement.parameters["trace_id"] == TRACE
    assert TRACE not in statement.sql
    assert statement.parameters["namespace"] == "hermes-dev"
    for bad in ("", "xyz", TRACE[:-1], TRACE + "0", "0" * 32, "' OR 1=1 --"):
        with pytest.raises(TraceQueryError):
            build_trace_detail(database="otel", binding=BINDING, trace_id=bad)


# --- normalization --------------------------------------------------------------


def test_routes_report_ratio_and_milliseconds() -> None:
    [row] = normalize_routes(
        [
            {
                "route": "GET /orders/*",
                "requests": "40",
                "errors": "2",
                "p50_ns": 1.0e8,
                "p95_ns": "2e8",
            }
        ]
    )
    assert row == {
        "route": "GET /orders/*",
        "requests": 40,
        "errors": 2,
        "error_ratio": 0.05,
        "p50_ms": 100.0,
        "p95_ms": 200.0,
    }


def test_no_requests_is_no_latency_not_zero() -> None:
    totals = normalize_totals([{"requests": "0", "errors": "0", "p95_ns": None}])
    assert totals == {"requests": 0, "errors": 0, "error_ratio": None, "p95_ms": None}
    assert normalize_totals([]) == {"requests": 0, "errors": 0, "error_ratio": None, "p95_ms": None}


def test_a_nan_quantile_is_no_measurement() -> None:
    [row] = normalize_routes(
        [{"route": "x", "requests": 1, "errors": 0, "p50_ns": "nan", "p95_ns": "inf"}]
    )
    assert row["p50_ms"] is None
    assert row["p95_ms"] is None


def test_requests_normalize_and_refuse_a_bad_trace_id() -> None:
    row = {
        "ts": "2026-11-04 11:59:58.100000000",
        "trace_id": TRACE,
        "route": "POST /v1/payments",
        "duration_ns": "1200000000",
        "status": "Error",
        "method": "POST",
        "http_status": "502",
        "path": "/v1/payments",
        "pod": "core-1",
    }
    [entry] = normalize_requests([row])
    assert entry["duration_ms"] == 1200.0
    assert entry["error"] is True
    assert entry["timestamp"] == "2026-11-04T11:59:58.100000+00:00"
    with pytest.raises(ValueError):
        normalize_requests([{**row, "trace_id": "nope"}])


def test_spans_show_only_allowlisted_attributes() -> None:
    [span] = normalize_spans(
        [
            {
                "ts": "2026-11-04 11:59:58.000000000",
                "span_id": "00f067aa0ba902b7",
                "parent_span_id": "",
                "name": "GET /orders/*",
                "kind": "Server",
                "duration_ns": "5000000",
                "status": "Unset",
                "status_message": "",
                "attributes": {
                    "http.request.method": "GET",
                    "client.address": "10.0.0.7",
                    "something.new": "x",
                },
                "pod": "core-1",
            }
        ]
    )
    assert span["attributes"] == {"http.request.method": "GET"}
    assert span["parent_span_id"] is None
    assert span["error"] is False


# --- settings and catalog ---------------------------------------------------------


def test_traces_are_off_by_default_and_need_a_store_in_production() -> None:
    assert make_settings().observability_traces_enabled is False
    with pytest.raises(RuntimeError, match="no observability store"):
        make_settings(
            env="production", observability_traces_enabled=True
        ).validate_runtime_security()
    with pytest.raises(RuntimeError, match="username and a password file"):
        make_settings(
            env="production",
            observability_traces_enabled=True,
            observability_store=ObservabilityStoreConnector(url="http://10.0.0.5:8123"),
        ).validate_runtime_security()


def test_traces_view_is_a_distinct_permission_held_only_by_the_owner_template() -> None:
    assert "traces.view" in PERMISSIONS
    holders = [name for name, (_, keys) in ROLE_TEMPLATES.items() if "traces.view" in keys]
    assert holders == ["Platform Owner"]
