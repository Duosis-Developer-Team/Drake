"""Persistence and reconciliation for the GitHub App integration.

Everything here is scope-bound and secret-free: rows carry identity and
observed attributes only. Repositories are keyed on GitHub's PERMANENT id
so renames and transfers reconcile onto the same row (ADR-0020 §1).
"""

import asyncio
import contextlib
import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.audit import AuditEventData, record_audit_event
from drake_api.github_app import catalog, lifecycle, onboarding, policy, webhook
from drake_api.github_app.auth import missing_permissions
from drake_api.github_app.client import (
    GitHubClient,
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    error_code,
)

logger = logging.getLogger(__name__)

PROVIDER = catalog.PROVIDER

# The read permission a policy rule needs, by rule family. Used to turn a
# 403 into an honest "we were not granted this", never into a PASS.
# The read permissions a dry-run evaluation needs. Requested explicitly so
# the minted token is no wider than the job.
# Membership projection reads repository identity only, so it asks for
# nothing beyond Metadata. Requesting authority a step has no use for is
# how least privilege quietly stops being least.
MEMBERSHIP_PERMISSIONS: dict[str, str] = {"metadata": "read"}

EVALUATION_PERMISSIONS: dict[str, str] = {
    "metadata": "read",
    "administration": "read",
    "actions": "read",
}

PERMISSION_HINTS = {
    "protection": "administration:read",
    "rulesets": "administration:read",
    "workflows": "actions:read",
    "environments": "actions:read",
}


class SecurityGateBlockedError(RuntimeError):
    """Raised before ANY network path when a manual gate is open."""

    def __init__(self, full_name: str, gate: str) -> None:
        super().__init__(f"repository is blocked by the {gate} security gate")
        self.full_name = full_name
        self.gate = gate


# A poison delivery must stop, not spin. Past this many attempts the row
# is dead-lettered as `failed` and audited, so it is visible rather than
# retried forever.
MAX_DELIVERY_ATTEMPTS = 5
# One drain pass stays bounded so a backlog cannot monopolise a worker.
DRAIN_BATCH_SIZE = 50
# How long a claimed reconciliation job stays owned. Long enough for the
# provider round trip, short enough that a dead worker's job is retried.
JOB_LEASE_SECONDS = 300


@dataclass(frozen=True)
class DeliveryOutcome:
    # "new"      -> we claimed it; the durable work item is ours to run
    # "pending"  -> claimed earlier but NEVER finished; must be run, not acked
    # "duplicate"-> genuinely finished before; idempotent acknowledgement
    # "failed"   -> dead-lettered; TERMINAL, no further domain work
    # "conflict" -> same id, different bytes; a security event
    status: str
    delivery_row_id: uuid.UUID | None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _audit(
    engine: AsyncEngine,
    action: str,
    result: str,
    *,
    target_type: str = "github_repository",
    target_id: str | None = None,
    scope_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_type: str = "service",
    actor_id: str = "github-app",
) -> None:
    """Best-effort audit. Failures never take down the caller's work."""
    try:
        await record_audit_event(
            engine,
            AuditEventData(
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                result=result,
                scope_type="organization" if scope_ref else None,
                scope_id=scope_ref,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata or {},
            ),
        )
    except Exception:
        logger.warning("github audit write did not persist", extra={"audit_action": action})


# --- webhook delivery ---------------------------------------------------


