"""What logs and traces share: how a binding names a workload in the store.

Both signals arrive with the same OpenTelemetry resource attributes — the
agent writes them on log records, OBI on spans, and the gateway stamps the
cluster name on both — so "the telemetry of this service" is answered the
same way for each: the cluster, namespace and workload of the authorized
binding row, never anything from the request.
"""

from datetime import UTC, datetime
from typing import Any

# Fixed windows. No request can ask for a month of telemetry.
RANGES: dict[str, int] = {"15m": 900, "1h": 3600, "6h": 21600, "24h": 86400}
DEFAULT_RANGE = "1h"

# The resource attribute that names a workload of each bound kind. These are
# the OpenTelemetry semantic-convention keys the k8sattributes processor and
# OBI write, so a binding's kind decides the key server-side.
WORKLOAD_ATTRIBUTE: dict[str, str] = {
    "Deployment": "k8s.deployment.name",
    "StatefulSet": "k8s.statefulset.name",
    "DaemonSet": "k8s.daemonset.name",
}

# The WHERE clause every telemetry read shares. Values are typed query
# parameters; the attribute key for the workload is chosen server-side from
# WORKLOAD_ATTRIBUTE and also sent as a parameter.
SCOPE_PREDICATE = """
      ResourceAttributes['k8s.cluster.name'] = {cluster:String}
  AND ResourceAttributes['k8s.namespace.name'] = {namespace:String}
  AND ResourceAttributes[{workload_attribute:String}] = {workload:String}
"""


class ScopeError(ValueError):
    """The binding cannot be expressed as a telemetry scope."""


def scope_parameters(binding: dict[str, Any]) -> dict[str, str]:
    """Query parameters naming the binding's workload. Raises for an
    unsupported kind rather than matching on a guess."""
    attribute = WORKLOAD_ATTRIBUTE.get(str(binding.get("workload_kind")))
    if attribute is None:
        raise ScopeError("unsupported workload kind")
    return {
        "cluster": str(binding["cluster"]["cluster_ref"]),
        "namespace": str(binding["namespace"]),
        "workload_attribute": attribute,
        "workload": str(binding["workload_name"]),
    }


def clickhouse_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def parse_store_time(value: Any) -> datetime:
    """ClickHouse renders DateTime64(9) as `YYYY-MM-DD HH:MM:SS.fffffffff`;
    Python keeps microseconds, which is all a page cursor needs."""
    if not isinstance(value, str):
        raise ValueError("timestamp")
    head, _, fraction = value.partition(".")
    parsed = datetime.strptime(head, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    if fraction:
        parsed = parsed.replace(microsecond=int(fraction[:6].ljust(6, "0")))
    return parsed
