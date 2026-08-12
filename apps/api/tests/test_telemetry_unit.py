"""Telemetry unit tests: registry fail-closed, compiler safety, budgets,
normalization honesty, adapter/SSRF boundary, internal metrics hygiene."""

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from drake_api.settings import TelemetryConnector
from drake_api.telemetry.budgets import BudgetError, resolve_range
from drake_api.telemetry.compiler import CompileError, compile_query, escape_label_value
from drake_api.telemetry.metrics import BrokerMetrics
from drake_api.telemetry.normalize import normalize_matrix
from drake_api.telemetry.provider import (
    ConnectorRefusedError,
    PrometheusAdapter,
    ProviderContractError,
    ProviderUnavailableError,
    RangeQueryResult,
    validate_connector,
)
from drake_api.telemetry.registry import (
    REGISTRY_DIR,
    RegistryError,
    find_template,
    load_registry,
)
from drake_api.testing import make_settings

# --- registry ----------------------------------------------------------------


def test_authoritative_registry_loads_and_hashes() -> None:
    registry = load_registry()
    # Counts move whenever the curated registry grows; they are asserted so
    # that a template or metric cannot be added without someone noticing.
    # +5 metrics and +5 templates in Sprint 13I: cluster CPU, memory and
    # filesystem capacity, the first cluster-scope entries in the registry.
    # +3 templates in Sprint 13F.6: environment-scope CPU, memory and
    # restarts. The metric count was UNCHANGED for those because they reuse
    # the workload metrics that already existed — the gap was never a missing
    # measurement, it was that the environment scope could not ask for one.
    #
    # +3 metrics and +3 templates for the failure signals: why a container is
    # not running, replicas missing, pods not ready. These ARE new
    # measurements — Prometheus held them and nothing in Drake read them.
    assert len(registry.metrics) == 27
    assert len(registry.templates) == 33
    # +1 board: cluster capacity, the first cluster-scope dashboard.
    assert len(registry.dashboards) == 3
    assert len(registry.content_hash) == 64
    assert registry.content_hash == load_registry().content_hash  # deterministic


def _mutated_registry(tmp_path: Path, file: str, mutate) -> Path:
    for name in ("metric-catalog.json", "query-templates.json", "dashboard-templates.json"):
        shutil.copy(REGISTRY_DIR / name, tmp_path / name)
    document = json.loads((tmp_path / file).read_text())
    mutate(document)
    (tmp_path / file).write_text(json.dumps(document))
    return tmp_path


def test_registry_rejects_duplicates(tmp_path: Path) -> None:
    def mutate(doc):
        doc["metrics"].append(doc["metrics"][0])

    base = _mutated_registry(tmp_path, "metric-catalog.json", mutate)
    with pytest.raises(RegistryError, match=r"duplicate|sorted"):
        load_registry(base)


def test_registry_rejects_unknown_metric_reference(tmp_path: Path) -> None:
    def mutate(doc):
        doc["templates"][0]["metricKey"] = "ghost.metric"

    base = _mutated_registry(tmp_path, "query-templates.json", mutate)
    with pytest.raises(RegistryError, match="unknown metric"):
        load_registry(base)


def test_registry_rejects_route_label(tmp_path: Path) -> None:
    def mutate(doc):
        doc["metrics"][0]["allowedInputLabels"].append("route")

    base = _mutated_registry(tmp_path, "metric-catalog.json", mutate)
    with pytest.raises(RegistryError, match="forbidden label 'route'"):
        load_registry(base)


def test_registry_rejects_budget_above_global_ceiling(tmp_path: Path) -> None:
    def mutate(doc):
        doc["templates"][0]["budgets"]["maxRangeSeconds"] = 90 * 24 * 3600

    base = _mutated_registry(tmp_path, "query-templates.json", mutate)
    with pytest.raises(RegistryError, match="global ceiling"):
        load_registry(base)


def test_registry_rejects_unsorted_entries(tmp_path: Path) -> None:
    def mutate(doc):
        doc["metrics"].reverse()

    base = _mutated_registry(tmp_path, "metric-catalog.json", mutate)
    with pytest.raises(RegistryError, match="not sorted"):
        load_registry(base)


def test_registry_rejects_snapshot_backed_template(tmp_path: Path) -> None:
    def mutate(doc):
        doc["templates"][0]["metricKey"] = "tenant.storage.logical_bytes"
        doc["templates"][0]["metricVersion"] = 1

    base = _mutated_registry(tmp_path, "query-templates.json", mutate)
    with pytest.raises(RegistryError, match="snapshot"):
        load_registry(base)


