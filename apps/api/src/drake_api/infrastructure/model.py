"""Pure pieces of the infrastructure snapshot (ADR-0032). No I/O.

The broker returns timeseries envelopes; these helpers turn them into the
shapes the screen draws, and decide the two things the screen must not
guess: which step keeps a query inside its point budget, and which group a
namespace belongs to when no Drake project claims it.
"""

import math
from typing import Any

# Prometheus refuses nothing here, but the broker does: a template may return
# at most this many points in total (registry GLOBAL_MAX_POINTS is 20 000;
# a margin keeps a namespace that appears mid-window from tipping it over).
POINT_BUDGET = 18_000
STEPS = (300, 600, 900, 1800, 3600)
DAY = 86_400


def step_for(series_estimate: int, range_seconds: int = DAY) -> int:
    """The finest step whose total points stay inside the budget."""
    n = max(1, series_estimate)
    for step in STEPS:
        if n * (range_seconds // step + 1) <= POINT_BUDGET:
            return step
    return STEPS[-1]


def grid(from_ts: int, to_ts: int, step: int) -> list[int]:
    start = (from_ts // step) * step
    end = -(-to_ts // step) * step
    return list(range(start, end + 1, step))


def by_labels(
    envelope: dict[str, Any] | None, *names: str
) -> dict[tuple[str, ...], list[list[Any]]]:
    """Series of one envelope keyed by the given label values."""
    out: dict[tuple[str, ...], list[list[Any]]] = {}
    for series in (envelope or {}).get("series", []):
        labels = series.get("labels", {})
        key = tuple(str(labels.get(n, "")) for n in names)
        out[key] = series.get("points", [])
    return out


def last_value(points: list[list[Any]]) -> float | None:
    for _ts, value in reversed(points or []):
        if value is not None and not (isinstance(value, float) and math.isnan(value)):
            return float(value)
    return None


def on_grid(points: list[list[Any]], times: list[int]) -> list[float | None]:
    """Points placed on the shared time grid; gaps stay None (never zero)."""
    index = {int(ts): value for ts, value in points or []}
    return [None if index.get(t) is None else round(float(index[t]), 6) for t in times]


def add_series(a: list[float | None], b: list[float | None]) -> list[float | None]:
    return [
        x if y is None else y if x is None else round(x + y, 6) for x, y in zip(a, b, strict=True)
    ]


def daily_growth(series: list[float | None], step: int) -> float | None:
    """Average change per day across the window, from its first and last real points."""
    real = [(i, v) for i, v in enumerate(series) if v is not None]
    if len(real) < 2:
        return None
    (i0, v0), (i1, v1) = real[0], real[-1]
    days = (i1 - i0) * step / DAY
    return None if days <= 0 else (v1 - v0) / days


SYSTEM_NAMESPACES = (
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "cert-manager",
    "local-path-storage",
)
SYSTEM_PREFIXES = ("ingress-nginx", "calico", "tigera", "metallb")
MONITORING = ("datadog", "monitoring", "drake-monitoring")


def group_of(namespace: str, project: dict[str, Any] | None) -> dict[str, str]:
    """Which group a namespace is drawn under.

    A Drake project claims its environments' namespaces. Everything else is
    named by what it is: Kubernetes itself, monitoring agents, Drake's own
    namespaces, or — for a namespace nobody has claimed — the namespace on
    its own, so an unknown workload is never hidden inside a bucket.
    """
    if project is not None:
        return {"kind": "project", "key": project["project_key"], "name": project["project_name"]}
    if namespace in MONITORING:
        return {"kind": "monitoring", "key": "monitoring", "name": "monitoring"}
    if namespace in SYSTEM_NAMESPACES or namespace.startswith(SYSTEM_PREFIXES):
        return {"kind": "kubernetes", "key": "kubernetes", "name": "kubernetes"}
    if namespace.startswith("drake-"):
        return {"kind": "drake", "key": "drake", "name": "drake"}
    return {"kind": "unclaimed", "key": f"ns:{namespace}", "name": namespace}
