"""What needs attention: the detectors, on numbers like production's.

Pure functions over overview sections. The cases are the four things found
by hand in hermes-test on 2026-11-18, and the near-misses that must not
become findings.
"""

from drake_api.observability.findings import (
    client_errors,
    connection_per_query,
    find,
    periodic_error_logs,
    slow_calls,
)


def ok(data):
    return {"state": "ok", "data": data}


def buckets(errors):
    return [{"t": i, "lines": e * 2, "errors": e, "warnings": 0} for i, e in enumerate(errors)]


FATAL = {
    "pattern": 'FATAL:  database "hermes" does not exist',
    "lines": 28782,
    "severity_number": 21,
    "severity_text": "FATAL",
    "workloads": 1,
    "binding_ids": ["auth-db"],
}
INFO = {
    "pattern": "GET /ready <n>",
    "lines": 25114,
    "severity_number": 9,
    "severity_text": "INFO",
    "workloads": 2,
}


def test_steady_error_lines_are_periodic() -> None:
    out = periodic_error_logs(buckets([1200, 1199, 1201, 1200, 1198, 1200, 300]), [INFO, FATAL])
    [finding] = out
    assert finding["kind"] == "periodic_error_logs"
    assert finding["metrics"]["pattern"].startswith("FATAL")
    assert finding["metrics"]["lines"] == 28782
    assert finding["binding_ids"] == ["auth-db"]
    # The still-filling newest bucket does not break steadiness.
    assert finding["metrics"]["coverage"] == 1.0


def test_a_line_read_as_an_error_since_mid_window_is_periodic_since_then() -> None:
    # Production on 2026-11-19: PostgreSQL's FATAL lines only carried a
    # severity after #149 shipped, six hours into the window.
    errors = [0] * 6 + [585] + [1200, 1199, 1200, 1198] * 4 + [548]
    [finding] = periodic_error_logs(buckets(errors), [FATAL])
    assert finding["metrics"]["since"] == 6
    assert finding["metrics"]["coverage"] == 1.0


def test_too_short_a_history_is_not_a_period() -> None:
    assert periodic_error_logs(buckets([0] * 20 + [1200, 1200, 1200, 600]), [FATAL]) == []


def test_a_burst_is_not_periodic() -> None:
    assert periodic_error_logs(buckets([0, 0, 0, 5000, 0, 0, 0]), [FATAL]) == []
    assert periodic_error_logs(buckets([10, 400, 20, 900, 15, 700, 5]), [FATAL]) == []


def test_steady_info_lines_are_not_errors() -> None:
    assert periodic_error_logs(buckets([1200] * 8), [INFO]) == []
    assert periodic_error_logs(buckets([0] * 8), [FATAL]) == []


def db(binding, operation, calls, p95=50.0, database="auth_db"):
    return {
        "binding_id": binding,
        "system": "postgresql",
        "database": database,
        "operation": operation,
        "calls": calls,
        "errors": 0,
        "p95_ms": p95,
    }


def test_a_connection_for_every_other_query_is_a_missing_pool() -> None:
    out = connection_per_query(
        [db("auth", "SELECT auth_db", 20758), db("auth", "connect", 9425, p95=92.5)]
    )
    [finding] = out
    assert finding["binding_ids"] == ["auth"]
    assert finding["metrics"]["ratio"] == round(9425 / 20758, 3)
    assert finding["metrics"]["connect_p95_ms"] == 92.5


def test_a_pooled_service_is_not_a_finding() -> None:
    assert (
        connection_per_query([db("core", "SELECT core_db", 50000), db("core", "connect", 40)]) == []
    )
    assert connection_per_query([db("core", "connect", 500)]) == []  # no queries: no ratio


def call(frm, to, calls, p95):
    return {"from_binding_id": frm, "to_binding_id": to, "calls": calls, "errors": 0, "p95_ms": p95}


def test_a_slow_call_names_a_likely_cause_only_when_one_was_found() -> None:
    [linked] = slow_calls([call("core", "auth", 959, 1160.0)], pooled={"auth"})
    assert linked["binding_ids"] == ["core", "auth"]
    assert linked["metrics"]["likely_cause"] == "connection_per_query"
    [plain] = slow_calls([call("core", "auth", 959, 1160.0)])
    assert plain["metrics"]["likely_cause"] is None


def test_fast_or_rare_calls_are_not_findings() -> None:
    assert slow_calls([call("core", "auth", 959, 513.5)]) == []
    assert slow_calls([call("mcp", "core", 3, 5000.0)]) == []
    assert slow_calls([call("a", "b", 50, None)]) == []


def test_auth_failures_are_information_other_4xx_a_warning() -> None:
    out = client_errors(
        [
            {"binding_id": "core", "codes": {"200": 889, "401": 94}},
            {"binding_id": "fe", "codes": {"200": 300, "404": 28, "405": 22}},
            {"binding_id": "quiet", "codes": {"200": 13118, "405": 12}},
        ]
    )
    by = {f["binding_ids"][0]: f for f in out}
    assert set(by) == {"core", "fe"}
    assert by["core"]["severity"] == "info" and by["core"]["metrics"]["auth_only"]
    assert by["fe"]["severity"] == "warning"
    assert by["fe"]["metrics"]["codes"] == {"404": 28, "405": 22}


def test_find_runs_what_answered_and_says_what_it_skipped() -> None:
    sections = {
        "log_lines": ok(buckets([1200] * 8)),
        "log_patterns": ok([FATAL]),
        "databases": ok([db("auth", "SELECT auth_db", 20758), db("auth", "connect", 9425)]),
        "calls": ok([call("core", "auth", 959, 1160.0)]),
        "codes": {"state": "unavailable", "reason": "store_upstream_error"},
    }
    result = find(sections)
    kinds = [f["kind"] for f in result["items"]]
    assert kinds == ["periodic_error_logs", "connection_per_query", "slow_call"]
    assert result["skipped"] == ["client_errors"]
    slow = result["items"][2]
    assert slow["metrics"]["likely_cause"] == "connection_per_query"


def test_a_caller_without_trace_access_gets_no_trace_findings() -> None:
    sections = {
        "log_lines": ok(None),
        "log_patterns": ok(None),
        "databases": {"state": "not_configured"},
        "calls": {"state": "not_configured"},
        "codes": {"state": "not_configured"},
    }
    result = find(sections)
    assert result["items"] == []
    assert set(result["skipped"]) == {
        "periodic_error_logs",
        "connection_per_query",
        "slow_call",
        "client_errors",
    }


def test_warnings_come_before_information() -> None:
    result = find(
        {
            "codes": ok([{"binding_id": "core", "codes": {"200": 889, "401": 94}}]),
            "databases": ok([db("auth", "SELECT auth_db", 1000), db("auth", "connect", 900)]),
        }
    )
    assert [f["severity"] for f in result["items"]] == ["warning", "info"]