async def record_delivery(
    connection: AsyncConnection,
    *,
    delivery_id: str,
    event_type: str,
    payload_digest: str,
    envelope: dict[str, Any],
    installation_external_id: int | None,
    repository_external_id: int | None,
    scope_id: uuid.UUID,
) -> DeliveryOutcome:
    """Claim a delivery id AND record its durable work item, atomically.

    The UNIQUE index is the replay defence and decides a concurrent race.
    The row starts `pending`: it is not an acknowledgement that the work
    happened, only that the work is now recoverable. Anything that reads
    `pending` as "already handled" reintroduces the loss this guards
    against — a crash before the domain mutation would be answered with a
    202 on every retry, and the event would vanish.
    """
    inserted = (
        await connection.execute(
            text(
                """
                INSERT INTO github_webhook_deliveries
                    (delivery_id, event_type, payload_digest, envelope,
                     installation_external_id, repository_external_id, scope_id, status)
                VALUES (:delivery_id, :event_type, :digest, CAST(:envelope AS jsonb),
                        :installation_id, :repository_id, :scope_id, 'pending')
                ON CONFLICT (delivery_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "delivery_id": delivery_id,
                "event_type": event_type,
                "digest": payload_digest,
                "envelope": json.dumps(envelope),
                "installation_id": installation_external_id,
                "repository_id": repository_external_id,
                "scope_id": scope_id,
            },
        )
    ).first()
    if inserted is not None:
        return DeliveryOutcome(status="new", delivery_row_id=uuid.UUID(str(inserted[0])))

    existing = (
        await connection.execute(
            text(
                "SELECT id, payload_digest, status FROM github_webhook_deliveries "
                "WHERE delivery_id = :delivery_id"
            ),
            {"delivery_id": delivery_id},
        )
    ).one()
    row_id = uuid.UUID(str(existing[0]))
    if str(existing[1]) != payload_digest:
        # Same id, different bytes. The stored row is EVIDENCE of the
        # original delivery; a forged replay must not get to edit it.
        return DeliveryOutcome(status="conflict", delivery_row_id=row_id)
    status = str(existing[2])
    if status == "processed":
        return DeliveryOutcome(status="duplicate", delivery_row_id=row_id)
    if status == "failed":
        # Dead-lettered. Treating this as "unfinished, try again" would turn
        # the retry ceiling into a suggestion: GitHub's redeliver button
        # would become an unbounded retry loop. Recovery from here is an
        # explicit operator action, not a webhook.
        return DeliveryOutcome(status="failed", delivery_row_id=row_id)
    # pending: unfinished work, so this redelivery is a chance to finish it
    # rather than a duplicate to wave through.
    return DeliveryOutcome(status="pending", delivery_row_id=row_id)


async def mark_delivery_processed(
    connection: AsyncConnection, delivery_row_id: uuid.UUID, status: str
) -> None:
    await connection.execute(
        text(
            "UPDATE github_webhook_deliveries SET status = :status, processed_at = now() "
            "WHERE id = :id"
        ),
        {"status": status, "id": delivery_row_id},
    )


def _error_code_slug(value: str) -> str:
    """Coerce a label into the bounded snake-case shape the schema accepts.

    The `last_error_code` CHECK is deliberately narrow. Writing a raw
    exception name into it fails the constraint, which would roll back the
    very transaction that counts the attempt — leaving a poison delivery to
    be retried forever with its counter perpetually reset.
    """
    lowered = "".join(char if char.isalnum() or char in "_.-" else "_" for char in value.lower())
    lowered = lowered.lstrip("_.-")
    return (lowered or "unknown")[:64]


async def _record_failed_attempt(
    engine: AsyncEngine, delivery_row_id: uuid.UUID, error_code_value: str
) -> None:
    """Count the attempt in its OWN transaction.

    The work transaction rolled back, so anything it wrote is gone —
    including an attempt counter. Bounding retries therefore has to happen
    outside it, or a poison delivery would be retried forever with the
    count perpetually reset to zero.
    """
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    "UPDATE github_webhook_deliveries "
                    "SET attempts = attempts + 1, last_attempt_at = now(), "
                    "    last_error_code = :code "
                    "WHERE id = :id AND status <> 'processed' "
                    "RETURNING attempts, delivery_id"
                ),
                {"id": delivery_row_id, "code": _error_code_slug(error_code_value)},
            )
        ).first()
        if row is None:
            return
        attempts, delivery_id = int(row[0]), str(row[1])
        exhausted = attempts >= MAX_DELIVERY_ATTEMPTS
        if exhausted:
            await connection.execute(
                text("UPDATE github_webhook_deliveries SET status = 'failed' WHERE id = :id"),
                {"id": delivery_row_id},
            )
    if exhausted:
        await _audit(
            engine,
            action="github.webhook.exhausted",
            result="failure",
            target_type="github_webhook",
            target_id=delivery_id,
            metadata={"attempts": attempts, "reason": _error_code_slug(error_code_value)},
        )


async def process_delivery(engine: AsyncEngine, delivery_row_id: uuid.UUID) -> str:
    """Run a claimed delivery's domain work and close it out ATOMICALLY.

    The row lock serialises concurrent processors of the same delivery, and
    the status flip commits in the same transaction as the domain work — so
    the row can never say `processed` about work that rolled back.

    Returns "processed", "duplicate" (someone else finished it first),
    "failed" (already dead-lettered, terminal), or raises after recording a
    bounded failed attempt.
    """
    result = _EnvelopeResult()
    try:
        async with engine.begin() as connection:
            locked = (
                await connection.execute(
                    text(
                        "SELECT status, event_type, envelope, scope_id "
                        "FROM github_webhook_deliveries WHERE id = :id FOR UPDATE"
                    ),
                    {"id": delivery_row_id},
                )
            ).one()
            status = str(locked[0])
            if status == "processed":
                return "duplicate"
            if status == "failed":
                # Terminal. The ceiling is a ceiling.
                return "failed"

            envelope = locked[2] if isinstance(locked[2], dict) else json.loads(str(locked[2]))
            scope_id = uuid.UUID(str(locked[3]))

            result = await _apply_envelope(
                connection, str(locked[1]), envelope, scope_id, delivery_row_id
            )
            await connection.execute(
                text(
                    "UPDATE github_webhook_deliveries "
                    "SET status = 'processed', processed_at = now(), "
                    "    attempts = attempts + 1, last_attempt_at = now(), "
                    "    last_error_code = NULL "
                    "WHERE id = :id"
                ),
                {"id": delivery_row_id},
            )
    except Exception as error:
        await _record_failed_attempt(engine, delivery_row_id, type(error).__name__)
        raise

    # Audits are written only once the domain transaction has committed, so
    # an audit never describes work that was rolled back.
    if result.conflicts:
        await _audit(
            engine,
            action="github.repository.state_conflict",
            result="denied",
            target_type="github_repository",
            metadata={
                "repositories": len(result.conflicts),
                "external_ids": sorted(result.conflicts)[:20],
            },
        )
    if result.unsupported_action:
        await _audit(
            engine,
            action="github.webhook.action_unsupported",
            result="denied",
            target_type="github_webhook",
            metadata={"event": result.event, "action": result.action},
        )
        # Handled and terminal, but NOT processed: no domain work happened,
        # and calling it processed would claim otherwise.
        return "unsupported"
    return "processed"


@dataclass
class _EnvelopeResult:
    """What one envelope actually changed."""

    touched: int = 0
    conflicts: list[int] = field(default_factory=list)
    unsupported_action: bool = False
    event: str = ""
    action: str = ""
    reconciliation_queued: bool = False


async def queue_installation_reconciliation(
    connection: AsyncConnection,
    *,
    scope_id: uuid.UUID,
    installation_external_id: int,
    reason: str,
) -> bool:
    """Record durable intent to re-derive an installation's membership.

    Written in the SAME transaction as the delivery it came from, so a
    truncated or ambiguous event cannot be acknowledged before the work
    that recovers its missing identities is durable. Repeated requests
    coalesce onto the single outstanding job.
    """
    inserted = (
        await connection.execute(
            text(
                """
                INSERT INTO github_reconciliation_jobs
                    (scope_id, installation_external_id, reason, status)
                VALUES (:scope_id, :installation_id, :reason, 'pending')
                ON CONFLICT DO NOTHING
                RETURNING id
                """
            ),
            {
                "scope_id": scope_id,
                "installation_id": installation_external_id,
                "reason": _error_code_slug(reason),
            },
        )
    ).first()
    return inserted is not None


async def _installation_repository_ids(
    connection: AsyncConnection, installation_external_id: int
) -> list[int]:
    """Every repository Drake already knows under this installation."""
    rows = (
        await connection.execute(
            text(
                "SELECT r.external_id FROM github_repositories r "
                "JOIN github_installations i ON i.id = r.installation_id "
                "WHERE i.provider = :provider AND i.external_id = :external_id"
            ),
            {"provider": PROVIDER, "external_id": installation_external_id},
        )
    ).all()
    return [int(row[0]) for row in rows]


async def _apply_envelope(
    connection: AsyncConnection,
    event: str,
    envelope: dict[str, Any],
    scope_id: uuid.UUID,
    delivery_row_id: uuid.UUID | None = None,
) -> _EnvelopeResult:
    """Apply one delivery according to its (event, action) plan."""
    action = str(envelope.get("action") or "")
    result = _EnvelopeResult(event=event, action=action)
    installation_external_id = envelope.get("installation_external_id")
    if installation_external_id is None:
        return result
    installation_external_id = int(installation_external_id)

    plan = lifecycle.plan_for(event, action)
    if not plan.supported:
        # An action we do not model produces NO domain mutation. Guessing
        # that an unknown action means "active" is how a future GitHub
        # action silently re-enables something.
        result.unsupported_action = True
        return result

    # `unchanged` means the state column is not written at all — not that
    # some default is written in its place.
    installation_row = await upsert_installation(
        connection,
        scope_id=scope_id,
        external_id=installation_external_id,
        account_login=str(envelope.get("account_login") or ""),
        state=None if plan.installation == "unchanged" else plan.installation,
    )
    parent_state = await installation_state(connection, installation_external_id) or "active"
    if delivery_row_id is not None:
        await connection.execute(
            text("UPDATE github_webhook_deliveries SET installation_id = :inst WHERE id = :id"),
            {"inst": installation_row, "id": delivery_row_id},
        )

    truncated = bool(envelope.get("truncated"))
    if truncated or plan.requires_reconciliation:
        # A truncated list is not a statement of membership, and an action
        # that says "something changed" without saying what needs the
        # provider consulted. Either way the intent is durable BEFORE this
        # delivery may be called finished.
        result.reconciliation_queued = await queue_installation_reconciliation(
            connection,
            scope_id=scope_id,
            installation_external_id=installation_external_id,
            reason="envelope_truncated" if truncated else plan.reason,
        )

    summaries = [
        summary
        for summary in (envelope.get("repositories") or [])
        if isinstance(summary, dict) and "external_id" in summary
    ]

    if plan.target == "all_of_installation":
        known = await _installation_repository_ids(connection, installation_external_id)
        if plan.outcome == "removed":
            await mark_access_removed(connection, known, "removed")
        elif plan.outcome == "suspended":
            await mark_access_removed(connection, known, "suspended")
        elif plan.outcome == "restored":
            await restore_access(connection, known)
            await resettle_states(connection, known)
        result.touched = len(known)
        return result

    if plan.target == "none":
        return result

    if truncated and plan.outcome == "removed":
        # A partial list must never drive a destructive membership change:
        # the repositories that fell outside the byte budget would look
        # like the ones that stayed. The queued reconciliation settles it.
        logger.warning(
            "github truncated removal deferred to reconciliation",
            extra={"installation": installation_external_id},
        )
        return result

    for summary in summaries:
        external_id = int(summary["external_id"])
        membership = str(summary.get("membership") or "present")
        outcome = "removed" if membership == "removed" else plan.outcome
        full_name = str(summary.get("full_name") or "")
        owner = webhook.summary_owner(summary)

        if outcome == "removed":
            await mark_access_removed(connection, [external_id], "removed")
            result.touched += 1
            continue

        if plan.reason == "repositories_added" and parent_state == "active":
            # The only announcement that legitimately restores access, and
            # only while the App is actually installed and unsuspended.
            # What state that leaves the repository in is still the
            # precedence chain's call, applied a few lines below.
            await restore_access(connection, [external_id])

        if owner and owner.lower() != catalog.ORGANIZATION.lower():
            # The repository has left the organization. Leaving it
            # accessible with stale metadata would be the worst outcome;
            # soft access loss keeps the history and stops the reads.
            await mark_access_removed(connection, [external_id], "removed")
            await queue_installation_reconciliation(
                connection,
                scope_id=scope_id,
                installation_external_id=installation_external_id,
                reason="repository_transferred_out",
            )
            result.touched += 1
            continue

        # Only what this message actually carried. Everything else keeps
        # whatever the last verified read established.
        repository_row_id, created = await upsert_repository(
            connection,
            installation_row_id=installation_row,
            scope_id=scope_id,
            external_id=external_id,
            full_name=full_name,
            name=webhook.summary_name(summary),
            owner_login=owner,
            node_id=str(summary.get("node_id") or ""),
            private=summary.get("private") if isinstance(summary.get("private"), bool) else None,
        )
        if not created:
            # A webhook is a notification, never evidence. Whatever it just
            # changed, the verdict we hold was gathered before it.
            await connection.execute(
                text(
                    "UPDATE github_repositories SET reconciliation_state = "
                    "CASE WHEN reconciliation_state = 'complete' THEN 'stale' "
                    "ELSE reconciliation_state END, updated_at = now() WHERE id = :id"
                ),
                {"id": repository_row_id},
            )
        gate = catalog.security_gate_for(full_name)
        try:
            await apply_announced_state(connection, repository_row_id, gate)
        except onboarding.InvalidTransitionError:
            # The state machine refused. Keep the current state and let the
            # rest of the batch proceed; the caller audits the conflict
            # rather than letting it become an unhandled exception.
            result.conflicts.append(external_id)
            continue
        result.touched += 1

    return result


async def drain_pending_deliveries(engine: AsyncEngine, limit: int = DRAIN_BATCH_SIZE) -> int:
    """Finish deliveries stranded by a crash, without waiting for GitHub.

    GitHub does not redeliver indefinitely, so a retry cannot be the only
    recovery path. `FOR UPDATE SKIP LOCKED` is what makes this safe across
    several API or worker instances: each claims a disjoint set in its own
    short transaction, and a worker that dies mid-batch releases its locks
    with the connection instead of stranding rows in a `processing` state
    nobody clears. Terminal (`failed`) rows are never selected.
    """
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id FROM github_webhook_deliveries "
                    "WHERE status = 'pending' AND attempts < :max "
                    "ORDER BY received_at "
                    "LIMIT :limit FOR UPDATE SKIP LOCKED"
                ),
                {"max": MAX_DELIVERY_ATTEMPTS, "limit": limit},
            )
        ).all()
        claimed = [uuid.UUID(str(row[0])) for row in rows]

    drained = 0
    for row_id in claimed:
        try:
            if await process_delivery(engine, row_id) == "processed":
                drained += 1
        except Exception:
            logger.warning("github delivery drain attempt failed", extra={"drained": drained})
    return drained


class DeliveryRecoveryWorker:
    """Bounded background loop that finishes stranded work.

    Owned by the application lifespan: started only when the integration is
    enabled, and cancelled deterministically on shutdown so no task
    outlives the process that created it.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        poll_seconds: float = 30.0,
        batch_size: int = DRAIN_BATCH_SIZE,
        reconciler: "GitHubReconciler | None" = None,
    ) -> None:
        self._engine = engine
        self._poll = max(0.01, poll_seconds)
        self._batch = max(1, batch_size)
        self._reconciler = reconciler
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="github-delivery-recovery")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            try:
                await drain_pending_deliveries(self._engine, self._batch)
                if self._reconciler is not None:
                    await drain_reconciliation_jobs(self._engine, self._reconciler, self._batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failing sweep must not kill the loop; the next tick
                # retries and the per-row attempt ceiling still applies.
                logger.warning("github recovery sweep failed")
            await asyncio.sleep(self._poll)


async def drain_reconciliation_jobs(
    engine: AsyncEngine,
    reconciler: "GitHubReconciler",
    limit: int = DRAIN_BATCH_SIZE,
    lease_seconds: int = JOB_LEASE_SECONDS,
) -> int:
    """Run outstanding installation-level reconciliation intents.

    Ownership is one job at a time. Claiming a batch up front and then
    running it serially means the last job's lease starts burning while the
    first job is still in the provider — by the time it runs, another
    worker may legitimately have taken it. So each job is claimed
    immediately before it runs, held by a lease that is renewed while the
    work is in flight, and finished only if the fencing token we claimed
    with is still the current one.
    """
    completed = 0
    for _ in range(max(1, limit)):
        claim = await _claim_next_job(engine, lease_seconds)
        if claim is None:
            return completed
        if await _run_claimed_job(engine, reconciler, claim, lease_seconds):
            completed += 1
    return completed


@dataclass(frozen=True)
class _JobClaim:
    job_id: uuid.UUID
    installation_external_id: int
    scope_id: uuid.UUID
    owner: str
    generation: int


async def _claim_next_job(engine: AsyncEngine, lease_seconds: int) -> "_JobClaim | None":
    """Take exclusive ownership of exactly one job.

    The attempt is spent HERE, atomically with the claim. A worker that
    dies immediately afterwards has still used one of its chances, which is
    what keeps a job that reliably kills its worker from being retried
    without end.
    """
    owner = str(uuid.uuid4())
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    UPDATE github_reconciliation_jobs
                    SET lease_owner = :owner,
                        lease_generation = lease_generation + 1,
                        lease_expires_at = now() + make_interval(secs => :lease),
                        attempts = attempts + 1,
                        last_attempt_at = now(),
                        updated_at = now()
                    WHERE id = (
                        SELECT id FROM github_reconciliation_jobs
                        WHERE status = 'pending'
                          AND attempts < :max
                          AND (lease_expires_at IS NULL OR lease_expires_at < now())
                        ORDER BY created_at
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, installation_external_id, scope_id, lease_generation, attempts
                    """
                ),
                {"owner": owner, "lease": max(0, lease_seconds), "max": MAX_DELIVERY_ATTEMPTS},
            )
        ).first()
    if row is None:
        return None
    return _JobClaim(
        job_id=uuid.UUID(str(row[0])),
        installation_external_id=int(row[1]),
        scope_id=uuid.UUID(str(row[2])),
        owner=owner,
        generation=int(row[3]),
    )


async def _renew_lease(engine: AsyncEngine, claim: "_JobClaim", lease_seconds: int) -> bool:
    """Extend our hold, but only while it is still ours."""
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE github_reconciliation_jobs "
                "SET lease_expires_at = now() + make_interval(secs => :lease), "
                "    updated_at = now() "
                "WHERE id = :id AND lease_owner = :owner AND lease_generation = :generation"
            ),
            {
                "id": claim.job_id,
                "owner": claim.owner,
                "generation": claim.generation,
                "lease": max(1, lease_seconds),
            },
        )
        return int(result.rowcount or 0) == 1


async def _run_claimed_job(
    engine: AsyncEngine,
    reconciler: "GitHubReconciler",
    claim: "_JobClaim",
    lease_seconds: int,
) -> bool:
    """Run one owned job, keeping the lease alive while it is in flight."""
    heartbeat_interval = max(0.2, min(5.0, max(1, lease_seconds) / 3))
    lost = asyncio.Event()

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(heartbeat_interval)
            if not await _renew_lease(engine, claim, lease_seconds):
                # Someone else owns it now. Stop renewing and let the
                # fencing check refuse our result.
                lost.set()
                return

    beat = asyncio.create_task(heartbeat(), name="github-job-lease")
    try:
        await reconciler.reconcile_installation(
            claim.installation_external_id, scope_id=claim.scope_id
        )
    except Exception as error:
        await _record_job_failure(engine, claim, type(error).__name__)
        return False
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat

    if lost.is_set():
        logger.warning(
            "github reconciliation job lease was lost mid-flight",
            extra={"installation": claim.installation_external_id},
        )
        return False

    # Fencing: only the current owner may close the job. A worker whose
    # lease was taken over writes nothing.
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                "UPDATE github_reconciliation_jobs "
                "SET status = 'processed', processed_at = now(), lease_owner = NULL, "
                "    lease_expires_at = NULL, updated_at = now() "
                "WHERE id = :id AND status = 'pending' "
                "  AND lease_owner = :owner AND lease_generation = :generation"
            ),
            {"id": claim.job_id, "owner": claim.owner, "generation": claim.generation},
        )
        applied = int(result.rowcount or 0) == 1
    return applied


async def _record_job_failure(engine: AsyncEngine, claim: "_JobClaim", code: str) -> None:
    """Record why the job failed, for the current owner only.

    The attempt was already spent at claim time, so nothing is counted
    twice here — and a crash between the two still costs one attempt.

    The lease is deliberately NOT released. Clearing it would make the job
    immediately re-claimable by the very sweep that just failed it, and one
    pass would burn the whole attempt budget on a job that is simply slow
    to recover. Letting the lease expire is what spaces the retries out.
    """
    exhausted = False
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    "UPDATE github_reconciliation_jobs "
                    "SET last_error_code = :code, updated_at = now() "
                    "WHERE id = :id AND status = 'pending' "
                    "  AND lease_owner = :owner AND lease_generation = :generation "
                    "RETURNING attempts"
                ),
                {
                    "id": claim.job_id,
                    "code": _error_code_slug(code),
                    "owner": claim.owner,
                    "generation": claim.generation,
                },
            )
        ).first()
        if row is not None and int(row[0]) >= MAX_DELIVERY_ATTEMPTS:
            await connection.execute(
                text("UPDATE github_reconciliation_jobs SET status = 'failed' WHERE id = :id"),
                {"id": claim.job_id},
            )
            exhausted = True
    if exhausted:
        await _audit(
            engine,
            action="github.reconciliation.exhausted",
            result="failure",
            target_type="github_installation",
            target_id=str(claim.installation_external_id),
            metadata={"attempts": MAX_DELIVERY_ATTEMPTS, "reason": _error_code_slug(code)},
        )


async def unknown_repository_ids(connection: AsyncConnection, external_ids: list[int]) -> list[int]:
    """Which of these permanent ids Drake has never seen."""
    if not external_ids:
        return []
    rows = (
        await connection.execute(
            text(
                "SELECT external_id FROM github_repositories "
                "WHERE provider = :provider AND external_id = ANY(:ids)"
            ),
            {"provider": PROVIDER, "ids": external_ids},
        )
    ).all()
    known = {int(row[0]) for row in rows}
    return sorted(set(external_ids) - known)


class InstallationScopeMismatchError(RuntimeError):
    """A delivery claims an installation that is bound to another scope."""


async def assert_installation_scope(
    connection: AsyncConnection, installation_external_id: int, scope_id: uuid.UUID
) -> None:
    """Refuse an event that contradicts the persisted installation/scope link.

    If the installation is already known under a different scope, the
    delivery is either misrouted or forged. Either way it must not be
    allowed to reassign ownership of that installation's data.
    """
    row = (
        await connection.execute(
            text(
                "SELECT scope_id FROM github_installations "
                "WHERE provider = :provider AND external_id = :external_id"
            ),
            {"provider": PROVIDER, "external_id": installation_external_id},
        )
    ).first()
    if row is not None and uuid.UUID(str(row[0])) != scope_id:
        raise InstallationScopeMismatchError("installation is bound to a different scope")


async def organization_scope_id(connection: AsyncConnection) -> uuid.UUID:
    row = (
        await connection.execute(
            text(
                "SELECT id FROM scopes WHERE scope_type = 'organization' AND external_ref = 'root'"
            )
        )
    ).first()
    if row is None:  # pragma: no cover - bootstrap invariant
        raise RuntimeError("organization scope is not seeded")
    return uuid.UUID(str(row[0]))


# --- installations ------------------------------------------------------


async def upsert_installation(
    connection: AsyncConnection,
    *,
    scope_id: uuid.UUID,
    external_id: int,
    account_login: str = "",
    account_external_id: int | None = None,
    account_type: str = "",
    app_slug: str = "",
    repository_selection: str | None = None,
    granted_permissions: dict[str, Any] | None = None,
    subscribed_events: list[str] | None = None,
    state: str | None = None,
    suspended_at: dt.datetime | None = None,
    reconciled: bool = False,
) -> uuid.UUID:
    """Insert or PATCH an installation row.

    Absent is not empty. A webhook envelope carries an installation id and
    an account login and nothing else, so writing the remaining columns
    from defaults erases what a real reconciliation established — the
    permissions, the events, the app slug, the account identity. Every
    optional field here therefore means "leave it alone" when omitted.

    `state=None` means the same thing: an event whose plan says the
    installation is unchanged must not touch the state column at all.
    Coercing an unrecognised value to `active` is how a repository rename
    used to revive a suspended or uninstalled App.

    `reconciled` gates `last_reconciled_at`: receiving a notification is
    not the same as having re-read the installation from the provider.
    """
    if state is not None and state not in ("active", "suspended", "deleted"):
        raise ValueError(f"unknown installation state: {state}")

    row = (
        await connection.execute(
            text(
                """
                INSERT INTO github_installations
                    (provider, external_id, scope_id, account_login, account_external_id,
                     account_type, app_slug, repository_selection, granted_permissions,
                     subscribed_events, state, suspended_at, last_reconciled_at)
                VALUES (:provider, :external_id, :scope_id, :login, :account_external_id,
                        :account_type, :app_slug, COALESCE(:selection, 'selected'),
                        COALESCE(CAST(:permissions AS jsonb), '{}'::jsonb),
                        COALESCE(CAST(:events AS jsonb), '[]'::jsonb),
                        COALESCE(:state, 'active'), :suspended_at,
                        CASE WHEN :reconciled THEN now() ELSE NULL END)
                ON CONFLICT (provider, external_id) DO UPDATE SET
                    account_login = COALESCE(
                        NULLIF(EXCLUDED.account_login, ''), github_installations.account_login
                    ),
                    account_external_id = COALESCE(
                        EXCLUDED.account_external_id, github_installations.account_external_id
                    ),
                    account_type = COALESCE(
                        NULLIF(EXCLUDED.account_type, ''), github_installations.account_type
                    ),
                    app_slug = COALESCE(
                        NULLIF(EXCLUDED.app_slug, ''), github_installations.app_slug
                    ),
                    repository_selection = COALESCE(
                        :selection, github_installations.repository_selection
                    ),
                    granted_permissions = COALESCE(
                        CAST(:permissions AS jsonb), github_installations.granted_permissions
                    ),
                    subscribed_events = COALESCE(
                        CAST(:events AS jsonb), github_installations.subscribed_events
                    ),
                    state = COALESCE(:state, github_installations.state),
                    suspended_at = CASE
                        WHEN :state = 'suspended' THEN now()
                        WHEN :state IS NOT NULL THEN NULL
                        ELSE github_installations.suspended_at
                    END,
                    last_reconciled_at = CASE
                        WHEN :reconciled THEN now()
                        ELSE github_installations.last_reconciled_at
                    END,
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "provider": PROVIDER,
                "external_id": external_id,
                "scope_id": scope_id,
                "login": account_login[:255],
                "account_external_id": account_external_id,
                "account_type": account_type[:64],
                "app_slug": app_slug[:128],
                "selection": (
                    repository_selection if repository_selection in ("all", "selected") else None
                ),
                "permissions": (
                    json.dumps(granted_permissions) if granted_permissions is not None else None
                ),
                "events": (
                    json.dumps(sorted(subscribed_events)) if subscribed_events is not None else None
                ),
                "state": state,
                "suspended_at": suspended_at,
                "reconciled": reconciled,
            },
        )
    ).one()
    return uuid.UUID(str(row[0]))


