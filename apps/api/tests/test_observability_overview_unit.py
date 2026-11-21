"""The environment overview: statements, parameters, matching back.

No database and no ClickHouse. What is asserted is what would go on the
wire, that nothing from the request reaches the SQL, and which numbers the
API would attach to which binding.
"""

from datetime import UTC, datetime

import pytest
from drake_api.observability.overview import (
    LATENCY_CLASSES,
    MAX_BINDINGS,
    MAX_ROWS,
    SLOW_CLASS,
    OverviewQueryError,
    build_overview_query,
    latency_bounds,
    normalize_calls,
    normalize_codes,
    normalize_databases,
    normalize_latency,
    normalize_log_lines,
    normalize_log_patterns,
    normalize_routes,
    normalize_traffic,
)
from drake_api.observability.scope import RANGES
from drake_api.observability.signals import MAX_GROUPS
from drake_api.observability.timeline import BUCKET_SECONDS

NOW = datetime(2026, 11, 19, 12, 30, 0, tzinfo=UTC)


def binding(bid: str, name: str, *, kind: str = "Deployment", ns: str = "hermes-test"):
    return {
        "id": bid,
        "namespace": ns,
        "workload_kind": kind,
        "workload_name": name,
        "cluster": {"cluster_ref": "duosis-prod-1"},
    }


CORE = binding("b-core", "core-service")
AUTH = binding("b-auth", "auth-service")
PG = binding("b-pg", "postgres", kind="StatefulSet")
CORE_KEY = "duosis-prod-1|hermes-test|deployment|core-service"
AUTH_KEY = "duosis-prod-1|hermes-test|deployment|auth-service"
STRANGER = "duosis-prod-1|hermes-test|deployment|someone-else"


def query(*bindings, range_key="24h"):
    return build_overview_query(
        database="otel", bindings=list(bindings), range_key=range_key, now=NOW
    )


def test_nothing_from_the_request_reaches_the_sql() -> None:
    q = query(CORE, AUTH, PG)
    for sql, _ in q.statements.values():
        for value in ("duosis-prod-1", "hermes-test", "core-service", "postgres"):
            assert value not in sql
        assert "otel.otel_" in sql
        assert "__" not in sql  # every placeholder filled
    assert q.parameters["workloads"] == (
        "['duosis-prod-1|hermes-test|deployment|auth-service',"
        "'duosis-prod-1|hermes-test|deployment|core-service',"
        "'duosis-prod-1|hermes-test|statefulset|postgres']"
    )
    assert q.parameters["namespaces"] == "['hermes-test']"


def test_every_statement_narrows_to_the_authorized_workloads_in_sql() -> None:
    q = query(CORE)
    for name, (sql, _) in q.statements.items():
        assert "has({workloads:Array(String)}" in sql, name
    # Both sides of a call are narrowed before the join.
    calls, _ = q.statements["calls"]
    assert calls.count("has({workloads:Array(String)}") == 2


def test_requests_are_server_spans_without_health_checks() -> None:
    q = query(CORE)
    for name in ("traffic", "latency", "codes", "routes"):
        sql, _ = q.statements[name]
        assert "SpanKind = 'Server'" in sql
        assert "health_checks:Array(Tuple" in sql
    latency, _ = q.statements["latency"]
    assert "Duration < " in latency  # long-lived connections stay out


