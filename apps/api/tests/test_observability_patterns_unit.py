"""Log patterns: the statement, its parameters, and what the API hands back.

No database and no ClickHouse.
"""

from datetime import UTC, datetime, timedelta

import pytest
from drake_api.observability.logs import LogQueryError
from drake_api.observability.patterns import (
    MAX_PATTERNS,
    build_pattern_query,
    normalize_patterns,
)

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
BINDING = {
    "namespace": "hermes-dev",
    "workload_kind": "Deployment",
    "workload_name": "core-service",
    "cluster": {"cluster_ref": "duosis-prod-1"},
}


def _query(**overrides):
    values = {
        "database": "otel",
        "binding": BINDING,
        "range_key": "1h",
        "severity": "any",
        "term": "",
        "now": NOW,
    }
    values.update(overrides)
    return build_pattern_query(**values)


def test_the_statement_groups_in_the_store_and_is_scoped_by_parameters() -> None:
    query = _query()
    assert "GROUP BY pattern" in query.statement
    for value in ("duosis-prod-1", "hermes-dev", "core-service"):
        assert value not in query.statement
    assert "__SCOPE__" not in query.statement and "__DATABASE__" not in query.statement
    assert query.parameters["workload"] == "core-service"
    assert query.max_rows == MAX_PATTERNS


def test_regexes_travel_as_parameters_with_backslashes_doubled() -> None:
    """ClickHouse reads String parameters in its escaped format; a single
    `\\b` would arrive as a backspace and silently break word boundaries."""
    query = _query()
    for name in ("re_time", "re_uuid", "re_ip", "re_hex", "re_num"):
        assert f"{{{name}:String}}" in query.statement
        assert name in query.parameters
    assert query.parameters["re_ip"].startswith("\\\\b")
    assert "\\\\d" in query.parameters["re_num"]
    # No regex text is spliced into the SQL itself.
    assert "\\d" not in query.statement


def test_the_log_reads_rules_apply_unchanged() -> None:
    with pytest.raises(LogQueryError):
        _query(range_key="7d")
    with pytest.raises(LogQueryError):
        _query(severity="loud")
    with pytest.raises(LogQueryError):
        _query(term="a\x00b")
    window = (NOW - timedelta(minutes=30), NOW - timedelta(minutes=20))
    narrowed = _query(window_start=window[0], window_end=window[1])
    assert (narrowed.start, narrowed.end) == window
    assert narrowed.parameters["min_severity"] == "0"
    assert _query(severity="error").parameters["min_severity"] == "17"


def test_rows_become_patterns_and_are_redacted_again() -> None:
    rows = [
        {
            "pattern": "login failed password=<n>",
            "lines": "118",
            "severity_number": 17,
            "severity_text": "ERROR",
            "first_ts": "2026-11-15 11:05:00.000000000",
            "last_ts": "2026-11-15 11:59:00.123456789",
            "latest": "login failed password=hunter2 for user 7",
            "earliest": "login failed password=letmein for user 3",
        }
    ]
    [pattern] = normalize_patterns(rows)
    assert pattern["lines"] == 118
    assert pattern["severity"] == "ERROR"
    assert pattern["last_at"] == "2026-11-15T11:59:00.123456+00:00"
    assert "hunter2" not in pattern["latest"]
    assert "letmein" not in pattern["earliest"]


@pytest.mark.parametrize(
    "bad",
    [
        {"pattern": None},
        {"lines": "-1"},
        {"severity_number": "17"},
        {"last_ts": 5},
    ],
)
def test_malformed_rows_are_refused(bad) -> None:
    row = {
        "pattern": "x",
        "lines": "1",
        "severity_number": 9,
        "severity_text": "INFO",
        "first_ts": "2026-11-15 11:05:00.000000000",
        "last_ts": "2026-11-15 11:05:00.000000000",
        "latest": "x",
        "earliest": "x",
        **bad,
    }
    with pytest.raises(ValueError):
        normalize_patterns([row])
