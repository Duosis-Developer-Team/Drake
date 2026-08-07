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

    installation_row = await upsert_installation(
        connection,
        scope_id=scope_id,
        external_id=installation_external_id,
        account_login=str(envelope.get("account_login") or ""),
        state=plan.installation,
    )
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

        repository_row_id, _created = await upsert_repository(
            connection,
            installation_row_id=installation_row,
            scope_id=scope_id,
            external_id=external_id,
            full_name=full_name,
            name=webhook.summary_name(summary),
            owner_login=owner,
            node_id=str(summary.get("node_id") or ""),
            private=bool(summary.get("private", True)),
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
    engine: AsyncEngine, reconciler: "GitHubReconciler", limit: int = DRAIN_BATCH_SIZE
) -> int:
    """Run outstanding installation-level reconciliation intents."""
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id, installation_external_id FROM github_reconciliation_jobs "
                    "WHERE status = 'pending' AND attempts < :max "
                    "ORDER BY created_at LIMIT :limit FOR UPDATE SKIP LOCKED"
                ),
                {"max": MAX_DELIVERY_ATTEMPTS, "limit": limit},
            )
        ).all()
        claimed = [(uuid.UUID(str(row[0])), int(row[1])) for row in rows]

    completed = 0
    for job_id, installation_external_id in claimed:
        try:
            await reconciler.reconcile_installation(installation_external_id)
        except Exception as error:
            await _record_job_failure(engine, job_id, type(error).__name__)
            continue
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE github_reconciliation_jobs "
                    "SET status = 'processed', processed_at = now(), "
                    "    attempts = attempts + 1, last_attempt_at = now() "
                    "WHERE id = :id"
                ),
                {"id": job_id},
            )
        completed += 1
    return completed


