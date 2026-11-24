"""Health templates answered from OpenTelemetry metrics (ADR-0038 phase 3):
the switch, the statements, and the Prometheus-shaped answer."""

import pytest
from drake_api.telemetry.otel_metrics import (
    STALENESS_SECONDS,
    SUPPORTED_TEMPLATES,
    OtelMetricsSwitch,
    build,
    to_matrix,
)

WORKLOAD = {
    "cluster_ref": "duosis-prod-1",
    "namespace": "hermes-test",
    "workload_name": "auth-service",
    "workload_kind": "Deployment",
}


def test_an_empty_switch_serves_nothing() -> None:
    off = OtelMetricsSwitch.from_settings([], [])
    for key in SUPPORTED_TEMPLATES:
        assert not off.serves(key, WORKLOAD)


def test_the_switch_needs_both_the_cluster_and_the_template() -> None:
    on = OtelMetricsSwitch.from_settings(["duosis-prod-1"], ["workload.replicas-ready.v1"])
    assert on.serves("workload.replicas-ready.v1", WORKLOAD)
    assert not on.serves("workload.replicas-desired.v1", WORKLOAD)
    assert not on.serves("workload.replicas-ready.v1", {**WORKLOAD, "cluster_ref": "other"})
    # A kind the metrics cannot name stays with Prometheus.
    assert not on.serves("workload.replicas-ready.v1", {**WORKLOAD, "workload_kind": "CronJob"})


def test_a_template_without_an_otel_answer_cannot_be_switched_on() -> None:
    on = OtelMetricsSwitch.from_settings(["duosis-prod-1"], ["service.request-rate.v1"])
    assert on.templates == frozenset()


@pytest.mark.parametrize(
    ("kind", "attribute", "ready_metric"),
    [
        ("Deployment", "k8s.deployment.name", "k8s.deployment.available"),
        ("StatefulSet", "k8s.statefulset.name", "k8s.statefulset.ready_pods"),
        ("DaemonSet", "k8s.daemonset.name", "k8s.daemonset.ready_nodes"),
    ],
)
def test_statements_name_the_cluster_and_take_values_as_parameters(
    kind: str, attribute: str, ready_metric: str
) -> None:
    values = {**WORKLOAD, "workload_kind": kind}
    for key in SUPPORTED_TEMPLATES:
        sql, params, steps = build(
            key, values, database="otel", start=1000, end=1600, step=60, window_seconds=300
        )
        assert steps == 10
        # Routed by cluster (ADR-0037 §3) and narrowed to the one workload.
        assert "{cluster:String}" in sql and params["cluster"] == "duosis-prod-1"
        assert f"ResourceAttributes['{attribute}'] = {{workload:String}}" in sql
        assert "auth-service" not in sql and "hermes-test" not in sql
        assert "__" not in sql
        if key == "workload.replicas-ready.v1":
            assert params["metric"] == ready_metric


def test_replicas_carry_forward_only_within_the_staleness_window() -> None:
    step = 60
    rows = [{"k": 0, "v": 2}, {"k": 3, "v": 3}]
    matrix = to_matrix("workload.replicas-ready.v1", rows, start=1000, step=step, steps=12)
    values = matrix.result[0]["values"]
    points = {t: float(v) for t, v in values}
    assert points[1000] == 2 and points[1120] == 2  # carried forward
    assert points[1180] == 3
    # Five minutes after the last sample, nothing is current any more.
    last = 1000 + 3 * step + STALENESS_SECONDS
    assert last in points and last + step not in points
    assert matrix.result[0]["metric"] == {}


def test_restarts_report_only_the_steps_that_were_computed() -> None:
    matrix = to_matrix(
        "workload.restarts-delta.v1",
        [{"k": 0, "v": 0}, {"k": 1, "v": "1"}],
        start=0,
        step=60,
        steps=1,
    )
    assert matrix.result[0]["values"] == [[0, "0.0"], [60, "1.0"]]


def test_nothing_current_is_an_empty_matrix() -> None:
    assert to_matrix("workload.replicas-desired.v1", [], start=0, step=60, steps=5).result == []


@pytest.mark.parametrize(
    ("key", "metric"),
    [
        ("workload.memory-usage.v1", "container.memory.working_set"),
        ("workload.memory-limit.v1", "k8s.container.memory_limit"),
        ("workload.cpu-limit.v1", "k8s.container.cpu_limit"),
    ],
)
def test_resource_sums_take_each_container_once(key: str, metric: str) -> None:
    sql, params, _ = build(
        key, WORKLOAD, database="otel", start=1000, end=1600, step=60, window_seconds=300
    )
    assert params["metric"] == metric and metric not in sql
    assert "otel_metrics_gauge" in sql and "sum(v)" in sql
    # A container is its pod and name: a restart's new container id is not
    # a second container.
    assert "ResourceAttributes['k8s.pod.uid']" in sql and "container.id" not in sql
    assert "{lookback:UInt32}" in sql


def test_cpu_usage_is_a_rate_over_the_cpu_seconds_counter() -> None:
    sql, params, _ = build(
        "workload.cpu-usage.v1",
        WORKLOAD,
        database="otel",
        start=1000,
        end=1600,
        step=60,
        window_seconds=300,
    )
    assert "otel_metrics_sum" in sql and "'container.cpu.time'" in sql
    assert params["window"] == "300"
    # A decrease is a counter reset, and one sample is not a rate.
    assert "if(d < 0, x, d)" in sql and "count() >= 2" in sql


def test_resource_answers_are_not_carried_forward() -> None:
    matrix = to_matrix(
        "workload.memory-usage.v1", [{"k": 0, "v": 5}, {"k": 3, "v": 6}], start=0, step=60, steps=5
    )
    assert matrix.result[0]["values"] == [[0, "5.0"], [180, "6.0"]]


def test_every_resource_template_can_be_switched_on() -> None:
    keys = [
        "workload.memory-usage.v1",
        "workload.memory-limit.v1",
        "workload.cpu-limit.v1",
        "workload.cpu-usage.v1",
    ]
    on = OtelMetricsSwitch.from_settings(["duosis-prod-1"], keys)
    assert on.templates == frozenset(keys)
