"""Gathering evidence, reaching a verdict, and raising the incident.

The evaluation itself is pure (`model.evaluate_protection`). This module
does the two things around it that need a database: assembling what is
known about a policy, and turning a bad verdict into an incident through
the Sprint 6 lifecycle rather than a second, parallel one.

The incident bridge is deliberately thin. Protection problems are
incidents like any other — they open once per active problem, resolve when
the problem clears, and notify through the Sprint 7 planner. No network
call happens inside the incident transaction.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.alerting.model import protection_correlation_key
from drake_api.protection.model import (
    ArtifactEvidence,
    DrillEvidence,
    OverallState,
    PolicyPromise,
    ProtectionEvidence,
    ProtectionVerdict,
    RunEvidence,
    evaluate_protection,
    incident_reasons,
)
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.protection.service")

# Protection incidents are severity `critical` like every other incident:
# the existing contract has one severity, and widening it here would make
# protection a special case in every consumer.
INCIDENT_SEVERITY = "critical"

INCIDENT_TITLES: dict[str, str] = {
    "backup_overdue": "Backup overdue",
    "latest_run_failed": "Backup failed",
    "integrity_failed": "Backup integrity check failed",
    "offsite_missing": "Offsite copy missing",
    "restore_failed": "Restore drill failed",
    "restore_verification_expired": "Restore verification expired",
}


@dataclass
class EvaluationOutcome:
    policy_id: uuid.UUID
    verdict: ProtectionVerdict
    incident_opened: uuid.UUID | None = None
    incident_resolved: uuid.UUID | None = None


async def gather_evidence(
    connection: AsyncConnection, policy_id: uuid.UUID, *, reporter_seen_at: datetime | None
) -> tuple[PolicyPromise, ProtectionEvidence]:
    """Everything known about one policy, in one read."""
    policy = (
        await connection.execute(
            text(
                """
                SELECT rpo_seconds, rto_seconds, requires_offsite, requires_integrity_check,
                       restore_verification_ttl_seconds, enabled, version
                FROM backup_policies WHERE id = :id
                """
            ),
            {"id": policy_id},
        )
    ).first()
    if policy is None:
        raise LookupError("unknown policy")
    promise = PolicyPromise(
        rpo_seconds=policy[0],
        rto_seconds=policy[1],
        requires_offsite=policy[2],
        requires_integrity_check=policy[3],
        restore_verification_ttl_seconds=policy[4],
        enabled=policy[5],
        version=policy[6],
    )

    # The newest attempt, whatever it said, and the newest SUCCESS. They
    # are different questions: a failure after a success does not erase the
    # success, and a success does not excuse the failure.
    attempt = (
        await connection.execute(
            text(
                """
                SELECT status, started_at, completed_at, error_code
                FROM backup_runs
                WHERE policy_id = :id AND superseded_by_run_id IS NULL
                ORDER BY started_at DESC LIMIT 1
                """
            ),
            {"id": policy_id},
        )
    ).first()
    last_success_at = (
        await connection.execute(
            text(
                """
                SELECT max(COALESCE(completed_at, started_at)) FROM backup_runs
                WHERE policy_id = :id AND status = 'succeeded'
                  AND superseded_by_run_id IS NULL
                """
            ),
            {"id": policy_id},
        )
    ).scalar_one_or_none()

    failures = (
        await connection.execute(
            text(
                """
                SELECT count(*) FROM backup_runs
                WHERE policy_id = :id AND status = 'failed'
                  AND started_at > COALESCE(:since, '-infinity'::timestamptz)
                """
            ),
            {"id": policy_id, "since": last_success_at},
        )
    ).scalar_one()

    # The artifact belonging to the newest SUCCESSFUL run. Evidence for one
    # store never stands in for another because the artifact is reached
    # through this policy's own runs.
    artifact_row = (
        await connection.execute(
            text(
                """
                SELECT a.id, a.presence, a.size_bytes, a.checksum, a.created_at_source,
                       (SELECT ic.result FROM integrity_checks ic
                        WHERE ic.artifact_id = a.id
                        ORDER BY ic.checked_at DESC LIMIT 1),
                       (SELECT ic.checked_at FROM integrity_checks ic
                        WHERE ic.artifact_id = a.id
                        ORDER BY ic.checked_at DESC LIMIT 1),
                       EXISTS (SELECT 1 FROM replication_copies rc
                               WHERE rc.artifact_id = a.id AND rc.is_offsite
                                 AND rc.state = 'present'),
                       (SELECT rc.site_key FROM replication_copies rc
                        WHERE rc.artifact_id = a.id AND rc.is_offsite AND rc.state = 'present'
                        ORDER BY rc.observed_at DESC LIMIT 1)
                FROM backup_artifacts a
                JOIN backup_runs r ON r.id = a.run_id
                WHERE a.policy_id = :id AND r.status = 'succeeded'
                ORDER BY a.source_event_at DESC
                LIMIT 1
                """
            ),
            {"id": policy_id},
        )
    ).first()

    artifact = (
        ArtifactEvidence()
        if artifact_row is None
        else ArtifactEvidence(
            exists=True,
            presence=artifact_row[1],
            size_bytes=artifact_row[2],
            checksum=artifact_row[3],
            created_at=artifact_row[4],
            integrity_result=artifact_row[5],
            integrity_checked_at=artifact_row[6],
            offsite_present=bool(artifact_row[7]),
            offsite_site_key=artifact_row[8],
        )
    )

    drill_row = (
        await connection.execute(
            text(
                """
                SELECT result, completed_at, duration_seconds, rto_met
                FROM restore_drills
                WHERE policy_id = :id AND completed_at IS NOT NULL
                ORDER BY completed_at DESC LIMIT 1
                """
            ),
            {"id": policy_id},
        )
    ).first()
    drill = (
        DrillEvidence()
        if drill_row is None
        else DrillEvidence(
            result=drill_row[0],
            completed_at=drill_row[1],
            duration_seconds=drill_row[2],
            rto_met=drill_row[3],
        )
    )

    evidence = ProtectionEvidence(
        last_success_at=last_success_at,
        last_attempt=(
            RunEvidence()
            if attempt is None
            else RunEvidence(
                status=attempt[0],
                started_at=attempt[1],
                completed_at=attempt[2],
                error_code=attempt[3],
            )
        ),
        artifact=artifact,
        drill=drill,
        reporter_seen_at=reporter_seen_at,
        consecutive_failures=int(failures or 0),
    )
    return promise, evidence


async def store_evaluation(
    connection: AsyncConnection,
    policy_id: uuid.UUID,
    promise: PolicyPromise,
    evidence: ProtectionEvidence,
    verdict: ProtectionVerdict,
    *,
    evaluated_for: datetime,
) -> None:
    """Record the verdict for this period AND this policy version.

    Keyed on the policy version so a historical assessment is never
    rewritten by today's promise: an evaluation recorded what the policy
    required at the time, and changing the RPO does not change what was
    true last month.
    """
    await connection.execute(
        text(
            """
            INSERT INTO protection_evaluations
                (policy_id, policy_version, evaluated_for, backup_state,
                 recoverability_state, overall_state, reasons, last_success_at,
                 last_attempt_at, last_restore_at, reporter_seen_at, consecutive_failures)
            VALUES (:policy, :version, :period, :backup, :recoverability, :overall,
                    CAST(:reasons AS jsonb), :last_success, :last_attempt, :last_restore,
                    :reporter, :failures)
            ON CONFLICT (policy_id, evaluated_for, policy_version) DO UPDATE
            SET backup_state = EXCLUDED.backup_state,
                recoverability_state = EXCLUDED.recoverability_state,
                overall_state = EXCLUDED.overall_state,
                reasons = EXCLUDED.reasons,
                last_success_at = EXCLUDED.last_success_at,
                last_attempt_at = EXCLUDED.last_attempt_at,
                last_restore_at = EXCLUDED.last_restore_at,
                reporter_seen_at = EXCLUDED.reporter_seen_at,
                consecutive_failures = EXCLUDED.consecutive_failures,
                computed_at = now()
            """
        ),
        {
            "policy": policy_id,
            "version": promise.version,
            "period": evaluated_for,
            "backup": str(verdict.backup_state),
            "recoverability": str(verdict.recoverability_state),
            "overall": str(verdict.overall_state),
            "reasons": json.dumps(verdict.reasons),
            "last_success": evidence.last_success_at,
            "last_attempt": evidence.last_attempt.started_at,
            "last_restore": evidence.drill.completed_at,
            "reporter": evidence.reporter_seen_at,
            "failures": evidence.consecutive_failures,
        },
    )


async def _context_for_policy(
    connection: AsyncConnection, policy_id: uuid.UUID
) -> dict[str, Any] | None:
    """Where a protection incident for this policy belongs.

    A workload binding is used when one exists, because it makes the
    incident appear on the service's own screens. But a backup policy
    protects a STORE, not a pod, and plenty of policies have no service
    binding at all — Sprint 9 recorded their verdicts and then declined to
    raise an incident, which meant an overdue backup with no bound workload
    was visible only to whoever thought to look.

    So the project and environment the policy already names are enough. The
    binding, when present, is extra context rather than a precondition.
    """
    row = (
        (
            await connection.execute(
                text(
                    """
                SELECT b.id, b.environment_service_id, bp.project_id, bp.environment_id,
                       b.service_id, bp.display_name, bp.store_key
                FROM backup_policies bp
                LEFT JOIN environment_services es ON es.project_id = bp.project_id
                    AND bp.environment_id IS NOT NULL
                    AND es.environment_id = bp.environment_id
                LEFT JOIN service_workload_bindings b
                    ON b.environment_service_id = es.id AND b.lifecycle = 'active'
                WHERE bp.id = :id
                ORDER BY b.created_at NULLS LAST
                LIMIT 1
                """
                ),
                {"id": policy_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    context = dict(row)
    if context.get("environment_id") is None:
        # An incident needs an environment. A policy that names none has
        # nothing to file against, and inventing one would put it in an
        # environment it does not protect.
        return None
    return context


async def sync_incident(
    connection: AsyncConnection,
    policy_id: uuid.UUID,
    verdict: ProtectionVerdict,
    *,
    now: datetime,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Open or resolve a protection incident through the Sprint 6 lifecycle.

    One incident per policy per active problem, keyed on the policy itself
    rather than on a workload: while the problem persists each evaluation
    updates the existing incident, and when it clears the incident resolves
    through the same auto-resolution path a health recovery uses.
    """
    problems = incident_reasons(verdict)
    context = await _context_for_policy(connection, policy_id)
    if context is None:
        return None, None

    key = protection_correlation_key(policy_id)
    existing = (
        await connection.execute(
            text(
                """
                SELECT id, state FROM incidents
                WHERE correlation_key = :key AND state IN ('open', 'acknowledged')
                FOR UPDATE
                """
            ),
            {"key": key},
        )
    ).first()

    if problems:
        primary = problems[0]
        if existing is not None:
            # The problem is still happening. Updating beats opening a
            # second incident for the same unresolved thing.
            await connection.execute(
                text(
                    "UPDATE incidents SET last_critical_at = :at, updated_at = now() WHERE id = :id"
                ),
                {"id": existing[0], "at": now},
            )
            return None, None
        opened = await _open_incident(connection, context, primary, problems, now, key)
        return opened, None

    if existing is not None:
        # The problem cleared. Resolution goes through the same lifecycle
        # a health recovery uses, so the timeline reads the same way.
        await connection.execute(
            text(
                """
                UPDATE incidents
                SET state = 'resolved', resolved_at = :at,
                    resolution_source = 'protection_recovered',
                    version = version + 1, updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": existing[0], "at": now},
        )
        await connection.execute(
            text(
                """
                INSERT INTO incident_events (incident_id, event_type, occurred_at, detail)
                VALUES (:id, 'auto_resolved', :at, CAST(:detail AS jsonb))
                """
            ),
            {"id": existing[0], "at": now, "detail": json.dumps({"source": "protection"})},
        )
        return None, uuid.UUID(str(existing[0]))
    return None, None


async def _open_incident(
    connection: AsyncConnection,
    context: Any,
    primary: str,
    problems: list[str],
    now: datetime,
    key: str,
) -> uuid.UUID | None:
    from sqlalchemy.exc import IntegrityError

    title = f"{context['store_key']}: {INCIDENT_TITLES.get(primary, 'Protection problem')}"
    savepoint = await connection.begin_nested()
    try:
        row = (
            await connection.execute(
                text(
                    """
                    INSERT INTO incidents
                        (source, correlation_key, binding_id, environment_service_id,
                         project_id, environment_id, service_id, state, severity,
                         title, primary_reason, opening_reasons, binding_revision,
                         opened_at, last_critical_at)
                    VALUES ('protection', :key, :binding, :es, :project, :environment,
                            :service, 'open', :severity, :title, :primary,
                            CAST(:reasons AS jsonb), 1, :at, :at)
                    RETURNING id
                    """
                ),
                {
                    "key": key,
                    # All three may be NULL: a policy protecting a store with
                    # no bound workload still has a real problem.
                    "binding": context["id"],
                    "es": context["environment_service_id"],
                    "project": context["project_id"],
                    "environment": context["environment_id"],
                    "service": context["service_id"],
                    "severity": INCIDENT_SEVERITY,
                    "title": title[:200],
                    "primary": primary,
                    "reasons": json.dumps(problems),
                    "at": now,
                },
            )
        ).first()
    except IntegrityError:
        # The partial unique index on active incidents already holds one
        # for this policy. Losing that race is success.
        await savepoint.rollback()
        return None
    await savepoint.commit()
    assert row is not None
    incident_id = uuid.UUID(str(row[0]))
    await connection.execute(
        text(
            """
            INSERT INTO incident_events (incident_id, event_type, occurred_at, detail)
            VALUES (:id, 'opened', :at, CAST(:detail AS jsonb))
            """
        ),
        {
            "id": incident_id,
            "at": now,
            "detail": json.dumps({"source": "protection", "reasons": problems}),
        },
    )
    return incident_id


async def evaluate_policy(
    engine: AsyncEngine,
    settings: Settings,
    policy_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> EvaluationOutcome:
    """Evaluate one policy, store the verdict, and sync its incident."""
    moment = now or datetime.now(UTC)
    async with engine.begin() as connection:
        connector_key = (
            await connection.execute(
                text("SELECT connector_key FROM backup_policies WHERE id = :id"),
                {"id": policy_id},
            )
        ).scalar_one()
        from drake_api.protection.ingest import reporter_seen_at, stale_after

        connector = settings.protection_connectors.get(str(connector_key))
        seen_at = await reporter_seen_at(connection, str(connector_key))
        promise, evidence = await gather_evidence(connection, policy_id, reporter_seen_at=seen_at)
        verdict = evaluate_protection(
            promise,
            evidence,
            now=moment,
            **({"reporter_stale_after": stale_after(connector)} if connector else {}),
        )
        await store_evaluation(
            connection, policy_id, promise, evidence, verdict, evaluated_for=moment
        )
        opened, resolved = await sync_incident(connection, policy_id, verdict, now=moment)
    return EvaluationOutcome(policy_id, verdict, opened, resolved)


async def evaluate_all(
    engine: AsyncEngine, settings: Settings, *, limit: int = 200
) -> list[EvaluationOutcome]:
    """Evaluate every enabled policy. One failure costs only its policy."""
    async with engine.connect() as connection:
        policy_ids = [
            uuid.UUID(str(row[0]))
            for row in (
                await connection.execute(
                    text(
                        "SELECT id FROM backup_policies WHERE enabled ORDER BY created_at "
                        "LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ).all()
        ]
    outcomes: list[EvaluationOutcome] = []
    for policy_id in policy_ids:
        try:
            outcomes.append(await evaluate_policy(engine, settings, policy_id))
        except Exception:
            logger.warning("protection evaluation failed for one policy")
    return outcomes


def overall_is_healthy(state: OverallState | str) -> bool:
    return str(state) in (
        str(OverallState.RECOVERABLE_VERIFIED),
        str(OverallState.PROTECTED_UNVERIFIED),
    )