def test_row_ceilings_fit_the_reader_profile() -> None:
    assert MAX_ROWS <= MAX_GROUPS
    worst = max(RANGES[r] // BUCKET_SECONDS[r] for r in RANGES)
    assert MAX_BINDINGS * worst <= MAX_ROWS
    assert worst * LATENCY_CLASSES <= MAX_ROWS
    for _sql, rows in query(CORE).statements.values():
        assert rows <= MAX_ROWS


@pytest.mark.parametrize(
    ("bindings", "range_key", "message"),
    [
        ([], "24h", "no bindings"),
        ([CORE], "2d", "unsupported range"),
        ([binding(f"b{i}", f"w{i}") for i in range(MAX_BINDINGS + 1)], "24h", "too many"),
        ([binding("b", "x", kind="CronJob")], "24h", "unsupported workload kind"),
        ([binding("b", "a|b")], "24h", "unsupported name"),
    ],
)
def test_refusals(bindings, range_key, message) -> None:
    with pytest.raises(OverviewQueryError, match=message):
        build_overview_query(database="otel", bindings=bindings, range_key=range_key, now=NOW)


def test_buckets_cover_the_window_and_end_with_the_current_one() -> None:
    q = query(CORE)
    assert q.bucket_seconds == 3600
    assert len(q.bucket_starts) == 24
    assert q.bucket_starts[-1] == int(datetime(2026, 11, 19, 12, 0, tzinfo=UTC).timestamp())
    assert q.end == datetime(2026, 11, 19, 13, 0, tzinfo=UTC)


def test_traffic_fills_every_bucket_and_drops_strangers() -> None:
    q = query(CORE, AUTH)
    t0, t5 = q.bucket_starts[0], q.bucket_starts[5]
    rows = [
        {"wk": CORE_KEY, "t": str(t5), "requests": "8", "errors": "2", "p95_ns": 1.5e9},
        {"wk": STRANGER, "t": str(t0), "requests": "99", "errors": "9", "p95_ns": 1e6},
    ]
    out = {s["binding_id"]: s["buckets"] for s in normalize_traffic(rows, q)}
    assert set(out) == {"b-core", "b-auth"}
    assert out["b-core"][5] == {"t": t5, "requests": 8, "errors": 2, "p95_ms": 1500.0}
    assert out["b-core"][0] == {"t": t0, "requests": 0, "errors": 0, "p95_ms": None}
    assert all(b["requests"] == 0 for b in out["b-auth"])


def test_latency_histogram_is_dense_and_bounded() -> None:
    q = query(CORE)
    t1 = q.bucket_starts[1]
    rows = [
        {"t": str(t1), "class": 2, "requests": "100"},
        {"t": str(t1), "class": 10, "requests": "3"},
        {"t": str(t1), "class": 40, "requests": "5"},  # out of range: ignored
    ]
    out = normalize_latency(rows, q)
    assert len(out["cells"]) == 24 and len(out["cells"][0]) == LATENCY_CLASSES
    assert out["cells"][1][2] == 100 and out["cells"][1][10] == 3
    assert out["totals"][2] == 100
    assert out["slow_from_class"] == SLOW_CLASS
    bounds = latency_bounds()
    assert bounds[SLOW_CLASS]["lo_ms"] == 1000.0
    assert bounds[0]["lo_ms"] == 0.0 and bounds[-1]["hi_ms"] is None


def test_codes_routes_and_databases_attach_to_bindings() -> None:
    q = query(CORE, AUTH)
    codes = normalize_codes(
        [
            {"wk": CORE_KEY, "code": "200", "requests": "50"},
            {"wk": CORE_KEY, "code": "401", "requests": "34"},
            {"wk": CORE_KEY, "code": "", "requests": "2"},
            {"wk": STRANGER, "code": "500", "requests": "9"},
        ],
        q,
    )
    assert {c["binding_id"]: c["codes"] for c in codes}["b-core"] == {
        "200": 50,
        "401": 34,
        "unknown": 2,
    }
    routes = normalize_routes(
        [
            {
                "wk": CORE_KEY,
                "route": "GET /x",
                "requests": "7",
                "errors": "0",
                "client_errors": "1",
                "long_lived": "0",
                "p50_ns": 1e9,
                "p95_ns": 1.3e9,
                "p99_ns": 1.5e9,
            },
            {
                "wk": AUTH_KEY,
                "route": "GET /mcp",
                "requests": "3",
                "errors": "0",
                "client_errors": "0",
                "long_lived": "3",
                "p50_ns": None,
                "p95_ns": None,
                "p99_ns": None,
            },
            {
                "wk": STRANGER,
                "route": "GET /secret",
                "requests": "1",
                "errors": "0",
                "client_errors": "0",
                "long_lived": "0",
                "p50_ns": 1,
                "p95_ns": 1,
                "p99_ns": 1,
            },
        ],
        q,
    )
    assert [r["route"] for r in routes] == ["GET /x", "GET /mcp"]
    assert routes[0]["p95_ms"] == 1300.0
    assert routes[1]["p95_ms"] is None and routes[1]["long_lived"] == 3
    dbs = normalize_databases(
        [
            {
                "wk": AUTH_KEY,
                "system": "postgresql",
                "name": "",
                "operation": "connect",
                "calls": "8691",
                "errors": "0",
                "p95_ns": 1.1e8,
            }
        ],
        q,
    )
    assert dbs == [
        {
            "binding_id": "b-auth",
            "system": "postgresql",
            "database": None,
            "operation": "connect",
            "calls": 8691,
            "errors": 0,
            "p95_ms": 110.0,
        }
    ]


def test_a_call_counts_only_between_two_authorized_workloads() -> None:
    q = query(CORE, AUTH)
    out = normalize_calls(
        [
            {
                "caller": CORE_KEY,
                "callee": AUTH_KEY,
                "calls": "43",
                "errors": "0",
                "p95_ns": 1.16e9,
            },
            {"caller": STRANGER, "callee": AUTH_KEY, "calls": "5", "errors": "0", "p95_ns": 1},
            {"caller": CORE_KEY, "callee": CORE_KEY, "calls": "5", "errors": "0", "p95_ns": 1},
        ],
        q,
    )
    assert out == [
        {
            "from_binding_id": "b-core",
            "to_binding_id": "b-auth",
            "calls": 43,
            "errors": 0,
            "p95_ms": 1160.0,
        }
    ]


def test_log_lines_and_patterns() -> None:
    q = query(CORE)
    t3 = q.bucket_starts[3]
    lines = normalize_log_lines(
        [{"t": str(t3), "lines": "2300", "errors": "1200", "warnings": "0"}], q
    )
    assert lines[3]["lines"] == 2300 and lines[0]["lines"] == 0
    patterns = normalize_log_patterns(
        [
            {
                "pattern": 'FATAL: database "hermes"',
                "lines": "28782",
                "severity_number": 21,
                "severity_text": "FATAL",
                "workloads": "2",
                "wks": [CORE_KEY, STRANGER],
            }
        ],
        q,
    )
    assert patterns[0]["binding_ids"] == ["b-core"]  # the stranger is not named
    assert patterns[0]["severity_number"] == 21 and patterns[0]["workloads"] == 2


def test_dashboard_log_patterns_are_redacted() -> None:
    """The per-service patterns were redacted and these were not: a
    credential that slipped past ingest reached every dashboard viewer."""
    q = query(CORE)
    patterns = normalize_log_patterns(
        [
            {
                "pattern": "connect postgres://app:s3cr3t-pass@db:5432/app Bearer abcdefgh12345678",
                "lines": "3",
                "severity_number": 17,
                "severity_text": "ERROR",
                "workloads": "1",
                "wks": [CORE_KEY],
            }
        ],
        q,
    )
    assert "s3cr3t-pass" not in patterns[0]["pattern"]
    assert "abcdefgh12345678" not in patterns[0]["pattern"]


def test_a_malformed_row_is_refused() -> None:
    q = query(CORE)
    with pytest.raises(ValueError):
        normalize_traffic([["not", "a", "dict"]], q)  # type: ignore[list-item]
    with pytest.raises(ValueError):
        normalize_codes([{"wk": CORE_KEY, "code": "200", "requests": "-1"}], q)
