"""Bounded protection metrics.

Labels are controlled identities only: project, environment, store, policy
key and status. Never an artifact id, a filename, a checksum or a run id —
those are unbounded, and a metric label that grows with every backup turns
a time series database into a slow leak of the thing it was measuring.
"""

from typing import Any

# The label set, fixed. Anything not in here does not become a label.
_LABELS = ("project", "environment", "store", "policy")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")[:120]


def _label_string(row: dict[str, Any]) -> str:
    return ",".join(f'{name}="{_escape(str(row.get(name) or ""))}"' for name in _LABELS)


def render_protection_metrics(rows: list[dict[str, Any]]) -> str:
    """Prometheus text for one row per policy.

    A `None` timestamp emits no sample at all rather than `0`: a zero epoch
    would render as 1970 and read as "backed up 55 years ago", which is
    both wrong and alarming.
    """
    lines: list[str] = []

    def gauge(name: str, help_text: str, key: str) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for row in rows:
            value = row.get(key)
            if value is None:
                continue
            lines.append(f"{name}{{{_label_string(row)}}} {value}")

    gauge(
        "drake_backup_last_success_timestamp_seconds",
        "Unix time of the most recent successful backup run.",
        "last_success_epoch",
    )
    gauge(
        "drake_backup_last_attempt_timestamp_seconds",
        "Unix time of the most recent backup attempt, successful or not.",
        "last_attempt_epoch",
    )
    gauge(
        "drake_backup_last_duration_seconds",
        "Duration of the most recent backup run.",
        "last_duration_seconds",
    )
    gauge(
        "drake_backup_last_artifact_size_bytes",
        "Size of the most recently observed artifact.",
        "last_artifact_size_bytes",
    )
    gauge(
        "drake_backup_consecutive_failures",
        "Failed backup runs since the last success.",
        "consecutive_failures",
    )
    gauge(
        "drake_restore_last_success_timestamp_seconds",
        "Unix time of the most recent successful restore drill.",
        "last_restore_epoch",
    )
    return "\n".join(lines) + "\n"


PROTECTION_METRIC_QUERY = """
    SELECT p.project_key, e.environment_key, bp.store_key, bp.policy_external_key,
           EXTRACT(EPOCH FROM pe.last_success_at)::bigint,
           EXTRACT(EPOCH FROM pe.last_attempt_at)::bigint,
           (SELECT r.duration_seconds FROM backup_runs r
            WHERE r.policy_id = bp.id ORDER BY r.started_at DESC LIMIT 1),
           (SELECT a.size_bytes FROM backup_artifacts a
            WHERE a.policy_id = bp.id ORDER BY a.source_event_at DESC LIMIT 1),
           COALESCE(pe.consecutive_failures, 0),
           EXTRACT(EPOCH FROM pe.last_restore_at)::bigint
    FROM backup_policies bp
    JOIN projects p ON p.id = bp.project_id
    LEFT JOIN environments e ON e.id = bp.environment_id
    LEFT JOIN LATERAL (
        SELECT * FROM protection_evaluations x
        WHERE x.policy_id = bp.id ORDER BY x.evaluated_for DESC LIMIT 1
    ) pe ON true
    WHERE bp.enabled
    LIMIT 500
"""


def metric_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "project": row[0],
            "environment": row[1],
            "store": row[2],
            "policy": row[3],
            "last_success_epoch": row[4],
            "last_attempt_epoch": row[5],
            "last_duration_seconds": row[6],
            "last_artifact_size_bytes": row[7],
            "consecutive_failures": row[8],
            "last_restore_epoch": row[9],
        }
        for row in rows
    ]
