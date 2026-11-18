"""Health checks and scrapes are not traffic: which probes count, and that
every request read leaves them out (observability.health_checks).

No database and no ClickHouse. The real-store behaviour is checked in
test_observability_health_checks_integration.
"""

from datetime import UTC, datetime

from drake_api.observability.application import build_application_query
from drake_api.observability.health_checks import (
    IS_HEALTH_CHECK,
    LONG_LIVED_SECONDS,
    MAX_PROBES_PER_WORKLOAD,
    health_check_parameter,
    probes_from_summary,
)
from drake_api.observability.signals import build_signal_query
from drake_api.observability.timeline import build_timeline_query
from drake_api.observability.traces import build_trace_list, normalize_totals

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)
BINDING = {
    "id": "b1",
    "namespace": "hermes-test",
    "workload_kind": "Deployment",
    "workload_name": "frontend",
    "cluster": {"cluster_ref": "duosis-prod-1"},
    "http_probes": [("/", "80"), ("/health", "8000")],
}


def test_probes_come_from_the_agents_summary_and_bad_entries_are_dropped() -> None:
    summary = {
        "http_probes": [
            {"container": "web", "kind": "readiness", "path": "/", "port": "80"},
            {"container": "web", "kind": "liveness", "path": "/", "port": "80"},  # same pair
            {"container": "api", "kind": "liveness", "path": "/health", "port": "8000"},
            {"container": "x", "kind": "liveness", "path": "health", "port": "80"},  # relative
            {"container": "x", "kind": "liveness", "path": "/h", "port": "99999"},  # no port
            {"container": "x", "kind": "liveness", "path": "/h\n", "port": "80"},  # control char
            {"container": "x", "kind": "liveness", "path": "/h", "port": 80},  # not a string
            "not a map",
        ]
    }
    assert probes_from_summary(summary) == [("/", "80"), ("/health", "8000")]
    assert probes_from_summary(None) == []
    assert probes_from_summary({"containers": []}) == []


def test_a_workload_never_contributes_more_than_its_bound() -> None:
    many = [{"path": f"/p{i}", "port": "80"} for i in range(100)]
    assert len(probes_from_summary({"http_probes": many})) == MAX_PROBES_PER_WORKLOAD


def test_the_parameter_is_a_typed_tuple_array_with_quotes_escaped() -> None:
    binding = {**BINDING, "http_probes": [("/a'b", "80")]}
    assert health_check_parameter([binding]) == "[('hermes-test','frontend','/a\\'b','80')]"
    assert health_check_parameter([{**BINDING, "http_probes": []}]) == "[]"


def test_a_probe_is_a_get_on_its_path_and_port_from_a_bare_address() -> None:
    # A user through the ingress is recorded by name, so it is never a probe.
    assert "isIPv4String(SpanAttributes['client.address'])" in IS_HEALTH_CHECK
    assert "IN ('GET', 'HEAD')" in IS_HEALTH_CHECK
    assert "'/metrics'" in IS_HEALTH_CHECK
    # Both generations of HTTP names, as in the request list.
    for name in ("http.request.method", "http.method", "url.path", "http.target"):
        assert f"SpanAttributes['{name}']" in IS_HEALTH_CHECK


def _trace_list():
    return build_trace_list(
        database="otel",
        binding=BINDING,
        range_key="1h",
        status="any",
        sort="recent",
        limit=10,
        now=NOW,
    )


def test_routes_and_requests_leave_health_checks_out() -> None:
    query = _trace_list()
    for statement in (query.routes, query.requests):
        assert "AND NOT (" in statement.sql
        assert "__" not in statement.sql
        assert statement.parameters["health_checks"] == (
            "[('hermes-test','frontend','/','80'),('hermes-test','frontend','/health','8000')]"
        )


def test_totals_count_them_aside_instead_of_hiding_them() -> None:
    sql = _trace_list().totals.sql
    assert "countIf(NOT (" in sql
    assert ") AS health_checks" in sql
    assert normalize_totals(
        [{"requests": "12", "errors": "1", "p95_ns": 2e8, "health_checks": "539"}]
    ) == {
        "requests": 12,
        "errors": 1,
        "error_ratio": 1 / 12,
        "p95_ms": 200.0,
        "health_checks": 539,
    }


def test_the_timeline_the_board_and_golden_signals_leave_them_out_too() -> None:
    timeline = build_timeline_query(database="otel", binding=BINDING, range_key="1h", now=NOW)
    assert "AND NOT (" in timeline.traces
    board = build_signal_query(database="otel", bindings=[BINDING], range_key="1h", now=NOW)
    assert "AND NOT (" in board.traces
    assert "health_checks" in board.parameters
    golden = build_application_query(
        database="otel",
        context={
            "namespace": "hermes-test",
            "workload_kind": "Deployment",
            "workload_name": "frontend",
            "cluster_ref": "duosis-prod-1",
            "http_probes": [("/", "80")],
        },
        window_seconds=300,
        now=NOW,
    )
    # `seen` still counts a probe: an answered probe proves the workload is
    # traced. The rate, the errors and the p95 do not.
    assert "count() AS seen" in golden.statement
    assert "AND NOT (" in golden.statement
    assert golden.parameters["health_checks"] == "[('hermes-test','frontend','/','80')]"
    for statement in (timeline.traces, board.traces, golden.statement):
        assert "__" not in statement


def test_long_lived_connections_stay_out_of_every_latency_percentile() -> None:
    """A server span longer than five minutes is a stream, not a slow
    request: every percentile leaves it out, every count keeps it."""
    long_lived = f"Duration < {LONG_LIVED_SECONDS * 1_000_000_000}"
    query = _trace_list()
    assert query.routes.sql.count(long_lived) == 2, "p50 and p95 of each route"
    assert long_lived in query.totals.sql
    assert "count" in query.totals.sql.split(long_lived)[0]
    timeline = build_timeline_query(database="otel", binding=BINDING, range_key="1h", now=NOW)
    board = build_signal_query(database="otel", bindings=[BINDING], range_key="1h", now=NOW)
    golden = build_application_query(
        database="otel",
        context={
            "namespace": "hermes-test",
            "workload_kind": "Deployment",
            "workload_name": "frontend",
            "cluster_ref": "duosis-prod-1",
            "http_probes": [("/", "80")],
        },
        window_seconds=300,
        now=NOW,
    )
    for statement in (timeline.traces, board.traces, golden.statement):
        assert f"quantileIf(0.95)(Duration, {long_lived})" in statement or (
            long_lived in statement and "quantileIf(0.95)" in statement
        )
        assert "count() AS" in statement or "countIf(" in statement
        assert "__" not in statement
