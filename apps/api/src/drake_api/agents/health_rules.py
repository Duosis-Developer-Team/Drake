"""Deterministic, explainable workload health (ADR-0018).

Table-driven rules over the bounded summaries the agent ships. Every verdict
carries machine-readable reason codes; kinds without a rule are `unknown`
with an explicit reason — unknown is NEVER presented as healthy, and stale
inputs are the caller's axis (freshness), not this module's.
"""

from collections.abc import Callable
from typing import Any

HEALTHY = "healthy"
DEGRADED = "degraded"
UNHEALTHY = "unhealthy"
UNKNOWN = "unknown"

Verdict = tuple[str, list[str]]
Conditions = list[dict[str, Any]]


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _condition(conditions: list[dict[str, Any]], type_name: str) -> dict[str, Any] | None:
    for condition in conditions:
        if condition.get("type") == type_name:
            return condition
    return None


def _replica_verdict(status: dict[str, Any], desired_key: str, ready_key: str) -> Verdict:
    desired = _as_int(status.get(desired_key))
    ready = _as_int(status.get(ready_key))
    if desired is None:
        return UNKNOWN, ["no_status_reported"]
    if desired == 0:
        return DEGRADED, ["scaled_to_zero"]
    if ready is None or ready == 0:
        return UNHEALTHY, ["no_ready_replicas"]
    if ready < desired:
        return DEGRADED, ["replicas_unavailable"]
    return HEALTHY, []


def _deployment(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    desired = _as_int(spec.get("replicas"))
    if desired is None:
        desired = _as_int(status.get("replicas"))
    ready = _as_int(status.get("ready_replicas")) or 0
    if desired is None:
        return UNKNOWN, ["no_status_reported"]
    if desired == 0:
        return DEGRADED, ["scaled_to_zero"]
    if ready == 0:
        return UNHEALTHY, ["no_ready_replicas"]
    if ready < desired:
        return DEGRADED, ["replicas_unavailable"]
    return HEALTHY, []


def _statefulset(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    desired = _as_int(spec.get("replicas"))
    if desired is None:
        desired = _as_int(status.get("replicas"))
    if desired is None:
        return UNKNOWN, ["no_status_reported"]
    if desired == 0:
        return DEGRADED, ["scaled_to_zero"]
    ready = _as_int(status.get("ready_replicas")) or 0
    if ready == 0:
        return UNHEALTHY, ["no_ready_replicas"]
    if ready < desired:
        return DEGRADED, ["replicas_unavailable"]
    return HEALTHY, []


def _daemonset(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    verdict, reasons = _replica_verdict(status, "desired", "ready")
    misscheduled = _as_int(status.get("misscheduled")) or 0
    if misscheduled > 0 and verdict == HEALTHY:
        return DEGRADED, ["pods_misscheduled"]
    return verdict, reasons


def _pod(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    phase = status.get("phase")
    reasons: list[str] = []
    if status.get("crashloop") is True:
        reasons.append("crashloop_backoff")
    if status.get("oom_killed") is True:
        reasons.append("oom_killed")
    if phase == "Succeeded":
        return HEALTHY, []
    if phase == "Failed":
        return UNHEALTHY, reasons or ["pod_failed"]
    if reasons:
        return UNHEALTHY, reasons
    if phase == "Pending":
        waiting = status.get("waiting_reason")
        return DEGRADED, ["pod_pending"] + ([f"waiting:{waiting}"] if waiting else [])
    if phase == "Running":
        restarts = _as_int(status.get("restarts")) or 0
        if restarts > 5:
            return DEGRADED, ["restart_churn"]
        return HEALTHY, []
    return UNKNOWN, ["no_status_reported"]


def _job(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    failed = _as_int(status.get("failed")) or 0
    succeeded = _as_int(status.get("succeeded")) or 0
    active = _as_int(status.get("active")) or 0
    if failed > 0:
        return UNHEALTHY, ["job_failed"]
    if succeeded > 0:
        return HEALTHY, []
    if active > 0:
        return DEGRADED, ["job_running"]
    return UNKNOWN, ["no_status_reported"]


def _cronjob(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    if spec.get("suspend") is True:
        return DEGRADED, ["cronjob_suspended"]
    if status.get("last_schedule_time"):
        if status.get("last_successful_time"):
            return HEALTHY, []
        return DEGRADED, ["no_successful_run_recorded"]
    return UNKNOWN, ["never_scheduled"]


def _node(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    ready = _condition(conditions, "Ready")
    if ready is None:
        return UNKNOWN, ["no_status_reported"]
    if ready.get("status") == "False":
        return UNHEALTHY, ["node_not_ready"]
    if ready.get("status") == "Unknown":
        return UNKNOWN, ["node_status_unknown"]
    pressure_reasons = [
        f"{condition_type.lower()}"
        for condition_type in ("MemoryPressure", "DiskPressure", "PIDPressure")
        if (_condition(conditions, condition_type) or {}).get("status") == "True"
    ]
    if pressure_reasons:
        return DEGRADED, pressure_reasons
    return HEALTHY, []


def _namespace(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    phase = status.get("phase")
    if phase == "Active":
        return HEALTHY, []
    if phase == "Terminating":
        return DEGRADED, ["namespace_terminating"]
    return UNKNOWN, ["no_status_reported"]


def _pvc(spec: dict[str, Any], status: dict[str, Any], conditions: Conditions) -> Verdict:
    phase = status.get("phase")
    if phase == "Bound":
        return HEALTHY, []
    if phase == "Pending":
        return DEGRADED, ["pvc_pending"]
    if phase == "Lost":
        return UNHEALTHY, ["pvc_lost"]
    return UNKNOWN, ["no_status_reported"]


_RULES: dict[str, Callable[[dict[str, Any], dict[str, Any], Conditions], Verdict]] = {
    "Deployment": _deployment,
    "ReplicaSet": _statefulset,  # same replica shape
    "StatefulSet": _statefulset,
    "DaemonSet": _daemonset,
    "Pod": _pod,
    "Job": _job,
    "CronJob": _cronjob,
    "Node": _node,
    "Namespace": _namespace,
    "PersistentVolumeClaim": _pvc,
}


def derive_health(kind: str, payload: dict[str, Any]) -> Verdict:
    """Health for one bounded resource record. `payload` is the stored
    bounded record (spec_summary/status_summary/conditions)."""
    rule = _RULES.get(kind)
    if rule is None:
        return UNKNOWN, ["no_health_rule"]
    spec = payload.get("spec_summary") or {}
    status = payload.get("status_summary") or {}
    conditions = payload.get("conditions") or []
    if (
        not isinstance(spec, dict)
        or not isinstance(status, dict)
        or not isinstance(conditions, list)
    ):
        return UNKNOWN, ["malformed_summaries"]
    return rule(spec, status, conditions)
