"""Pure pieces of the infrastructure snapshot (ADR-0032)."""

from drake_api.infrastructure.model import (
    POINT_BUDGET,
    add_series,
    by_labels,
    daily_growth,
    grid,
    group_of,
    last_value,
    on_grid,
    step_for,
)
from drake_api.telemetry.registry import GLOBAL_MAX_POINTS, load_registry


def test_step_keeps_every_query_inside_the_point_budget() -> None:
    assert step_for(2) == 300
    assert step_for(50) == 300
    assert step_for(200) == 1800
    for series in (1, 26, 61, 62, 120, 200, 1000):
        step = step_for(series)
        assert step == 3600 or series * (86_400 // step + 1) <= POINT_BUDGET
    assert POINT_BUDGET < GLOBAL_MAX_POINTS


def test_grid_and_placement_keep_gaps_as_gaps() -> None:
    times = grid(1_000, 2_000, 300)
    assert times == [900, 1200, 1500, 1800, 2100]
    placed = on_grid([[1200, 1.5], [1800, None]], times)
    assert placed == [None, 1.5, None, None, None]
    assert add_series([1.0, None, None], [2.0, 3.0, None]) == [3.0, 3.0, None]


def test_labels_last_values_and_growth() -> None:
    envelope = {"series": [{"labels": {"node": "n1"}, "points": [[1, 0.2], [2, None]]}]}
    assert by_labels(envelope, "node") == {("n1",): [[1, 0.2], [2, None]]}
    assert by_labels(None, "node") == {}
    assert last_value([[1, 0.2], [2, None]]) == 0.2
    assert last_value([]) is None
    # 0.70 -> 0.72 over 24 hours of 300 s steps is 0.02 per day.
    series = [0.70] + [None] * 287 + [0.72]
    assert abs((daily_growth(series, 300) or 0) - 0.02) < 1e-3
    assert daily_growth([None, 0.5], 300) is None


def test_groups_name_what_a_namespace_is() -> None:
    project = {"project_key": "hermes", "project_name": "Hermes"}
    assert group_of("hermes-dev", project) == {"kind": "project", "key": "hermes", "name": "Hermes"}
    assert group_of("kube-system", None)["kind"] == "kubernetes"
    assert group_of("ingress-nginx-test", None)["kind"] == "kubernetes"
    assert group_of("datadog", None)["kind"] == "monitoring"
    assert group_of("drake-otel", None)["kind"] == "drake"
    assert group_of("datalake-webui-mock", None) == {
        "kind": "unclaimed",
        "key": "ns:datalake-webui-mock",
        "name": "datalake-webui-mock",
    }


def test_registry_carries_the_infrastructure_templates() -> None:
    registry = load_registry()
    keys = {t.key: t for t in registry.templates.values()}
    for key in (
        "cluster.node-cpu-utilization.v1",
        "cluster.namespace-cpu-usage.v1",
        "cluster.namespace-restarts-24h.v1",
        "namespace.pod-cpu-usage.v1",
    ):
        assert key in keys
    assert keys["namespace.pod-cpu-usage.v1"].scope_types == ("namespace",)
    assert [m[1] for m in keys["namespace.pod-cpu-usage.v1"].matchers] == [
        "cluster_ref",
        "namespace",
    ]
