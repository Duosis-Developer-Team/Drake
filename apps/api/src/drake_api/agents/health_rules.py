"""Deterministic, explainable workload health (ADR-0018).

Table-driven rules over the bounded summaries the agent ships. Every verdict
carries machine-readable reason codes; kinds without a rule are `unknown`
with an explicit reason — unknown is NEVER presented as healthy, and stale
inputs are the caller's axis (freshness), not this module's. Time-dependent
rules (CronJob schedule lag) take an injectable UTC clock and never touch
naive datetimes.
"""

import datetime as dt
import re
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


def _condition(conditions: Conditions, type_name: str) -> dict[str, Any] | None:
    for condition in conditions:
        if condition.get("type") == type_name:
            return condition
    return None


def _safe_reason(reason: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", reason)[:64]


def _generation_lag(spec: dict[str, Any], status: dict[str, Any]) -> bool:
    """Rollout lag ONLY when both generations are reliably present."""
    generation = _as_int(spec.get("generation"))
    observed = _as_int(status.get("observed_generation"))
    return generation is not None and observed is not None and observed < generation


def _deployment(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
    replica_failure = _condition(conditions, "ReplicaFailure")
    if replica_failure is not None and replica_failure.get("status") == "True":
        return UNHEALTHY, ["replica_failure"]
    progressing = _condition(conditions, "Progressing")
    if progressing is not None and progressing.get("status") == "False":
        reason = progressing.get("reason") or "progress_stalled"
        if reason == "ProgressDeadlineExceeded":
            return UNHEALTHY, ["progress_deadline_exceeded"]
        return UNHEALTHY, [f"progressing_false:{_safe_reason(reason)}"]

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
    reasons: list[str] = []
    available = _as_int(status.get("available_replicas"))
    updated = _as_int(status.get("updated_replicas"))
    if ready < desired:
        reasons.append("replicas_unavailable")
    if available is not None and available < desired:
        reasons.append("replicas_not_available")
    if updated is not None and updated < desired:
        reasons.append("rollout_in_progress")
    if _generation_lag(spec, status):
        reasons.append("generation_lag")
    if reasons:
        return DEGRADED, sorted(set(reasons))
    return HEALTHY, []


def _statefulset(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
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
    reasons: list[str] = []
    if ready < desired:
        reasons.append("replicas_unavailable")
    current = _as_int(status.get("current_replicas"))
    updated = _as_int(status.get("updated_replicas"))
    if updated is not None and updated < desired:
        reasons.append("update_rollout_in_progress")
    elif current is not None and updated is not None and current != updated:
        reasons.append("update_rollout_in_progress")
    current_revision = status.get("current_revision")
    update_revision = status.get("update_revision")
    if (
        isinstance(current_revision, str)
        and isinstance(update_revision, str)
        and current_revision != update_revision
    ):
        reasons.append("revision_lag")
    if _generation_lag(spec, status):
        reasons.append("generation_lag")
    if reasons:
        return DEGRADED, sorted(set(reasons))
    return HEALTHY, []


def _daemonset(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
    desired = _as_int(status.get("desired"))
    ready = _as_int(status.get("ready"))
    if desired is None:
        return UNKNOWN, ["no_status_reported"]
    if desired == 0:
        return DEGRADED, ["scaled_to_zero"]
    if ready is None or ready == 0:
        return UNHEALTHY, ["no_ready_replicas"]
    reasons: list[str] = []
    if ready < desired:
        reasons.append("replicas_unavailable")
    misscheduled = _as_int(status.get("misscheduled")) or 0
    if misscheduled > 0:
        reasons.append("pods_misscheduled")
    updated = _as_int(status.get("updated"))
    if updated is not None and updated < desired:
        reasons.append("rollout_in_progress")
    if _generation_lag(spec, status):
        reasons.append("generation_lag")
    if reasons:
        return DEGRADED, sorted(set(reasons))
    return HEALTHY, []


def _pod(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
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
        # CrashLoop/OOM take precedence over every softer signal.
        return UNHEALTHY, reasons

    scheduled = _condition(conditions, "PodScheduled")
    if scheduled is not None and scheduled.get("status") in ("False", "Unknown"):
        reason = scheduled.get("reason") or ""
        if reason == "Unschedulable":
            return UNHEALTHY, ["unschedulable"]
        return DEGRADED, ["pod_not_scheduled"]
    if phase == "Pending":
        waiting = status.get("waiting_reason")
        extra = [f"waiting:{_safe_reason(str(waiting))}"] if waiting else []
        return DEGRADED, ["pod_pending", *extra]
    if phase == "Running":
        soft: list[str] = []
        ready = _condition(conditions, "Ready")
        if ready is not None and ready.get("status") == "False":
            soft.append("pod_not_ready")
        containers_ready = _condition(conditions, "ContainersReady")
        if containers_ready is not None and containers_ready.get("status") == "False":
            soft.append("containers_not_ready")
        restarts = _as_int(status.get("restarts")) or 0
        if restarts > 5:
            soft.append("restart_churn")
        if soft:
            return DEGRADED, sorted(set(soft))
        return HEALTHY, []
    return UNKNOWN, ["no_status_reported"]


def _job(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
    failed_condition = _condition(conditions, "Failed")
    if failed_condition is not None and failed_condition.get("status") == "True":
        reason = failed_condition.get("reason") or "job_failed"
        return UNHEALTHY, [f"job_failed:{_safe_reason(reason)}"]
    complete = _condition(conditions, "Complete")
    if complete is not None and complete.get("status") == "True":
        return HEALTHY, []
    failed = _as_int(status.get("failed")) or 0
    succeeded = _as_int(status.get("succeeded")) or 0
    active = _as_int(status.get("active")) or 0
    if failed > 0 and active > 0:
        return DEGRADED, ["job_retrying_after_failures"]
    if failed > 0:
        return UNHEALTHY, ["job_failed"]
    if succeeded > 0:
        return HEALTHY, []
    if active > 0:
        return DEGRADED, ["job_running"]
    return UNKNOWN, ["no_status_reported"]


# Simple cron shapes bounded WITHOUT a cron library: every-minute, */n
# minutes, hourly, daily. Anything else is honestly un-modelled — the
# timing rule stays silent instead of guessing.
_CRON_STEP = re.compile(r"^\*/(\d+) \* \* \* \*$")
_CRON_EVERY_MINUTE = re.compile(r"^\* \* \* \* \*$")
_CRON_HOURLY = re.compile(r"^\d+ \* \* \* \*$")
_CRON_DAILY = re.compile(r"^\d+ \d+ \* \* \*$")


def _cron_interval_bound(schedule: str) -> dt.timedelta | None:
    schedule = schedule.strip()
    if _CRON_EVERY_MINUTE.match(schedule):
        return dt.timedelta(minutes=1)
    step = _CRON_STEP.match(schedule)
    if step:
        return dt.timedelta(minutes=int(step.group(1)))
    if _CRON_HOURLY.match(schedule):
        return dt.timedelta(hours=1)
    if _CRON_DAILY.match(schedule):
        return dt.timedelta(days=1)
    return None


def _parse_utc(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None  # timezone-naive input is never trusted
    return parsed.astimezone(dt.UTC)


def _cronjob(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
    if spec.get("suspend") is True:
        return DEGRADED, ["cronjob_suspended"]
    last_schedule = _parse_utc(status.get("last_schedule_time"))
    if last_schedule is None:
        return UNKNOWN, ["never_scheduled"]
    reasons: list[str] = []
    schedule = spec.get("schedule")
    if isinstance(schedule, str):
        bound = _cron_interval_bound(schedule)
        if bound is not None and now - last_schedule > 2 * bound + dt.timedelta(minutes=1):
            reasons.append("schedule_missed")
    last_success = _parse_utc(status.get("last_successful_time"))
    if last_success is None:
        reasons.append("no_successful_run_recorded")
    if reasons:
        return DEGRADED, sorted(set(reasons))
    return HEALTHY, []


def _node(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
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


def _namespace(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
    phase = status.get("phase")
    if phase == "Active":
        return HEALTHY, []
    if phase == "Terminating":
        stuck = _condition(conditions, "NamespaceFinalizersRemaining")
        if stuck is not None and stuck.get("status") == "True":
            return DEGRADED, ["namespace_terminating_stuck"]
        return DEGRADED, ["namespace_terminating"]
    return UNKNOWN, ["no_status_reported"]


_QUANTITY = re.compile(r"^(\d+)([a-zA-Z]*)$")


def _quota_pair(hard: Any, used: Any) -> tuple[int, int] | None:
    """Compare quota quantities ONLY when both parse as plain integers with
    identical units — anything else is honestly not compared."""
    if not (isinstance(hard, str) and isinstance(used, str)):
        return None
    hard_match = _QUANTITY.match(hard)
    used_match = _QUANTITY.match(used)
    if not hard_match or not used_match or hard_match.group(2) != used_match.group(2):
        return None
    return int(hard_match.group(1)), int(used_match.group(1))


def _resource_quota(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
    pairs = 0
    reasons: list[str] = []
    for key, hard_value in status.items():
        if not key.startswith("hard_"):
            continue
        used_value = status.get("used_" + key.removeprefix("hard_"))
        comparable = _quota_pair(hard_value, used_value)
        if comparable is None:
            continue
        pairs += 1
        hard, used = comparable
        if hard <= 0:
            continue
        if used >= hard:
            reasons.append("quota_exhausted")
        elif used * 10 >= hard * 9:
            reasons.append("quota_near_limit")
    if pairs == 0:
        return UNKNOWN, ["no_comparable_quota_data"]
    if "quota_exhausted" in reasons:
        return DEGRADED, ["quota_exhausted"]
    if "quota_near_limit" in reasons:
        return DEGRADED, ["quota_near_limit"]
    return HEALTHY, []


def _pvc(
    spec: dict[str, Any], status: dict[str, Any], conditions: Conditions, now: dt.datetime
) -> Verdict:
    phase = status.get("phase")
    if phase == "Bound":
        return HEALTHY, []
    if phase == "Pending":
        return DEGRADED, ["pvc_pending"]
    if phase == "Lost":
        return UNHEALTHY, ["pvc_lost"]
    return UNKNOWN, ["no_status_reported"]


Rule = Callable[[dict[str, Any], dict[str, Any], Conditions, dt.datetime], Verdict]

_RULES: dict[str, Rule] = {
    "Deployment": _deployment,
    "ReplicaSet": _statefulset,  # same replica shape (revisions simply absent)
    "StatefulSet": _statefulset,
    "DaemonSet": _daemonset,
    "Pod": _pod,
    "Job": _job,
    "CronJob": _cronjob,
    "Node": _node,
    "Namespace": _namespace,
    "ResourceQuota": _resource_quota,
    "PersistentVolumeClaim": _pvc,
}


def derive_health(kind: str, payload: dict[str, Any], now: dt.datetime | None = None) -> Verdict:
    """Health for one bounded resource record. `payload` is the stored
    bounded record (spec_summary/status_summary/conditions). `now` is an
    injectable timezone-aware UTC clock for time-dependent rules."""
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
    clock = now if now is not None else dt.datetime.now(dt.UTC)
    if clock.tzinfo is None:
        return UNKNOWN, ["malformed_clock"]
    return rule(spec, status, conditions, clock)
