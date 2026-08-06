"""Table-driven unit tests for deterministic health derivation (ADR-0018)."""

import datetime as dt

import pytest
from drake_api.agents.health_rules import derive_health

CASES = [
    # (kind, payload, expected_health, expected_reason_fragment)
    (
        "Deployment",
        {"spec_summary": {"replicas": 3}, "status_summary": {"ready_replicas": 3}},
        "healthy",
        None,
    ),
    (
        "Deployment",
        {"spec_summary": {"replicas": 3}, "status_summary": {"ready_replicas": 1}},
        "degraded",
        "replicas_unavailable",
    ),
    (
        "Deployment",
        {"spec_summary": {"replicas": 3}, "status_summary": {"ready_replicas": 0}},
        "unhealthy",
        "no_ready_replicas",
    ),
    ("Deployment", {"spec_summary": {"replicas": 0}}, "degraded", "scaled_to_zero"),
    ("Deployment", {}, "unknown", "no_status_reported"),
    (
        "StatefulSet",
        {"spec_summary": {"replicas": 2}, "status_summary": {"ready_replicas": 2}},
        "healthy",
        None,
    ),
    (
        "DaemonSet",
        {"status_summary": {"desired": 4, "ready": 4, "misscheduled": 1}},
        "degraded",
        "pods_misscheduled",
    ),
    (
        "DaemonSet",
        {"status_summary": {"desired": 4, "ready": 0}},
        "unhealthy",
        "no_ready_replicas",
    ),
    ("Pod", {"status_summary": {"phase": "Running", "restarts": 0}}, "healthy", None),
    ("Pod", {"status_summary": {"phase": "Succeeded"}}, "healthy", None),
    ("Pod", {"status_summary": {"phase": "Pending"}}, "degraded", "pod_pending"),
    (
        "Pod",
        {"status_summary": {"phase": "Running", "crashloop": True}},
        "unhealthy",
        "crashloop_backoff",
    ),
    (
        "Pod",
        {"status_summary": {"phase": "Running", "oom_killed": True}},
        "unhealthy",
        "oom_killed",
    ),
    (
        "Pod",
        {"status_summary": {"phase": "Running", "restarts": 9}},
        "degraded",
        "restart_churn",
    ),
    ("Pod", {"status_summary": {"phase": "Failed"}}, "unhealthy", "pod_failed"),
    ("Pod", {}, "unknown", "no_status_reported"),
    ("Job", {"status_summary": {"succeeded": 1}}, "healthy", None),
    ("Job", {"status_summary": {"failed": 2}}, "unhealthy", "job_failed"),
    ("Job", {"status_summary": {"active": 1}}, "degraded", "job_running"),
    ("CronJob", {"spec_summary": {"suspend": True}}, "degraded", "cronjob_suspended"),
    (
        "CronJob",
        {
            "spec_summary": {},
            "status_summary": {
                "last_schedule_time": "2026-08-06T00:00:00Z",
                "last_successful_time": "2026-08-06T00:01:00Z",
            },
        },
        "healthy",
        None,
    ),
    ("CronJob", {}, "unknown", "never_scheduled"),
    (
        "Node",
        {"conditions": [{"type": "Ready", "status": "True"}]},
        "healthy",
        None,
    ),
    (
        "Node",
        {"conditions": [{"type": "Ready", "status": "False"}]},
        "unhealthy",
        "node_not_ready",
    ),
    (
        "Node",
        {"conditions": [{"type": "Ready", "status": "Unknown"}]},
        "unknown",
        "node_status_unknown",
    ),
    (
        "Node",
        {
            "conditions": [
                {"type": "Ready", "status": "True"},
                {"type": "MemoryPressure", "status": "True"},
            ]
        },
        "degraded",
        "memorypressure",
    ),
    ("Namespace", {"status_summary": {"phase": "Active"}}, "healthy", None),
    (
        "Namespace",
        {"status_summary": {"phase": "Terminating"}},
        "degraded",
        "namespace_terminating",
    ),
    ("PersistentVolumeClaim", {"status_summary": {"phase": "Bound"}}, "healthy", None),
    (
        "PersistentVolumeClaim",
        {"status_summary": {"phase": "Pending"}},
        "degraded",
        "pvc_pending",
    ),
    ("PersistentVolumeClaim", {"status_summary": {"phase": "Lost"}}, "unhealthy", "pvc_lost"),
    # --- Sprint 4 closure coverage ---
    (
        "Deployment",
        {
            "spec_summary": {"replicas": 3},
            "status_summary": {"ready_replicas": 3},
            "conditions": [
                {"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}
            ],
        },
        "unhealthy",
        "progress_deadline_exceeded",
    ),
    (
        "Deployment",
        {
            "spec_summary": {"replicas": 3},
            "status_summary": {"ready_replicas": 3},
            "conditions": [{"type": "ReplicaFailure", "status": "True"}],
        },
        "unhealthy",
        "replica_failure",
    ),
    (
        "Deployment",
        {
            "spec_summary": {"replicas": 3},
            "status_summary": {"ready_replicas": 3, "available_replicas": 1},
        },
        "degraded",
        "replicas_not_available",
    ),
    (
        "Deployment",
        {
            "spec_summary": {"replicas": 3},
            "status_summary": {"ready_replicas": 3, "updated_replicas": 1},
        },
        "degraded",
        "rollout_in_progress",
    ),
    (
        "Deployment",
        {
            "spec_summary": {"replicas": 2, "generation": 7},
            "status_summary": {"ready_replicas": 2, "observed_generation": 5},
        },
        "degraded",
        "generation_lag",
    ),
    (
        "StatefulSet",
        {
            "spec_summary": {"replicas": 3},
            "status_summary": {
                "ready_replicas": 3,
                "current_replicas": 3,
                "updated_replicas": 1,
            },
        },
        "degraded",
        "update_rollout_in_progress",
    ),
    (
        "StatefulSet",
        {
            "spec_summary": {"replicas": 2},
            "status_summary": {
                "ready_replicas": 2,
                "current_revision": "web-abc",
                "update_revision": "web-def",
            },
        },
        "degraded",
        "revision_lag",
    ),
    (
        "DaemonSet",
        {"status_summary": {"desired": 4, "ready": 4, "updated": 2}},
        "degraded",
        "rollout_in_progress",
    ),
    (
        "Pod",
        {
            "status_summary": {"phase": "Pending"},
            "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}],
        },
        "unhealthy",
        "unschedulable",
    ),
    (
        "Pod",
        {
            "status_summary": {"phase": "Pending"},
            "conditions": [{"type": "PodScheduled", "status": "Unknown"}],
        },
        "degraded",
        "pod_not_scheduled",
    ),
    (
        "Pod",
        {
            "status_summary": {"phase": "Running", "restarts": 0},
            "conditions": [{"type": "Ready", "status": "False"}],
        },
        "degraded",
        "pod_not_ready",
    ),
    (
        "Pod",
        {
            "status_summary": {"phase": "Running"},
            "conditions": [{"type": "ContainersReady", "status": "False"}],
        },
        "degraded",
        "containers_not_ready",
    ),
    (
        "Pod",
        {
            "status_summary": {"phase": "Running", "crashloop": True},
            "conditions": [{"type": "Ready", "status": "False"}],
        },
        "unhealthy",
        "crashloop_backoff",  # CrashLoop precedence over soft signals
    ),
    (
        "Job",
        {
            "status_summary": {"failed": 2},
            "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
        },
        "unhealthy",
        "job_failed:BackoffLimitExceeded",
    ),
    (
        "Job",
        {"status_summary": {}, "conditions": [{"type": "Complete", "status": "True"}]},
        "healthy",
        None,
    ),
    (
        "Job",
        {"status_summary": {"failed": 1, "active": 1}},
        "degraded",
        "job_retrying_after_failures",
    ),
    (
        "Namespace",
        {
            "status_summary": {"phase": "Terminating"},
            "conditions": [{"type": "NamespaceFinalizersRemaining", "status": "True"}],
        },
        "degraded",
        "namespace_terminating_stuck",
    ),
    (
        "ResourceQuota",
        {"status_summary": {"hard_pods": "10", "used_pods": "10"}},
        "degraded",
        "quota_exhausted",
    ),
    (
        "ResourceQuota",
        {"status_summary": {"hard_pods": "10", "used_pods": "9"}},
        "degraded",
        "quota_near_limit",
    ),
    (
        "ResourceQuota",
        {"status_summary": {"hard_pods": "10", "used_pods": "2"}},
        "healthy",
        None,
    ),
    (
        "ResourceQuota",
        {"status_summary": {"hard_memory": "2Gi", "used_memory": "512Mi"}},
        "unknown",
        "no_comparable_quota_data",  # mixed units are never guessed
    ),
    # Kinds without rules are unknown WITH a reason — never silently healthy.
    ("Service", {"status_summary": {}}, "unknown", "no_health_rule"),
    ("Event", {}, "unknown", "no_health_rule"),
    ("StorageClass", {}, "unknown", "no_health_rule"),
]


@pytest.mark.parametrize(("kind", "payload", "expected", "reason"), CASES)
def test_health_rule(kind: str, payload: dict, expected: str, reason: str | None) -> None:
    health, reasons = derive_health(kind, payload)
    assert health == expected, f"{kind}: {reasons}"
    if reason is not None:
        assert reason in reasons, f"{kind}: expected {reason} in {reasons}"
    if expected == "healthy":
        assert reasons == [], f"healthy must carry no blame: {reasons}"


def test_unknown_is_never_healthy() -> None:
    """Every rule-less kind and every empty payload derives to a
    non-healthy verdict with an explicit reason."""
    for kind in ("Service", "EndpointSlice", "HorizontalPodAutoscaler", "ResourceQuota"):
        health, reasons = derive_health(kind, {})
        assert health == "unknown"
        assert reasons, "unknown must always explain itself"


def test_malformed_summaries_are_unknown() -> None:
    health, reasons = derive_health("Deployment", {"status_summary": "not-a-dict"})
    assert health == "unknown"
    assert reasons == ["malformed_summaries"]


def test_cronjob_schedule_lag_uses_injected_utc_clock() -> None:
    """Time-dependent rules take an aware UTC clock; naive datetimes and
    unparseable schedules never produce timing verdicts."""
    payload = {
        "spec_summary": {"schedule": "*/5 * * * *"},
        "status_summary": {
            "last_schedule_time": "2026-08-06T10:00:00Z",
            "last_successful_time": "2026-08-06T10:00:30Z",
        },
    }
    on_time = dt.datetime(2026, 8, 6, 10, 6, tzinfo=dt.UTC)
    health, reasons = derive_health("CronJob", payload, now=on_time)
    assert health == "healthy", reasons

    lagging = dt.datetime(2026, 8, 6, 10, 30, tzinfo=dt.UTC)
    health, reasons = derive_health("CronJob", payload, now=lagging)
    assert health == "degraded"
    assert "schedule_missed" in reasons

    # An unmodelled cron shape yields NO timing verdict (honest silence).
    exotic = dict(payload)
    exotic["spec_summary"] = {"schedule": "3 2 * * 1-5"}
    health, reasons = derive_health("CronJob", exotic, now=lagging)
    assert health == "healthy", reasons

    naive = dt.datetime(2026, 8, 6, 10, 30)
    health, reasons = derive_health("CronJob", payload, now=naive)
    assert health == "unknown"
    assert reasons == ["malformed_clock"]


def test_daily_cron_lag_detected() -> None:
    payload = {
        "spec_summary": {"schedule": "0 3 * * *"},
        "status_summary": {
            "last_schedule_time": "2026-08-01T03:00:00Z",
            "last_successful_time": "2026-08-01T03:01:00Z",
        },
    }
    now = dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.UTC)
    health, reasons = derive_health("CronJob", payload, now=now)
    assert health == "degraded"
    assert "schedule_missed" in reasons