def test_registry_rejects_malformed_json(tmp_path: Path) -> None:
    for name in ("metric-catalog.json", "query-templates.json", "dashboard-templates.json"):
        shutil.copy(REGISTRY_DIR / name, tmp_path / name)
    (tmp_path / "query-templates.json").write_text("{not json")
    with pytest.raises(RegistryError, match="unparseable"):
        load_registry(tmp_path)


# --- compiler ----------------------------------------------------------------


def _template():
    return find_template(load_registry(), "service.request-rate.v1")


def test_compiler_escapes_label_values() -> None:
    assert escape_label_value('a"b\\c') == 'a\\"b\\\\c'
    with pytest.raises(CompileError):
        escape_label_value("evil\nvalue")


def test_compiler_produces_deterministic_parseable_query() -> None:
    template = _template()
    values = {"project_key": "alpha", "environment_key": "dev", "service_key": "api"}
    first = compile_query(template, values, {}, 60)
    second = compile_query(template, values, {}, 60)
    assert first == second
    assert 'project="alpha"' in first.query
    assert "{{" not in first.query
    assert first.window_seconds == 240


def test_compiler_neutralizes_injection_attempts() -> None:
    template = _template()
    values = {
        "project_key": 'alpha"} or on(instance) up{x="y',
        "environment_key": "dev",
        "service_key": "api",
    }
    compiled = compile_query(template, values, {}, 60)
    # The quote is escaped — the value cannot terminate the matcher string.
    assert '\\"' in compiled.query
    assert 'or on(instance) up{x=\\"y"' in compiled.query


def test_compiler_rejects_unknown_parameters_and_missing_sources() -> None:
    template = _template()
    values = {"project_key": "alpha", "environment_key": "dev", "service_key": "api"}
    with pytest.raises(CompileError, match="unknown parameter"):
        compile_query(template, values, {"promql": "up"}, 60)
    with pytest.raises(CompileError, match="matcher sources"):
        compile_query(template, {"project_key": "alpha"}, {}, 60)


# --- budgets -----------------------------------------------------------------


def test_range_budget_rejects_huge_range() -> None:
    template = _template()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(BudgetError, match="exceeds"):
        resolve_range(template, start, datetime(2026, 1, 20, tzinfo=UTC), 60)


def test_tiny_step_is_adjusted_not_unbounded() -> None:
    template = _template()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 6, tzinfo=UTC)  # 5 days
    effective = resolve_range(template, start, end, 1)
    assert effective.step_adjusted
    assert effective.effective_step_seconds >= template.min_step_seconds
    points = (effective.to_ts - effective.from_ts) // effective.effective_step_seconds + 1
    assert points <= template.max_points


def test_inverted_range_rejected() -> None:
    template = _template()
    start = datetime(2026, 1, 2, tzinfo=UTC)
    with pytest.raises(BudgetError, match="after"):
        resolve_range(template, start, datetime(2026, 1, 1, tzinfo=UTC), 60)


# --- normalization -----------------------------------------------------------


def test_non_finite_values_become_null_partial_never_zero() -> None:
    template = _template()
    raw = RangeQueryResult(
        result=[{"metric": {}, "values": [[100, "1.5"], [160, "NaN"], [220, "+Inf"]]}]
    )
    series, partial, warnings = normalize_matrix(template, raw)
    assert series[0]["points"] == [[100, 1.5], [160, None], [220, None]]
    assert partial is True
    assert warnings == ["non_finite_values"]


def test_unexpected_label_is_fail_closed() -> None:
    template = _template()  # output labels: []
    raw = RangeQueryResult(result=[{"metric": {"pod_name": "x-123"}, "values": [[1, "1"]]}])
    with pytest.raises(ProviderContractError, match="unexpected_series_label"):
        normalize_matrix(template, raw)


def test_series_and_point_budgets_fail_closed() -> None:
    template = _template()
    too_many_series = RangeQueryResult(
        result=[{"metric": {}, "values": [[1, "1"]]} for _ in range(template.max_series + 1)]
    )
    with pytest.raises(ProviderContractError, match="series_budget_exceeded"):
        normalize_matrix(template, too_many_series)
    too_many_points = RangeQueryResult(
        result=[{"metric": {}, "values": [[i, "1"] for i in range(template.max_points + 1)]}]
    )
    with pytest.raises(ProviderContractError, match="point_budget_exceeded"):
        normalize_matrix(template, too_many_points)