async def _record_job_failure(engine: AsyncEngine, job_id: uuid.UUID, code: str) -> None:
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    "UPDATE github_reconciliation_jobs "
                    "SET attempts = attempts + 1, last_attempt_at = now(), "
                    "    last_error_code = :code "
                    "WHERE id = :id AND status = 'pending' RETURNING attempts"
                ),
                {"id": job_id, "code": _error_code_slug(code)},
            )
        ).first()
        if row is not None and int(row[0]) >= MAX_DELIVERY_ATTEMPTS:
            await connection.execute(
                text("UPDATE github_reconciliation_jobs SET status = 'failed' WHERE id = :id"),
                {"id": job_id},
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
    repository_selection: str = "selected",
    granted_permissions: dict[str, Any] | None = None,
    subscribed_events: list[str] | None = None,
    state: str = "active",
    suspended_at: dt.datetime | None = None,
) -> uuid.UUID:
    row = (
        await connection.execute(
            text(
                """
                INSERT INTO github_installations
                    (provider, external_id, scope_id, account_login, account_external_id,
                     account_type, app_slug, repository_selection, granted_permissions,
                     subscribed_events, state, suspended_at, last_reconciled_at)
                VALUES (:provider, :external_id, :scope_id, :login, :account_external_id,
                        :account_type, :app_slug, :selection, CAST(:permissions AS jsonb),
                        CAST(:events AS jsonb), :state, :suspended_at, now())
                ON CONFLICT (provider, external_id) DO UPDATE SET
                    account_login = EXCLUDED.account_login,
                    account_external_id = EXCLUDED.account_external_id,
                    account_type = EXCLUDED.account_type,
                    app_slug = EXCLUDED.app_slug,
                    repository_selection = EXCLUDED.repository_selection,
                    granted_permissions = EXCLUDED.granted_permissions,
                    subscribed_events = EXCLUDED.subscribed_events,
                    state = EXCLUDED.state,
                    suspended_at = EXCLUDED.suspended_at,
                    last_reconciled_at = now(),
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
                    repository_selection
                    if repository_selection in ("all", "selected")
                    else "selected"
                ),
                "permissions": json.dumps(granted_permissions or {}),
                "events": json.dumps(sorted(subscribed_events or [])),
                "state": state if state in ("active", "suspended", "deleted") else "active",
                "suspended_at": suspended_at,
            },
        )
    ).one()
    return uuid.UUID(str(row[0]))


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
    private: bool = True,
    visibility: str = "private",
    archived: bool = False,
    disabled: bool = False,
    default_branch: str = "",
) -> tuple[uuid.UUID, bool]:
    """Insert or reconcile a repository by PERMANENT id.

    Returns (row id, created). A rename or transfer updates attributes on
    the SAME row, so history and audit stay attached.
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
                        :owner, :name, :full_name, :private, :visibility, :archived,
                        :disabled, :default_branch, :initial_state, :initial_reason,
                        :gate, 'accessible')
                ON CONFLICT (provider, external_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "provider": PROVIDER,
                "external_id": external_id,
                "node_id": node_id[:128],
                "installation_id": installation_row_id,
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
                    owner_login = :owner,
                    name = :name,
                    full_name = :full_name,
                    private = :private,
                    visibility = :visibility,
                    archived = :archived,
                    disabled = :disabled,
                    default_branch = COALESCE(NULLIF(:default_branch, ''), default_branch),
                    security_gate = :gate,
                    access_state = 'accessible',
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
    """Re-derive state after a webhook announcement.

    A webhook tells us a repository EXISTS, not that everything we already
    learned about it is void. Deriving from the row's own facts is what
    keeps a re-delivery from dragging a READY repository back to
    DISCOVERED — while an open security gate still wins, because
    `resolve_target` puts it first.
    """
    row = (
        await connection.execute(
            text(
                "SELECT last_reconciled_at, last_error_code, access_state "
                "FROM github_repositories WHERE id = :id"
            ),
            {"id": repository_row_id},
        )
    ).one()
    target, reason = onboarding.resolve_target(
        security_gate=security_gate,
        access_state=str(row[2] or "accessible"),
        reconciled=row[0] is not None,
        had_error=bool(row[1]),
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


async def restore_access(connection: AsyncConnection, external_ids: list[int]) -> list[str]:
    """Access came back. Compliance knowledge did not.

    Repositories return to `discovered`, never straight to `ready`: an
    unsuspend says we may look again, not that what we would find is fine.
    A gated repository stays blocked regardless.
    """
    if not external_ids:
        return []
    rows = (
        await connection.execute(
            text(
                """
                UPDATE github_repositories
                SET access_state = 'accessible',
                    onboarding_state = CASE
                        WHEN security_gate IS NOT NULL THEN 'blocked'
                        ELSE 'discovered'
                    END,
                    state_reason = CASE
                        WHEN security_gate IS NOT NULL THEN 'security_gate_' || security_gate
                        ELSE 'awaiting_reconciliation'
                    END,
                    updated_at = now()
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

    async def reconcile_repository(
        self,
        repository_row_id: uuid.UUID,
        installation_external_id: int,
        full_name: str,
        repository_external_id: int,
        profile: str = policy.DEFAULT_PROFILE,
    ) -> "ReconcileResult":
        """Re-derive one repository's projection AND evaluate it.

        Reconciliation is what makes the projection true again; policy
        evaluation is a stage inside it, not the whole of it. A missed
        rename or a repository that quietly went archived is corrected
        here, on the permanent id, without waiting for a webhook.
        """
        result = await self.evaluate_repository(
            installation_external_id,
            full_name,
            profile,
            repository_external_id=repository_external_id,
        )
        async with self._engine.begin() as connection:
            await update_repository_projection(connection, repository_row_id, result.repository)
        return result

    async def reconcile_installation(self, installation_external_id: int) -> "InstallationSync":
        """Re-derive an installation's full repository membership.

        This is the recovery path for everything a webhook could not tell
        us: truncated envelopes, missed deliveries, drift while Drake was
        down. Membership is only applied when the provider listing came
        back COMPLETE — a partial page set is a reason to fail, never to
        conclude that the missing repositories are gone.
        """
        detail = await self._client.get_installation(installation_external_id)
        state = "active"
        if detail.get("suspended_at"):
            state = "suspended"

        token = await self._client.installation_token(
            installation_external_id, permissions=dict(EVALUATION_PERMISSIONS)
        )
        # Raises GitHubContractError if the listing was truncated, so a
        # partial membership can never be committed as complete.
        listed = await self._client.list_installation_repositories(token)

        present: list[dict[str, Any]] = []
        for item in listed:
            external_id = item.get("id")
            full_name = str(item.get("full_name") or "")
            if not isinstance(external_id, int) or not full_name:
                continue
            owner = full_name.split("/")[0] if "/" in full_name else ""
            if owner.lower() != catalog.ORGANIZATION.lower():
                # Outside the organization we govern: not ours to onboard.
                continue
            present.append(item)

        async with self._engine.begin() as connection:
            scope_id = await organization_scope_id(connection)
            installation_row = await upsert_installation(
                connection,
                scope_id=scope_id,
                external_id=installation_external_id,
                account_login=str((detail.get("account") or {}).get("login") or ""),
                state=state,
            )
            known = set(await _installation_repository_ids(connection, installation_external_id))
            seen: set[int] = set()
            for item in present:
                external_id = int(item["id"])
                seen.add(external_id)
                full_name = str(item["full_name"])
                repository_row_id, _created = await upsert_repository(
                    connection,
                    installation_row_id=installation_row,
                    scope_id=scope_id,
                    external_id=external_id,
                    full_name=full_name,
                    name=str(item.get("name") or full_name.split("/")[-1]),
                    owner_login=full_name.split("/")[0],
                    node_id=str(item.get("node_id") or ""),
                    private=bool(item.get("private", True)),
                )
                await update_repository_projection(connection, repository_row_id, item)
                gate = catalog.security_gate_for(full_name)
                with contextlib.suppress(onboarding.InvalidTransitionError):
                    await apply_announced_state(connection, repository_row_id, gate)

            # Anything we knew about that the provider no longer lists has
            # left the installation. Soft state: the row stays.
            vanished = sorted(known - seen)
            if vanished:
                await mark_access_removed(connection, vanished, "removed")

        return InstallationSync(
            installation_external_id=installation_external_id,
            state=state,
            present=len(present),
            removed=len(vanished),
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