async def installation_state(connection: AsyncConnection, external_id: int) -> str | None:
    row = (
        await connection.execute(
            text(
                "SELECT state FROM github_installations "
                "WHERE provider = :provider AND external_id = :external_id"
            ),
            {"provider": PROVIDER, "external_id": external_id},
        )
    ).first()
    return None if row is None else str(row[0])


async def set_installation_state(connection: AsyncConnection, external_id: int, state: str) -> None:
    await connection.execute(
        text(
            """
            UPDATE github_installations
            SET state = :state,
                suspended_at = CASE WHEN :state = 'suspended' THEN now() ELSE NULL END,
                updated_at = now()
            WHERE provider = :provider AND external_id = :external_id
            """
        ),
        {"state": state, "provider": PROVIDER, "external_id": external_id},
    )


# --- repositories -------------------------------------------------------


async def upsert_repository(
    connection: AsyncConnection,
    *,
    installation_row_id: uuid.UUID,
    scope_id: uuid.UUID,
    external_id: int,
    full_name: str,
    name: str = "",
    owner_login: str = "",
    node_id: str = "",
    private: bool | None = None,
    visibility: str = "",
    archived: bool | None = None,
    disabled: bool | None = None,
    default_branch: str = "",
) -> tuple[uuid.UUID, bool]:
    """Insert or PATCH a repository by PERMANENT id.

    Returns (row id, created). A rename or transfer updates attributes on
    the SAME row, so history and audit stay attached.

    Optional attributes default to "not carried". A webhook envelope has no
    `archived`, `disabled`, `visibility` or `default_branch`, so passing
    them as concrete defaults would make every rename assert facts the
    message never stated. Only a verified provider read supplies them.
    """
    gate = catalog.security_gate_for(full_name)
    inserted = (
        await connection.execute(
            text(
                """
                INSERT INTO github_repositories
                    (provider, external_id, node_id, installation_id, scope_id,
                     owner_login, name, full_name, private, visibility, archived,
                     disabled, default_branch, onboarding_state, state_reason,
                     security_gate, access_state)
                VALUES (:provider, :external_id, :node_id, :installation_id, :scope_id,
                        :owner, :name, :full_name, :private_default, :visibility_default,
                        :archived_default, :disabled_default, :default_branch,
                        :initial_state, :initial_reason, :gate, 'accessible')
                ON CONFLICT (provider, external_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "provider": PROVIDER,
                "external_id": external_id,
                "node_id": node_id[:128],
                "installation_id": installation_row_id,
                "private_default": True if private is None else private,
                "visibility_default": visibility[:32] or "private",
                "archived_default": False if archived is None else archived,
                "disabled_default": False if disabled is None else disabled,
                "scope_id": scope_id,
                "owner": owner_login[:255],
                "name": (name or full_name.split("/")[-1])[:255],
                "full_name": full_name[:512],
                "private": private,
                "visibility": visibility[:32],
                "archived": archived,
                "disabled": disabled,
                "default_branch": default_branch[:255],
                "gate": gate,
                # Derived in Python so the SQL carries no untyped CASE
                # parameter — the initial state IS the gate decision.
                "initial_state": onboarding.BLOCKED if gate else onboarding.DISCOVERED,
                "initial_reason": (f"security_gate_{gate}" if gate else "awaiting_reconciliation")[
                    :64
                ],
            },
        )
    ).first()
    if inserted is not None:
        return uuid.UUID(str(inserted[0])), True

    updated = (
        await connection.execute(
            text(
                """
                UPDATE github_repositories SET
                    node_id = COALESCE(NULLIF(:node_id, ''), node_id),
                    installation_id = :installation_id,
                    owner_login = COALESCE(NULLIF(:owner, ''), owner_login),
                    name = COALESCE(NULLIF(:name, ''), name),
                    full_name = COALESCE(NULLIF(:full_name, ''), full_name),
                    -- Absent is not False, and absent is not 'private'. A
                    -- webhook envelope carries none of these, so writing
                    -- them from defaults would report every repository as
                    -- un-archived, enabled and on a guessed branch after
                    -- any rename.
                    private = COALESCE(:private, private),
                    visibility = COALESCE(NULLIF(:visibility, ''), visibility),
                    archived = COALESCE(:archived, archived),
                    disabled = COALESCE(:disabled, disabled),
                    default_branch = COALESCE(NULLIF(:default_branch, ''), default_branch),
                    -- A gate may be OPENED by an observation (a rename into
                    -- a gated name) but never closed by one. Closing it is a
                    -- manual operator process, so renaming away from the
                    -- gated name must not quietly unblock the repository.
                    security_gate = COALESCE(:gate, security_gate),
                    -- access_state is deliberately NOT written here. A
                    -- metadata update is not evidence that access was
                    -- restored; restoring it is a lifecycle transition of
                    -- its own (installation.unsuspend, or a membership
                    -- addition under an active installation).
                    updated_at = now()
                WHERE provider = :provider AND external_id = :external_id
                RETURNING id
                """
            ),
            {
                "provider": PROVIDER,
                "external_id": external_id,
                "node_id": node_id[:128],
                "installation_id": installation_row_id,
                "private_default": True if private is None else private,
                "visibility_default": visibility[:32] or "private",
                "archived_default": False if archived is None else archived,
                "disabled_default": False if disabled is None else disabled,
                "owner": owner_login[:255],
                "name": (name or full_name.split("/")[-1])[:255],
                "full_name": full_name[:512],
                "private": private,
                "visibility": visibility[:32],
                "archived": archived,
                "disabled": disabled,
                "default_branch": default_branch[:255],
                "gate": gate,
            },
        )
    ).one()
    return uuid.UUID(str(updated[0])), False


async def apply_state(
    connection: AsyncConnection,
    repository_row_id: uuid.UUID,
    target: onboarding.OnboardingState,
    reason: str,
) -> onboarding.Transition:
    """The writer of every derived onboarding_state change.

    Access loss is the one other path (`mark_access_removed`), and DISABLED
    is legal from every state, so it cannot violate the machine.
    """
    current = (
        await connection.execute(
            text("SELECT onboarding_state FROM github_repositories WHERE id = :id FOR UPDATE"),
            {"id": repository_row_id},
        )
    ).scalar_one()
    current_state = cast(onboarding.OnboardingState, str(current))
    change = onboarding.transition(current_state, target, reason)
    if change.changed:
        await connection.execute(
            text(
                "UPDATE github_repositories SET onboarding_state = :state, "
                "state_reason = :reason, updated_at = now() WHERE id = :id"
            ),
            {"state": change.next, "reason": reason[:64], "id": repository_row_id},
        )
    return change


async def apply_announced_state(
    connection: AsyncConnection,
    repository_row_id: uuid.UUID,
    security_gate: str | None,
) -> onboarding.Transition:
    """Re-derive state after an announcement, through the precedence chain.

    A webhook tells us a repository EXISTS; it is not evidence that the
    App is still installed, that access was restored, or that the last
    reconciliation was complete. All three are read from what we already
    hold, so an announcement can only ever move a repository as far as the
    strongest standing reason allows.
    """
    row = (
        await connection.execute(
            text(
                "SELECT r.last_error_code, r.access_state, r.reconciliation_state, i.state, "
                "r.security_gate FROM github_repositories r "
                "JOIN github_installations i ON i.id = r.installation_id "
                "WHERE r.id = :id"
            ),
            {"id": repository_row_id},
        )
    ).one()
    # A stored gate outranks whatever the caller derived from the announced
    # name: renaming away from a gated name does not close the gate, and an
    # observation must never be able to argue that it did.
    effective_gate = (row[4] and str(row[4])) or security_gate
    target, reason = onboarding.resolve_effective(
        security_gate=effective_gate,
        installation_state=str(row[3] or "active"),
        access_state=str(row[1] or "accessible"),
        reconciliation_state=str(row[2] or "never"),
        had_error=bool(row[0]),
    )
    return await apply_state(connection, repository_row_id, target, reason)


async def mark_access_removed(
    connection: AsyncConnection, external_ids: list[int], access_state: str
) -> list[str]:
    """Soft state only — the row and its history are never deleted."""
    if not external_ids:
        return []
    rows = (
        await connection.execute(
            text(
                """
                UPDATE github_repositories
                SET access_state = :access_state,
                    -- An open security gate outranks an access observation:
                    -- a blocked repository stays blocked, so losing sight of
                    -- it can never quietly downgrade the reason it is closed.
                    onboarding_state = CASE
                        WHEN security_gate IS NOT NULL THEN 'blocked'
                        ELSE 'disabled'
                    END,
                    -- While we cannot see it we hold no CURRENT evidence
                    -- at all — not stale evidence, none. Keeping `complete`
                    -- would let the precedence chain derive READY again the
                    -- moment access returns, from a reading taken before we
                    -- lost sight of the repository. The last-good snapshot
                    -- and `last_reconciled_at` are untouched; they record
                    -- history, not the present.
                    reconciliation_state = 'never',
                    state_reason = CASE
                        WHEN security_gate IS NOT NULL
                            THEN 'security_gate_' || security_gate
                        ELSE :reason
                    END,
                    updated_at = now()
                WHERE provider = :provider AND external_id = ANY(:ids)
                RETURNING full_name
                """
            ),
            {
                "access_state": access_state,
                "reason": f"access_{access_state}"[:64],
                "provider": PROVIDER,
                "ids": external_ids,
            },
        )
    ).all()
    return [str(row[0]) for row in rows]


async def resettle_states(connection: AsyncConnection, external_ids: list[int]) -> None:
    """Re-derive onboarding state for repositories whose access changed.

    Access is one input to the precedence chain, not a conclusion. After
    moving it, the state each repository should now be in is recomputed
    from everything the chain considers.
    """
    if not external_ids:
        return
    rows = (
        await connection.execute(
            text(
                "SELECT id, security_gate FROM github_repositories "
                "WHERE provider = :provider AND external_id = ANY(:ids)"
            ),
            {"provider": PROVIDER, "ids": external_ids},
        )
    ).all()
    for row in rows:
        with contextlib.suppress(onboarding.InvalidTransitionError):
            await apply_announced_state(connection, uuid.UUID(str(row[0])), row[1] and str(row[1]))


async def restore_access(connection: AsyncConnection, external_ids: list[int]) -> list[str]:
    """Access came back. Nothing else is asserted.

    This writes `access_state` ONLY. What the repository's onboarding state
    should now be is not this function's call — it depends on the security
    gate, the parent installation, and whether the current evidence is
    complete, all of which the precedence chain already knows. Writing
    `discovered` here would overrule that chain and quietly promote a
    repository whose evidence we know to be partial.
    """
    if not external_ids:
        return []
    rows = (
        await connection.execute(
            text(
                """
                UPDATE github_repositories
                SET access_state = 'accessible', updated_at = now()
                WHERE provider = :provider AND external_id = ANY(:ids)
                RETURNING full_name
                """
            ),
            {"provider": PROVIDER, "ids": external_ids},
        )
    ).all()
    return [str(row[0]) for row in rows]


# --- policy snapshots ---------------------------------------------------


async def store_policy_evaluation(
    connection: AsyncConnection,
    repository_row_id: uuid.UUID,
    evaluation: policy.PolicyEvaluation,
    *,
    dry_run: bool = True,
) -> uuid.UUID:
    row = (
        await connection.execute(
            text(
                """
                INSERT INTO github_policy_evaluations
                    (repository_id, profile, overall, blocking_count, unknown_count,
                     results, evidence_digest, dry_run)
                VALUES (:repository_id, :profile, :overall, :blocking, :unknown,
                        CAST(:results AS jsonb), :digest, :dry_run)
                RETURNING id
                """
            ),
            {
                "repository_id": repository_row_id,
                "profile": evaluation.profile,
                "overall": evaluation.overall,
                "blocking": evaluation.blocking_count,
                "unknown": evaluation.unknown_count,
                "results": json.dumps(evaluation.as_json()),
                "digest": evaluation.evidence_digest,
                "dry_run": dry_run,
            },
        )
    ).one()
    await connection.execute(
        text("UPDATE github_repositories SET last_policy_evaluated_at = now() WHERE id = :id"),
        {"id": repository_row_id},
    )
    return uuid.UUID(str(row[0]))


# --- reconciliation -----------------------------------------------------


@dataclass
class ReconcileReport:
    repositories_seen: int = 0
    repositories_created: int = 0
    repositories_blocked: int = 0
    repositories_failed: int = 0
    policy_evaluated: int = 0
    errors: list[str] | None = None


class GitHubReconciler:
    """Drives read-only reconciliation and dry-run policy evaluation."""

    def __init__(self, engine: AsyncEngine, client: GitHubClient) -> None:
        self._engine = engine
        self._client = client

    async def gather_policy_inputs(
        self,
        token: Any,
        owner: str,
        repo: str,
        repository: dict[str, Any],
        shortfall: list[str] | None = None,
    ) -> policy.PolicyInputs:
        """Fetch every fact a rule may need, converting each failure into
        an explicit "not determinable" reason instead of a silent gap.

        A permission the token did not actually grant is one of those
        reasons, recorded up front rather than rediscovered as a 403 per
        call — and never something a later PASS can be built on.
        """
        full_name = f"{owner}/{repo}"
        default_branch = str(repository.get("default_branch") or "")
        missing = sorted(set(shortfall or ()))
        administration_missing = (
            f"missing permission ({PERMISSION_HINTS['protection']})"
            if "administration" in missing
            else None
        )
        actions_missing = (
            f"missing permission ({PERMISSION_HINTS['workflows']})"
            if "actions" in missing
            else None
        )

        protection: dict[str, Any] | None = None
        protection_error: str | None = administration_missing
        if administration_missing is None:
            if default_branch:
                try:
                    protection = await self._client.get_branch_protection(
                        token, owner, repo, default_branch
                    )
                except GitHubNotFoundError:
                    protection = None
                    protection_error = None  # a real "no protection" answer
                except GitHubForbiddenError:
                    protection_error = f"missing permission ({PERMISSION_HINTS['protection']})"
                except GitHubError as error:
                    protection_error = error_code(error)
            else:
                protection_error = "default branch unknown"

        # Rules actually in effect on the default branch. The ruleset LIST
        # endpoint returns summaries without a `rules` member, so it cannot
        # answer "is this rule configured". This endpoint needs only
        # Metadata:read, which is proven present before we get here.
        branch_rules: list[dict[str, Any]] | None = None
        branch_rules_error: str | None = None
        if default_branch:
            try:
                branch_rules = await self._client.get_branch_rules(
                    token, owner, repo, default_branch
                )
            except GitHubForbiddenError:
                branch_rules_error = "missing permission (metadata:read)"
            except GitHubNotFoundError:
                branch_rules = []  # a real "no rules apply" answer
            except GitHubError as error:
                branch_rules_error = error_code(error)
        else:
            branch_rules_error = "default branch unknown"

        workflows: list[dict[str, Any]] | None = None
        workflows_error: str | None = actions_missing
        if actions_missing is None:
            try:
                workflows = await self._client.list_workflows(token, owner, repo)
            except GitHubForbiddenError:
                workflows_error = f"missing permission ({PERMISSION_HINTS['workflows']})"
            except GitHubError as error:
                workflows_error = error_code(error)

        environments: list[dict[str, Any]] | None = None
        environments_error: str | None = actions_missing
        environment_details: dict[str, dict[str, Any]] = {}
        environment_errors: dict[str, str] = {}
        if actions_missing is None:
            try:
                environments = await self._client.list_environments(token, owner, repo)
                for environment in environments:
                    name = str(environment.get("name") or "")
                    if not name:
                        continue
                    if not any(hint in name.lower() for hint in ("production", "prod", "live")):
                        continue
                    try:
                        environment_details[name] = await self._client.get_environment(
                            token, owner, repo, name
                        )
                    except GitHubForbiddenError:
                        # Recorded, not swallowed: a production environment
                        # we could not read must keep the aggregate off PASS.
                        environment_errors[name] = (
                            f"missing permission ({PERMISSION_HINTS['environments']})"
                        )
                    except GitHubError as error:
                        environment_errors[name] = error_code(error)
            except GitHubForbiddenError:
                environments_error = f"missing permission ({PERMISSION_HINTS['environments']})"
            except GitHubError as error:
                environments_error = error_code(error)

        security_analysis = repository.get("security_and_analysis")
        return policy.PolicyInputs(
            full_name=full_name,
            default_branch=default_branch,
            protection=protection,
            protection_error=protection_error,
            branch_rules=branch_rules,
            branch_rules_error=branch_rules_error,
            workflows=workflows,
            workflows_error=workflows_error,
            environments=environments,
            environments_error=environments_error,
            environment_details=environment_details,
            environment_errors=environment_errors,
            security_analysis=(security_analysis if isinstance(security_analysis, dict) else None),
            security_analysis_error=(
                None
                if isinstance(security_analysis, dict)
                else "security analysis settings are not visible to this installation"
            ),
            archived=bool(repository.get("archived")),
            missing_permissions=tuple(missing),
        )

    async def evaluate_repository(
        self,
        installation_external_id: int,
        full_name: str,
        profile: str = policy.DEFAULT_PROFILE,
        repository_external_id: int | None = None,
    ) -> "ReconcileResult":
        """Reconcile ONE repository, then evaluate it. Read-only throughout.

        The manual security gate is checked BEFORE any credential is
        minted, so a blocked repository never reaches the network.

        The token is requested for this repository's permanent id and for
        the read permissions this evaluation actually needs — never wider,
        and never reused from a differently scoped request.
        """
        gate = catalog.security_gate_for(full_name)
        if gate:
            raise SecurityGateBlockedError(full_name, gate)
        owner, _, repo = full_name.partition("/")
        token = await self._client.installation_token(
            installation_external_id,
            repository_ids=[repository_external_id] if repository_external_id else None,
            permissions=dict(EVALUATION_PERMISSIONS),
        )
        # A grant narrower than requested is carried into the evaluation as
        # missing evidence. It is never a reason to ask for more, and never
        # something a later PASS may be built on.
        shortfall = missing_permissions(token.permissions, dict(EVALUATION_PERMISSIONS))
        if "metadata" in shortfall:
            # Without Metadata:read nothing about the repository is
            # readable, so there is nothing honest to evaluate.
            raise PermissionShortfallError(full_name, shortfall)

        repository = await self._client.get_repository(token, owner, repo)
        inputs = await self.gather_policy_inputs(
            token, owner, repo, repository, shortfall=shortfall
        )
        evaluation = policy.evaluate(inputs, profile)
        return ReconcileResult(
            evaluation=evaluation,
            repository=repository,
            shortfall=tuple(sorted(shortfall)),
            complete=inputs.evidence_complete(),
        )

    async def reconcile_installation(
        self, installation_external_id: int, *, scope_id: uuid.UUID
    ) -> "InstallationSync":
        """Re-derive an installation's membership, inside its own scope.

        `scope_id` is passed in from the job or the persisted installation
        and verified against what we hold — falling back to the root scope
        would silently move another tenant's data into it.

        Membership sync is NOT policy evaluation. It establishes which
        repositories exist and what their observed attributes are; nothing
        here promotes anything to READY, and a change that invalidates
        previously gathered evidence marks it stale instead.
        """
        async with self._engine.connect() as connection:
            persisted = (
                await connection.execute(
                    text(
                        "SELECT scope_id FROM github_installations "
                        "WHERE provider = :provider AND external_id = :external_id"
                    ),
                    {"provider": PROVIDER, "external_id": installation_external_id},
                )
            ).first()
        if persisted is not None and uuid.UUID(str(persisted[0])) != scope_id:
            await _audit(
                self._engine,
                action="github.installation.scope_mismatch",
                result="denied",
                target_type="github_installation",
                target_id=str(installation_external_id),
                metadata={"reason": "job_scope_mismatch"},
            )
            raise InstallationScopeMismatchError(
                "reconciliation scope does not match the persisted installation"
            )

        try:
            detail = await self._client.get_installation(installation_external_id)
        except GitHubNotFoundError:
            # The provider no longer knows this installation for this App:
            # it was uninstalled while we were not listening. That is a real
            # answer, unlike a 403 or a timeout, so it is safe to act on.
            return await self._close_uninstalled(installation_external_id, scope_id)

        try:
            self._verify_installation_identity(detail, installation_external_id)
        except InstallationIdentityError as mismatch:
            await _audit(
                self._engine,
                action="github.installation.identity_mismatch",
                result="denied",
                target_type="github_installation",
                target_id=str(installation_external_id),
                metadata={"reason": mismatch.reason},
            )
            raise
        state = "suspended" if detail.get("suspended_at") else "active"

        # Membership projection needs only Metadata:read. Asking for
        # administration or actions here would request authority this step
        # has no use for.
        token = await self._client.installation_token(
            installation_external_id, permissions=dict(MEMBERSHIP_PERMISSIONS)
        )
        shortfall = missing_permissions(token.permissions, dict(MEMBERSHIP_PERMISSIONS))
        if shortfall:
            await _audit(
                self._engine,
                action="github.installation.permission_insufficient",
                result="denied",
                target_type="github_installation",
                target_id=str(installation_external_id),
                metadata={"missing": sorted(shortfall)},
            )
            raise PermissionShortfallError(str(installation_external_id), shortfall)

        # Raises if the listing was truncated, so a partial membership can
        # never be committed as complete.
        listed = await self._client.list_installation_repositories(token)

        # Validate the WHOLE listing before applying any of it. Skipping a
        # malformed entry would make the repository it describes look like
        # one that had vanished, and mark it removed.
        present: list[dict[str, Any]] = []
        malformed = 0
        for item in listed:
            external_id = item.get("id")
            full_name = str(item.get("full_name") or "")
            if not isinstance(external_id, int) or isinstance(external_id, bool):
                malformed += 1
                continue
            if not full_name or "/" not in full_name:
                malformed += 1
                continue
            if full_name.split("/")[0].lower() != catalog.ORGANIZATION.lower():
                # Outside the organization we govern: legitimately not ours,
                # not a malformed entry.
                continue
            present.append(item)
        if malformed:
            await _audit(
                self._engine,
                action="github.installation.membership_malformed",
                result="denied",
                target_type="github_installation",
                target_id=str(installation_external_id),
                metadata={"entries": malformed},
            )
            raise MembershipContractError(
                f"{malformed} membership entries were unusable; the listing is not trustworthy"
            )

        vanished: list[int] = []
        async with self._engine.begin() as connection:
            installation_row = await upsert_installation(
                connection,
                scope_id=scope_id,
                external_id=installation_external_id,
                account_login=str((detail.get("account") or {}).get("login") or ""),
                account_external_id=(detail.get("account") or {}).get("id"),
                account_type=str((detail.get("account") or {}).get("type") or ""),
                app_slug=str(detail.get("app_slug") or ""),
                repository_selection=str(detail.get("repository_selection") or "") or None,
                granted_permissions=(
                    detail.get("permissions")
                    if isinstance(detail.get("permissions"), dict)
                    else None
                ),
                subscribed_events=(
                    [str(item) for item in detail["events"]]
                    if isinstance(detail.get("events"), list)
                    else None
                ),
                state=state,
                reconciled=True,
            )
            known = set(await _installation_repository_ids(connection, installation_external_id))
            # One read of every existing projection, BEFORE anything is
            # written, so comparisons are against what we actually held.
            previous_by_id = await _repository_projections(connection, sorted(known))
            seen: set[int] = set()
            for item in present:
                external_id = int(item["id"])
                seen.add(external_id)
                full_name = str(item["full_name"])
                gate = catalog.security_gate_for(full_name)
                repository_row_id, created = await upsert_repository(
                    connection,
                    installation_row_id=installation_row,
                    scope_id=scope_id,
                    external_id=external_id,
                    full_name=full_name,
                    name=str(item.get("name") or full_name.split("/")[-1]),
                    owner_login=full_name.split("/")[0],
                    node_id=str(item.get("node_id") or ""),
                    private=bool(item.get("private", True)),
                    visibility=str(item.get("visibility") or ""),
                    archived=bool(item.get("archived", False)),
                    disabled=bool(item.get("disabled", False)),
                    default_branch=str(item.get("default_branch") or ""),
                )
                if gate:
                    # Gated: recorded as present, never read any further.
                    await connection.execute(
                        text(
                            "UPDATE github_repositories SET reconciliation_state = 'never', "
                            "updated_at = now() WHERE id = :id"
                        ),
                        {"id": repository_row_id},
                    )
                else:
                    await self._settle_membership_evidence(
                        connection,
                        repository_row_id,
                        item,
                        None if created else previous_by_id.get(external_id),
                    )
                with contextlib.suppress(onboarding.InvalidTransitionError):
                    await apply_announced_state(connection, repository_row_id, gate)

            vanished = sorted(known - seen)
            if vanished:
                await mark_access_removed(connection, vanished, "removed")

        return InstallationSync(
            installation_external_id=installation_external_id,
            state=state,
            present=len(present),
            removed=len(vanished),
        )

    @staticmethod
    async def _settle_membership_evidence(
        connection: AsyncConnection,
        repository_row_id: uuid.UUID,
        observed: dict[str, Any],
        previous: dict[str, Any] | None,
    ) -> None:
        """Decide whether previously gathered evidence still applies.

        `previous` is the projection read BEFORE anything was written. That
        matters: comparing after a partial update means a field the update
        already overwrote compares equal to itself, and the change that
        invalidated the evidence goes unnoticed.

        A membership sync reads repository ATTRIBUTES, not governance. If an
        attribute the evidence depended on has moved — the default branch
        above all, since every branch-scoped verdict was gathered against
        it — the stored verdict describes something that no longer exists.
        """
        if previous is None:
            await connection.execute(
                text(
                    "UPDATE github_repositories SET reconciliation_state = 'never', "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": repository_row_id},
            )
            return

        observed_full_name = str(observed.get("full_name") or "")
        observed_owner = (
            observed_full_name.split("/")[0]
            if "/" in observed_full_name
            else str(previous["owner_login"])
        )
        comparisons = (
            (observed_full_name or str(previous["full_name"]), str(previous["full_name"])),
            (observed_owner, str(previous["owner_login"])),
            (
                str(observed.get("default_branch") or previous["default_branch"]),
                str(previous["default_branch"]),
            ),
            (
                str(observed.get("visibility") or previous["visibility"]),
                str(previous["visibility"]),
            ),
            (bool(observed.get("private", previous["private"])), bool(previous["private"])),
            (bool(observed.get("archived", previous["archived"])), bool(previous["archived"])),
            (bool(observed.get("disabled", previous["disabled"])), bool(previous["disabled"])),
        )
        changed = any(now != before for now, before in comparisons)

        await update_repository_projection(connection, repository_row_id, observed)
        if changed:
            await connection.execute(
                text(
                    "UPDATE github_repositories SET reconciliation_state = 'stale', "
                    "updated_at = now() WHERE id = :id"
                ),
                {"id": repository_row_id},
            )

    @staticmethod
    def _verify_installation_identity(detail: dict[str, Any], expected: int) -> None:
        observed_id = detail.get("id")
        if not isinstance(observed_id, int) or isinstance(observed_id, bool):
            raise InstallationIdentityError(expected, "id_missing")
        if observed_id != expected:
            raise InstallationIdentityError(expected, "id_mismatch")
        login = str((detail.get("account") or {}).get("login") or "")
        if not login:
            raise InstallationIdentityError(expected, "account_missing")
        if login.lower() != catalog.ORGANIZATION.lower():
            raise InstallationIdentityError(expected, "account_mismatch")

    async def _close_uninstalled(
        self, installation_external_id: int, scope_id: uuid.UUID
    ) -> "InstallationSync":
        """A confirmed uninstall: close the installation and its access."""
        async with self._engine.begin() as connection:
            await upsert_installation(
                connection,
                scope_id=scope_id,
                external_id=installation_external_id,
                state="deleted",
                reconciled=True,
            )
            known = await _installation_repository_ids(connection, installation_external_id)
            if known:
                await mark_access_removed(connection, known, "removed")
        await _audit(
            self._engine,
            action="github.installation.uninstalled",
            result="success",
            target_type="github_installation",
            target_id=str(installation_external_id),
            metadata={"repositories": len(known)},
        )
        return InstallationSync(
            installation_external_id=installation_external_id,
            state="deleted",
            present=0,
            removed=len(known),
        )

    async def reconcile_repository(
        self,
        repository_row_id: uuid.UUID,
        installation_external_id: int,
        full_name: str,
        repository_external_id: int,
        profile: str = policy.DEFAULT_PROFILE,
    ) -> "ReconcileResult":
        """Re-derive one repository's projection AND evaluate it.

        The provider is asked about a PATH, but we mean a permanent id.
        Those are different things the moment a repository is renamed,
        transferred, or deleted and re-created at the same path — so the
        response is verified against the identity we meant before a single
        column is written.
        """
        # The gate is authoritative from whichever source has it OPEN. A
        # rename away from the gated name changes what the name derives,
        # but it is not permission to start talking to the provider — the
        # recorded gate has to be consulted first, before a token is even
        # looked up.
        gate = await effective_security_gate(self._engine, repository_row_id, full_name)
        if gate:
            raise SecurityGateBlockedError(full_name, gate)
        owner, _, repo = full_name.partition("/")
        token = await self._client.installation_token(
            installation_external_id,
            repository_ids=[repository_external_id],
            permissions=dict(EVALUATION_PERMISSIONS),
        )
        shortfall = missing_permissions(token.permissions, dict(EVALUATION_PERMISSIONS))
        if "metadata" in shortfall:
            await self._mark_incomplete(repository_row_id, "failed", "required_permission_missing")
            raise PermissionShortfallError(full_name, shortfall)

        async with self._engine.begin() as connection:
            # We are, right now, validating it. Saying so is what makes the
            # eventual READY a legal transition rather than a jump.
            with contextlib.suppress(onboarding.InvalidTransitionError):
                await apply_state(
                    connection,
                    repository_row_id,
                    onboarding.VALIDATING,
                    "reconciliation_started",
                )

        repository = await self._client.get_repository(token, owner, repo)
        await self._verify_repository_identity(
            repository_row_id, repository, repository_external_id, full_name
        )

        # The provider may have just told us the repository is now called
        # something that IS gated. The gate is derived from the name, so it
        # has to be re-derived here — before any policy subresource is read.
        observed_name = str(repository.get("full_name") or full_name)
        observed_gate = catalog.security_gate_for(observed_name)
        if observed_gate:
            async with self._engine.begin() as connection:
                await update_repository_projection(connection, repository_row_id, repository)
                await connection.execute(
                    text(
                        "UPDATE github_repositories SET security_gate = :gate, "
                        "reconciliation_state = 'never', updated_at = now() WHERE id = :id"
                    ),
                    {"gate": observed_gate, "id": repository_row_id},
                )
                await apply_announced_state(connection, repository_row_id, observed_gate)
            await _audit(
                self._engine,
                action="github.repository.security_gate_applied",
                result="denied",
                target_type="github_repository",
                target_id=str(repository_row_id),
                metadata={"gate": observed_gate, "full_name": observed_name[:255]},
            )
            raise SecurityGateBlockedError(observed_name, observed_gate)

        inputs = await self.gather_policy_inputs(
            token, owner, repo, repository, shortfall=shortfall
        )
        evaluation = policy.evaluate(inputs, profile)
        complete = inputs.evidence_complete()

        async with self._engine.begin() as connection:
            await update_repository_projection(connection, repository_row_id, repository)
            await store_policy_evaluation(connection, repository_row_id, evaluation, dry_run=True)
            await connection.execute(
                text(
                    "UPDATE github_repositories SET reconciliation_state = :state, "
                    "last_policy_evaluated_at = now(), "
                    "last_error_code = CASE WHEN :complete THEN NULL ELSE last_error_code END, "
                    "last_reconciled_at = CASE "
                    "  WHEN :complete THEN now() ELSE last_reconciled_at END, "
                    "updated_at = now() WHERE id = :id"
                ),
                {
                    "state": "complete" if complete else "partial",
                    "complete": complete,
                    "id": repository_row_id,
                },
            )
            # One place decides the resulting state, from the precedence
            # chain, so the endpoint and the worker cannot disagree.
            gate_row = (
                await connection.execute(
                    text("SELECT security_gate FROM github_repositories WHERE id = :id"),
                    {"id": repository_row_id},
                )
            ).one()
            with contextlib.suppress(onboarding.InvalidTransitionError):
                await apply_announced_state(
                    connection, repository_row_id, gate_row[0] and str(gate_row[0])
                )
        return ReconcileResult(
            evaluation=evaluation,
            repository=repository,
            shortfall=tuple(sorted(shortfall)),
            complete=complete,
        )

    async def _mark_incomplete(self, repository_row_id: uuid.UUID, state: str, reason: str) -> None:
        """Record that the CURRENT evidence is not complete.

        Kept separate from `last_reconciled_at`, which still records the
        last time a reconciliation actually succeeded. Conflating the two
        let a later webhook read an old success as proof that what we hold
        now is complete.
        """
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE github_repositories SET reconciliation_state = :state, "
                    "last_error_code = :reason, updated_at = now() WHERE id = :id"
                ),
                {"state": state, "reason": _error_code_slug(reason), "id": repository_row_id},
            )
            gate_row = (
                await connection.execute(
                    text("SELECT security_gate FROM github_repositories WHERE id = :id"),
                    {"id": repository_row_id},
                )
            ).one()
            with contextlib.suppress(onboarding.InvalidTransitionError):
                await apply_announced_state(
                    connection, repository_row_id, gate_row[0] and str(gate_row[0])
                )

    async def _verify_repository_identity(
        self,
        repository_row_id: uuid.UUID,
        repository: dict[str, Any],
        expected_external_id: int,
        expected_full_name: str,
    ) -> None:
        """The response must be about the repository we asked about."""
        observed_id = repository.get("id")
        if not isinstance(observed_id, int) or isinstance(observed_id, bool):
            await self._reject_identity(repository_row_id, "id_missing", expected_external_id, None)
            raise RepositoryIdentityError(expected_full_name, "id_missing")
        if observed_id != expected_external_id:
            # A different repository now lives at this path. Writing its
            # attributes onto our row would bind the wrong object to the
            # right identity.
            await self._reject_identity(
                repository_row_id, "id_mismatch", expected_external_id, observed_id
            )
            raise RepositoryIdentityError(expected_full_name, "id_mismatch")

        # The numeric id stays the identity, but a node id we already hold
        # and one the provider reports must agree: two different node ids
        # for the same number means one of them is not what we think it is.
        # An empty stored value is a legacy row, filled in from this
        # verified response rather than treated as a conflict.
        observed_node = str(repository.get("node_id") or "")
        async with self._engine.connect() as connection:
            stored_row = (
                await connection.execute(
                    text("SELECT node_id FROM github_repositories WHERE id = :id"),
                    {"id": repository_row_id},
                )
            ).first()
        stored_node = str(stored_row[0]) if stored_row is not None and stored_row[0] else ""
        if stored_node and observed_node and stored_node != observed_node:
            await self._reject_identity(
                repository_row_id, "node_id_mismatch", expected_external_id, observed_id
            )
            raise RepositoryIdentityError(expected_full_name, "node_id_mismatch")

        observed_full_name = str(repository.get("full_name") or "")
        observed_owner = observed_full_name.split("/")[0] if "/" in observed_full_name else ""
        if not observed_owner:
            await self._reject_identity(
                repository_row_id, "owner_missing", expected_external_id, observed_id
            )
            raise RepositoryIdentityError(expected_full_name, "owner_missing")
        if observed_owner.lower() != catalog.ORGANIZATION.lower():
            # It left the organization. Soft access loss, never READY.
            async with self._engine.begin() as connection:
                await mark_access_removed(connection, [expected_external_id], "removed")
                await connection.execute(
                    text(
                        "UPDATE github_repositories SET reconciliation_state = 'stale', "
                        "updated_at = now() WHERE id = :id"
                    ),
                    {"id": repository_row_id},
                )
            await _audit(
                self._engine,
                action="github.repository.transferred_out",
                result="denied",
                target_type="github_repository",
                target_id=str(repository_row_id),
                metadata={"observed_owner": observed_owner[:255]},
            )
            raise RepositoryIdentityError(expected_full_name, "owner_mismatch")

    async def _reject_identity(
        self,
        repository_row_id: uuid.UUID,
        reason: str,
        expected: int,
        observed: int | None,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE github_repositories SET reconciliation_state = 'failed', "
                    "last_error_code = 'identity_conflict', updated_at = now() WHERE id = :id"
                ),
                {"id": repository_row_id},
            )
            await apply_state(
                connection, repository_row_id, onboarding.BLOCKED, "identity_conflict"
            )
        await _audit(
            self._engine,
            action="github.repository.identity_mismatch",
            result="denied",
            target_type="github_repository",
            target_id=str(repository_row_id),
            metadata={"reason": reason, "expected": expected, "observed": observed},
        )


@dataclass(frozen=True)
class ReconcileResult:
    """The outcome of reconciling one repository."""

    evaluation: policy.PolicyEvaluation
    repository: dict[str, Any]
    shortfall: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class InstallationSync:
    installation_external_id: int
    state: str
    present: int
    removed: int


async def effective_security_gate(
    engine: AsyncEngine, repository_row_id: uuid.UUID, full_name: str
) -> str | None:
    """The gate that actually applies, before any network access.

    Two sources, and OPEN wins: the gate recorded on the row, and the gate
    the current name derives. Consulting only the name would let a rename
    decide when Drake may start calling the provider; consulting only the
    row would miss a repository that has just been renamed INTO a gated
    name. Closing a gate stays a manual operator action either way.
    """
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT security_gate FROM github_repositories WHERE id = :id"),
                {"id": repository_row_id},
            )
        ).first()
    persisted = str(row[0]) if row is not None and row[0] else None
    return persisted or catalog.security_gate_for(full_name)


async def _repository_projections(
    connection: AsyncConnection, external_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Snapshot the projection of each repository, before any write."""
    if not external_ids:
        return {}
    rows = (
        await connection.execute(
            text(
                "SELECT external_id, full_name, owner_login, default_branch, visibility, "
                "private, archived, disabled, node_id FROM github_repositories "
                "WHERE provider = :provider AND external_id = ANY(:ids)"
            ),
            {"provider": PROVIDER, "ids": external_ids},
        )
    ).all()
    return {
        int(row[0]): {
            "full_name": row[1],
            "owner_login": row[2],
            "default_branch": row[3],
            "visibility": row[4],
            "private": row[5],
            "archived": row[6],
            "disabled": row[7],
            "node_id": row[8],
        }
        for row in rows
    }


class MembershipContractError(RuntimeError):
    """The membership listing was not usable as a statement of membership."""


class RepositoryIdentityError(RuntimeError):
    """The provider answered about a repository we did not ask about."""

    def __init__(self, full_name: str, reason: str) -> None:
        super().__init__(f"provider identity did not match for {full_name}: {reason}")
        self.full_name = full_name
        self.reason = reason


class InstallationIdentityError(RuntimeError):
    """The provider answered about an installation we did not ask about."""

    def __init__(self, expected: int, reason: str) -> None:
        super().__init__(f"provider installation identity mismatch ({reason})")
        self.expected = expected
        self.reason = reason


class PermissionShortfallError(RuntimeError):
    """A required read permission was not granted to the installation."""

    def __init__(self, full_name: str, missing: list[str]) -> None:
        super().__init__(f"installation is missing required read permissions for {full_name}")
        self.full_name = full_name
        self.missing = sorted(missing)


async def update_repository_projection(
    connection: AsyncConnection, repository_row_id: uuid.UUID, repository: dict[str, Any]
) -> None:
    """Write the observed attributes a provider read just confirmed."""
    full_name = str(repository.get("full_name") or "")
    owner = full_name.split("/")[0] if "/" in full_name else ""
    await connection.execute(
        text(
            """
            UPDATE github_repositories
            SET full_name = COALESCE(NULLIF(:full_name, ''), full_name),
                name = COALESCE(NULLIF(:name, ''), name),
                owner_login = COALESCE(NULLIF(:owner, ''), owner_login),
                node_id = COALESCE(NULLIF(:node_id, ''), node_id),
                default_branch = COALESCE(NULLIF(:default_branch, ''), default_branch),
                private = :private,
                visibility = COALESCE(NULLIF(:visibility, ''), visibility),
                archived = :archived,
                disabled = :disabled,
                updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": repository_row_id,
            "full_name": full_name[:512],
            "name": str(repository.get("name") or "")[:255],
            "owner": owner[:255],
            "node_id": str(repository.get("node_id") or "")[:128],
            "default_branch": str(repository.get("default_branch") or "")[:255],
            "private": bool(repository.get("private", True)),
            "visibility": str(repository.get("visibility") or "")[:32],
            "archived": bool(repository.get("archived", False)),
            "disabled": bool(repository.get("disabled", False)),
        },
    )