def test_series_ordering_is_deterministic() -> None:
    registry = load_registry()
    template = find_template(registry, "environment.request-rate.v1")
    raw = RangeQueryResult(
        result=[
            {"metric": {"service": "web"}, "values": [[2, "1"], [1, "2"]]},
            {"metric": {"service": "api"}, "values": [[1, "3"]]},
        ]
    )
    series, _, _ = normalize_matrix(template, raw)
    assert [s["labels"]["service"] for s in series] == ["api", "web"]
    assert series[1]["points"] == [[1, 2.0], [2, 1.0]]  # sorted by timestamp


# --- provider / SSRF ---------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",  # cloud metadata (link-local)
        "http://224.0.0.1:9090",  # multicast
        "http://0.0.0.0:9090",  # unspecified
        "ftp://example.test",  # scheme
        "http://user:pass@example.test",  # embedded credentials
    ],
)
def test_ssrf_targets_always_refused(url: str) -> None:
    settings = make_settings(env="test")
    with pytest.raises(ConnectorRefusedError):
        _run(validate_connector(TelemetryConnector(url=url), settings))


def test_plaintext_and_loopback_refused_outside_local_test() -> None:
    settings = make_settings(env="prod", oidc_issuer="https://issuer.example")
    with pytest.raises(ConnectorRefusedError, match="plaintext"):
        _run(
            validate_connector(TelemetryConnector(url="http://prometheus.internal:9090"), settings)
        )
    with pytest.raises(ConnectorRefusedError, match="target_refused"):
        _run(validate_connector(TelemetryConnector(url="https://127.0.0.1:9090"), settings))
    # ...but loopback over http is the local/test convenience:
    local = make_settings(env="test")
    assert _run(validate_connector(TelemetryConnector(url="http://127.0.0.1:59090"), local))


def _adapter_with(handler) -> PrometheusAdapter:
    return PrometheusAdapter(make_settings(env="test"), transport=httpx.MockTransport(handler))


def _query(adapter: PrometheusAdapter):
    return adapter.query_range(
        TelemetryConnector(url="http://127.0.0.1:59090"),
        "up",
        start=0,
        end=600,
        step_seconds=60,
        timeout_seconds=2.0,
        correlation_id="test",
    )


def test_adapter_rejects_malformed_and_oversized_and_errors() -> None:
    cases = [
        (httpx.Response(200, text="not json"), ProviderContractError),
        (httpx.Response(200, json={"status": "error", "error": "boom"}), ProviderContractError),
        (
            httpx.Response(200, json={"status": "success", "data": {"resultType": "vector"}}),
            ProviderContractError,
        ),
        (httpx.Response(302, headers={"location": "http://evil"}), ProviderContractError),
        (httpx.Response(500, text="upstream exploded"), ProviderUnavailableError),
        (httpx.Response(400, json={"status": "error"}), ProviderContractError),
        (httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)), ProviderContractError),
    ]
    for response, expected in cases:
        adapter = _adapter_with(lambda request, r=response: r)
        with pytest.raises(expected):
            _run(_query(adapter))


def test_adapter_timeout_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    adapter = _adapter_with(handler)
    with pytest.raises(ProviderUnavailableError, match="provider_timeout"):
        _run(_query(adapter))


def test_adapter_success_and_correlation_propagation() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["correlation"] = request.headers.get("X-Correlation-ID", "")
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": [[1, "2.0"]]}],
                },
            },
        )

    adapter = _adapter_with(handler)
    result = _run(_query(adapter))
    assert result.result[0]["values"] == [[1, "2.0"]]
    assert seen["correlation"] == "test"


# --- internal metrics --------------------------------------------------------


def test_broker_metrics_render_is_bounded_and_safe() -> None:
    metrics = BrokerMetrics()
    metrics.record_query(
        template_key="service.request-rate.v1",
        provider_type="prometheus",
        outcome="ok",
        cache_state="miss",
        duration_seconds=0.2,
        returned_points=42,
    )
    metrics.record_rejection("concurrency")
    rendered = metrics.render()
    assert 'template_key="service.request-rate.v1"' in rendered
    assert 'reason="concurrency"' in rendered
    assert "drake_telemetry_query_duration_seconds_count 1" in rendered
    # No PII-shaped labels ever appear in the exposition:
    for forbidden in ("user", "tenant", "correlation", "url=", "scope_ref"):
        assert forbidden not in rendered
