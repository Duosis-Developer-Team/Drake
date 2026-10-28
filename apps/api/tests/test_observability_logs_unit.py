"""ADR-0029 log read path: query compilation, the store adapter, settings.

No database, no ClickHouse: the store is exercised through an httpx mock
transport, so what is asserted is exactly what would go on the wire.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from drake_api.observability.logs import (
    LOG_RANGES,
    MAX_LIMIT,
    LogQueryError,
    build_log_query,
    next_cursor,
    normalize_rows,
)
from drake_api.observability.store import (
    ClickHouseStore,
    StoreContractError,
    StoreUnavailableError,
    safe_identifier,
)
from drake_api.rbac.catalog import PERMISSIONS, ROLE_TEMPLATES
from drake_api.settings import ObservabilityStoreConnector
from drake_api.testing import make_settings

NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)

BINDING = {
    "id": "00000000-0000-0000-0000-000000000001",
    "namespace": "alpha-prod",
    "workload_kind": "Deployment",
    "workload_name": "alpha-api",
    "cluster": {"cluster_ref": "local-fixture", "id": "c1"},
}


def _run(coro):
    return asyncio.run(coro)


def _query(**overrides):
    values = {
        "database": "otel",
        "binding": BINDING,
        "range_key": "1h",
        "severity": "any",
        "term": "",
        "limit": 100,
        "before": None,
        "now": NOW,
    }
    values.update(overrides)
    return build_log_query(**values)


# --- query compilation --------------------------------------------------------


def test_scope_comes_from_the_binding_and_values_stay_out_of_the_statement() -> None:
    query = _query(term="timeout")
    assert query.parameters["cluster"] == "local-fixture"
    assert query.parameters["namespace"] == "alpha-prod"
    assert query.parameters["workload"] == "alpha-api"
    assert query.parameters["workload_attribute"] == "k8s.deployment.name"
    assert query.parameters["term"] == "timeout"
    # None of the values is in the SQL text; only typed placeholders are.
    for value in ("local-fixture", "alpha-prod", "alpha-api", "timeout"):
        assert value not in query.statement
    assert "{namespace:String}" in query.statement
    assert "FROM otel.otel_logs" in query.statement


def test_a_hostile_term_is_only_ever_a_parameter() -> None:
    term = "' OR 1=1 --"
    query = _query(term=term)
    assert query.parameters["term"] == term
    assert term not in query.statement


@pytest.mark.parametrize(
    ("kind", "attribute"),
    [
        ("Deployment", "k8s.deployment.name"),
        ("StatefulSet", "k8s.statefulset.name"),
        ("DaemonSet", "k8s.daemonset.name"),
    ],
)
def test_workload_kind_decides_the_attribute_server_side(kind: str, attribute: str) -> None:
    query = _query(binding={**BINDING, "workload_kind": kind})
    assert query.parameters["workload_attribute"] == attribute


def test_unknown_workload_kind_is_refused() -> None:
    with pytest.raises(LogQueryError):
        _query(binding={**BINDING, "workload_kind": "CronJob"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"range_key": "30d"},
        {"severity": "critical"},
        {"limit": 0},
        {"limit": MAX_LIMIT + 1},
        {"term": "x" * 101},
        {"term": "line\nbreak"},
        {"term": "nul\x00"},
    ],
)
def test_out_of_contract_requests_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(LogQueryError):
        _query(**overrides)


def test_database_name_must_be_an_identifier() -> None:
    assert safe_identifier("otel") == "otel"
    for hostile in ("otel; DROP TABLE x", "otel.logs", "", "1otel", "o" * 64):
        with pytest.raises(StoreContractError):
            safe_identifier(hostile)


def test_window_is_one_of_the_fixed_ranges() -> None:
    for key, seconds in LOG_RANGES.items():
        query = _query(range_key=key)
        assert query.end - query.start == timedelta(seconds=seconds)
        assert query.end == NOW


def test_severity_maps_to_otel_severity_bands() -> None:
    assert _query(severity="any").parameters["min_severity"] == "0"
    assert _query(severity="warn").parameters["min_severity"] == "13"
    assert _query(severity="error").parameters["min_severity"] == "17"


def test_cursor_narrows_the_window_and_keeps_the_boundary_microsecond() -> None:
    before = NOW - timedelta(minutes=10)
    query = _query(before=before)
    assert query.end == before + timedelta(microseconds=1)
    assert query.start == NOW - timedelta(hours=1)


def test_cursor_outside_the_range_is_refused() -> None:
    with pytest.raises(LogQueryError):
        _query(before=NOW - timedelta(hours=2))


# --- normalization ------------------------------------------------------------


def _row(**overrides):
    row = {
        "ts": "2026-10-28 11:59:58.123456789",
        "severity_text": "ERROR",
        "severity_number": 17,
        "body": "POST /v1/payments 502",
        "trace_id": "",
        "span_id": "",
        "pod": "alpha-api-1",
        "container": "api",
    }
    row.update(overrides)
    return row


def test_rows_normalize_and_absent_fields_become_null() -> None:
    [entry] = normalize_rows([_row()])
    assert entry["timestamp"] == "2026-10-28T11:59:58.123456+00:00"
    assert entry["severity"] == "ERROR"
    assert entry["trace_id"] is None
    assert entry["pod"] == "alpha-api-1"


def test_bodies_are_redacted_again_at_read_time() -> None:
    secret_line = "login " + "pass" + "word=" + "s3cr3t-value"
    [entry] = normalize_rows([_row(body=secret_line)])
    assert "s3cr3t-value" not in entry["body"]
    assert "[REDACTED]" in entry["body"]


def test_malformed_rows_are_refused_not_guessed() -> None:
    with pytest.raises(ValueError):
        normalize_rows([_row(severity_number="17")])
    with pytest.raises(ValueError):
        normalize_rows([_row(body=None)])


def test_next_cursor_only_when_the_page_is_full() -> None:
    entries = normalize_rows([_row(), _row(ts="2026-10-28 11:59:57.000000000")])
    assert next_cursor(entries, limit=3) is None
    assert next_cursor(entries, limit=2) == entries[-1]["timestamp"]


# --- store adapter ------------------------------------------------------------


def _settings(tmp_path: Path, **connector: object):
    password_file = tmp_path / "reader-password"
    password_file.write_text("local-test-reader\n", encoding="utf-8")
    values: dict[str, object] = {
        "url": "http://127.0.0.1:58123",
        "username": "drake_reader",
        "password_file": str(password_file),
    }
    values.update(connector)
    return make_settings(
        env="test",
        observability_logs_enabled=True,
        observability_store=ObservabilityStoreConnector(**values),  # type: ignore[arg-type]
    )


def _store_query(settings, handler) -> object:
    store = ClickHouseStore(settings, transport=httpx.MockTransport(handler))
    query = _query()
    return _run(
        store.query(
            query.statement,
            query.parameters,
            max_rows=query.limit,
            timeout_seconds=5.0,
            correlation_id="test",
        )
    )


def test_parameters_and_credentials_go_on_the_wire_as_designed(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"data": [_row()]})

    result = _store_query(_settings(tmp_path), handler)
    params = seen["params"]
    assert isinstance(params, dict)
    assert params["param_namespace"] == "alpha-prod"
    assert params["result_overflow_mode"] == "throw"
    assert params["max_result_rows"] == "100"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["x-clickhouse-user"] == "drake_reader"
    # Read from the file at query time, trimmed; never a settings value.
    assert headers["x-clickhouse-key"] == "local-test-reader"
    assert "alpha-prod" not in str(seen["body"])
    assert len(result.rows) == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("status", "error", "code"),
    [
        (500, StoreUnavailableError, "store_upstream_error"),
        (401, StoreUnavailableError, "store_auth_refused"),
        (400, StoreContractError, "store_rejected_query"),
        (302, StoreContractError, "store_redirect_refused"),
    ],
)
def test_upstream_failures_become_bounded_codes(
    tmp_path: Path, status: int, error: type[Exception], code: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # A body that quotes the statement must never reach the caller.
        return httpx.Response(status, text="Code: 62. Syntax error near SELECT secret")

    with pytest.raises(error, match=code):
        _store_query(_settings(tmp_path), handler)


def test_more_rows_than_asked_for_is_a_contract_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [_row()] * 101})

    with pytest.raises(StoreContractError, match="store_too_many_rows"):
        _store_query(_settings(tmp_path), handler)


def test_malformed_json_is_refused(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    with pytest.raises(StoreContractError, match="store_malformed_response"):
        _store_query(_settings(tmp_path), handler)


def test_unreadable_password_file_is_unavailable_without_naming_the_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path, password_file=str(tmp_path / "missing"))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent without the credential")

    with pytest.raises(StoreUnavailableError) as raised:
        _store_query(settings, handler)
    assert raised.value.code == "store_credential_unreadable"
    assert "missing" not in str(raised.value)


def test_unconfigured_store_issues_no_request() -> None:
    settings = make_settings(env="test")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    store = ClickHouseStore(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(StoreUnavailableError, match="store_not_configured"):
        _run(store.query("SELECT 1", {}, max_rows=1, timeout_seconds=1, correlation_id="t"))


# --- settings and permission catalog -------------------------------------------


def test_production_refuses_an_enabled_store_without_a_credential_reference() -> None:
    with pytest.raises(RuntimeError, match="username and a password file"):
        make_settings(
            env="production",
            observability_logs_enabled=True,
            observability_store=ObservabilityStoreConnector(url="http://10.0.0.5:8123"),
        ).validate_runtime_security()


def test_production_refuses_enabled_logs_without_a_store() -> None:
    with pytest.raises(RuntimeError, match="no observability store"):
        make_settings(env="production", observability_logs_enabled=True).validate_runtime_security()


def test_logs_are_off_by_default() -> None:
    settings = make_settings(env="production")
    assert settings.observability_logs_enabled is False
    assert settings.observability_store is None
    settings.validate_runtime_security()


def test_logs_view_is_a_distinct_permission_held_only_by_the_owner_template() -> None:
    assert "logs.view" in PERMISSIONS
    holders = [name for name, (_, keys) in ROLE_TEMPLATES.items() if "logs.view" in keys]
    assert holders == ["Platform Owner"]
