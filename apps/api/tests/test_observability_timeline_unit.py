"""The timeline strip's read, and the window that narrows logs and traces.

No database and no ClickHouse: what would go on the wire, and what the API
would hand back.
"""

from datetime import UTC, datetime, timedelta

import pytest
from drake_api.observability.logs import LogQueryError, build_log_query
from drake_api.observability.scope import RANGES, narrow_window
from drake_api.observability.store import StoreContractError
from drake_api.observability.timeline import (
    BUCKET_SECONDS,
    MAX_BUCKETS,
    TimelineQueryError,
    bucket_frame,
    build_timeline_query,
    normalize_logs,
    normalize_traces,
)
from drake_api.observability.traces import TraceQueryError, build_trace_list

NOW = datetime(2026, 11, 15, 12, 3, 20, tzinfo=UTC)
BINDING = {
    "namespace": "hermes-dev",
    "workload_kind": "Deployment",
    "workload_name": "core-service",
    "cluster": {"cluster_ref": "duosis-prod-1"},
}


def _query(range_key: str = "1h"):
    return build_timeline_query(database="otel", binding=BINDING, range_key=range_key, now=NOW)


def test_every_range_has_a_bucket_width_that_divides_it() -> None:
    assert set(BUCKET_SECONDS) == set(RANGES)
    for key, seconds in RANGES.items():
        assert seconds % BUCKET_SECONDS[key] == 0
        assert seconds // BUCKET_SECONDS[key] <= MAX_BUCKETS


def test_buckets_are_epoch_aligned_and_end_with_the_current_one() -> None:
    query = _query("1h")
    assert query.bucket_seconds == 150
    assert len(query.bucket_starts) == 24
    assert all(start % 150 == 0 for start in query.bucket_starts)
    current = int(NOW.timestamp()) - int(NOW.timestamp()) % 150
    assert query.bucket_starts[-1] == current
    assert query.end == datetime.fromtimestamp(current + 150, UTC)
    frame = bucket_frame(query, NOW)
    assert frame[-1]["partial"] is True
    assert all(entry["partial"] is False for entry in frame[:-1])


def test_statements_are_scoped_by_parameters_only() -> None:
    query = _query()
    for sql in (query.traces, query.logs):
        for value in ("duosis-prod-1", "hermes-dev", "core-service"):
            assert value not in sql
        assert "__SCOPE__" not in sql and "__DATABASE__" not in sql
    assert "SpanKind = 'Server'" in query.traces
    # A called service's server span has a parent in its caller (ADR-0029).
    assert "ParentSpanId" not in query.traces
    assert query.parameters["workload"] == "core-service"
    assert query.parameters["bucket"] == "150"


def test_refuses_what_it_cannot_express() -> None:
    with pytest.raises(TimelineQueryError):
        build_timeline_query(database="otel", binding=BINDING, range_key="7d", now=NOW)
    with pytest.raises(TimelineQueryError):
        build_timeline_query(
            database="otel",
            binding={**BINDING, "workload_kind": "CronJob"},
            range_key="1h",
            now=NOW,
        )
    with pytest.raises(StoreContractError):
        build_timeline_query(database="otel;x", binding=BINDING, range_key="1h", now=NOW)


def test_missing_buckets_are_zero_requests_with_no_ratio_and_no_p95() -> None:
    query = _query()
    first, second = query.bucket_starts[0], query.bucket_starts[1]
    rows = [
        {"t": first, "requests": "40", "errors": "4", "p95_ns": 12_500_000.0},
        {"t": str(second), "requests": "0", "errors": "0", "p95_ns": float("nan")},
        {"t": 12345, "requests": "999", "errors": "999", "p95_ns": 1.0},
    ]
    out = normalize_traces(rows, query)
    assert len(out) == 24
    assert out[0] == {"requests": 40, "errors": 4, "error_ratio": 0.1, "p95_ms": 12.5}
    assert out[1] == {"requests": 0, "errors": 0, "error_ratio": None, "p95_ms": None}
    assert out[2] == {"requests": 0, "errors": 0, "error_ratio": None, "p95_ms": None}
    # A row outside the frame is dropped, never shifted into it.
    assert sum(entry["requests"] for entry in out) == 40


def test_log_lanes_count_errors_and_warnings() -> None:
    query = _query()
    rows = [{"t": query.bucket_starts[-1], "lines": "10", "errors": "2", "warnings": "3"}]
    out = normalize_logs(rows, query)
    assert out[-1] == {"lines": 10, "errors": 2, "warnings": 3}
    assert out[0] == {"lines": 0, "errors": 0, "warnings": 0}


@pytest.mark.parametrize("bad", ["-1", "1.5", True, None])
def test_malformed_counts_are_refused(bad) -> None:
    query = _query()
    with pytest.raises(ValueError):
        normalize_traces([{"t": query.bucket_starts[0], "requests": bad, "errors": "0"}], query)


# --- the window ---------------------------------------------------------------


def test_a_window_narrows_and_never_widens() -> None:
    start, end = NOW - timedelta(hours=1), NOW
    inside = (NOW - timedelta(minutes=30), NOW - timedelta(minutes=20))
    assert narrow_window(start, end, *inside) == inside
    wider = (NOW - timedelta(hours=5), NOW + timedelta(hours=5))
    assert narrow_window(start, end, *wider) == (start, end)
    with pytest.raises(ValueError):
        narrow_window(start, end, NOW - timedelta(hours=3), NOW - timedelta(hours=2))
    with pytest.raises(ValueError):
        narrow_window(start, end, NOW - timedelta(minutes=5), NOW - timedelta(minutes=10))


def test_logs_and_traces_read_the_window_they_were_given() -> None:
    w = (NOW - timedelta(minutes=30), NOW - timedelta(minutes=25))
    logs = build_log_query(
        database="otel",
        binding=BINDING,
        range_key="1h",
        severity="any",
        term="",
        limit=100,
        before=None,
        now=NOW,
        window_start=w[0],
        window_end=w[1],
    )
    assert (logs.start, logs.end) == w
    traces = build_trace_list(
        database="otel",
        binding=BINDING,
        range_key="1h",
        status="any",
        sort="recent",
        limit=50,
        now=NOW,
        window_start=w[0],
        window_end=w[1],
    )
    assert (traces.start, traces.end) == w
    assert traces.requests.parameters["start"] == logs.parameters["start"]


def test_a_window_outside_the_range_is_refused_by_both_reads() -> None:
    w = (NOW - timedelta(hours=3), NOW - timedelta(hours=2))
    with pytest.raises(LogQueryError):
        build_log_query(
            database="otel",
            binding=BINDING,
            range_key="1h",
            severity="any",
            term="",
            limit=100,
            before=None,
            now=NOW,
            window_start=w[0],
            window_end=w[1],
        )
    with pytest.raises(TraceQueryError):
        build_trace_list(
            database="otel",
            binding=BINDING,
            range_key="1h",
            status="any",
            sort="recent",
            limit=50,
            now=NOW,
            window_start=w[0],
            window_end=w[1],
        )
