"""Persistence and reconciliation for the GitHub App integration.

Everything here is scope-bound and secret-free: rows carry identity and
observed attributes only. Repositories are keyed on GitHub's PERMANENT id
so renames and transfers reconcile onto the same row (ADR-0020 §1).
"""

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.audit import AuditEventData, record_audit_event
from drake_api.github_app import catalog, onboarding, policy, webhook
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
    scope_id: uuid.UUID | None = None,
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
    if str(existing[2]) == "processed":
        return DeliveryOutcome(status="duplicate", delivery_row_id=row_id)
    # pending / failed: unfinished work, so this redelivery is a chance to
    # finish it rather than a duplicate to wave through.
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

    Returns "processed", "duplicate" (someone else finished it first), or
    raises after recording a bounded failed attempt.
    """
    conflicts: list[int] = []
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
            if str(locked[0]) == "processed":
                return "duplicate"

            envelope = locked[2] if isinstance(locked[2], dict) else json.loads(str(locked[2]))
            scope_id = uuid.UUID(str(locked[3])) if locked[3] is not None else None
            if scope_id is None:
                scope_id = await organization_scope_id(connection)

            await _apply_envelope(connection, str(locked[1]), envelope, scope_id, delivery_row_id)
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

    if conflicts:
        await _audit(
            engine,
            action="github.repository.state_conflict",
            result="denied",
            target_type="github_repository",
            metadata={"repositories": len(conflicts)},
        )
    return "processed"


async def _apply_envelope(
    connection: AsyncConnection,
    event: str,
    envelope: dict[str, Any],
    scope_id: uuid.UUID,
    delivery_row_id: uuid.UUID | None = None,
) -> tuple[int, list[int]]:
    """The idempotent domain work for one delivery envelope.

    Returns (repositories touched, repositories the state machine refused).
    """
    installation_external_id = envelope.get("installation_external_id")
    if installation_external_id is None:
        return 0, []
    action = str(envelope.get("action") or "")
    installation_row = await upsert_installation(
        connection,
        scope_id=scope_id,
        external_id=int(installation_external_id),
        account_login=str(envelope.get("account_login") or ""),
        state=(
            "suspended"
            if action == "suspend"
            else "deleted"
            if action in ("deleted", "removed")
            else "active"
        ),
    )
    if delivery_row_id is not None:
        await connection.execute(
            text("UPDATE github_webhook_deliveries SET installation_id = :inst WHERE id = :id"),
            {"inst": installation_row, "id": delivery_row_id},
        )

    touched = 0
    conflicts: list[int] = []
    for summary in envelope.get("repositories") or []:
        if not isinstance(summary, dict) or "external_id" not in summary:
            continue
        if summary.get("membership") == "removed" or action in ("removed", "deleted"):
            await mark_access_removed(connection, [int(summary["external_id"])], "removed")
            continue
        repository_row_id, _created = await upsert_repository(
            connection,
            installation_row_id=installation_row,
            scope_id=scope_id,
            external_id=int(summary["external_id"]),
            full_name=str(summary.get("full_name") or ""),
            name=webhook.summary_name(summary),
            owner_login=webhook.summary_owner(summary),
            node_id=str(summary.get("node_id") or ""),
            private=bool(summary.get("private", True)),
        )
        gate = catalog.security_gate_for(str(summary.get("full_name") or ""))
        try:
            await apply_announced_state(connection, repository_row_id, gate)
        except onboarding.InvalidTransitionError:
            # The state machine refused. Keep the current state and let the
            # rest of the batch proceed; the caller audits the conflict
            # rather than letting it become an unhandled exception.
            conflicts.append(int(summary["external_id"]))
            continue
        touched += 1

    if action == "suspend":
        await set_installation_state(connection, int(installation_external_id), "suspended")
    return touched, conflicts


async def drain_pending_deliveries(engine: AsyncEngine, limit: int = DRAIN_BATCH_SIZE) -> int:
    """Finish deliveries stranded by a crash, without waiting for GitHub.

    GitHub does not redeliver indefinitely, so a retry cannot be the only
    recovery path. `SKIP LOCKED` keeps concurrent workers off each other's
    rows, and the attempt ceiling keeps a poison row from being picked up
    forever.
    """
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT id FROM github_webhook_deliveries "
                    "WHERE status = 'pending' AND attempts < :max "
                    "ORDER BY received_at LIMIT :limit"
                ),
                {"max": MAX_DELIVERY_ATTEMPTS, "limit": limit},
            )
        ).all()

    drained = 0
    for row in rows:
        try:
            if await process_delivery(engine, uuid.UUID(str(row[0]))) == "processed":
                drained += 1
        except Exception:
            logger.warning("github delivery drain attempt failed", extra={"drained": drained})
    return drained


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
                    onboarding_state = 'disabled',
                    state_reason = :reason,
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
        self, token: Any, owner: str, repo: str, repository: dict[str, Any]
    ) -> policy.PolicyInputs:
        """Fetch every fact a rule may need, converting each failure into
        an explicit "not determinable" reason instead of a silent gap."""
        full_name = f"{owner}/{repo}"
        default_branch = str(repository.get("default_branch") or "")

        protection: dict[str, Any] | None = None
        protection_error: str | None = None
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
        # answer "is this rule configured".
        branch_rules: list[dict[str, Any]] | None = None
        branch_rules_error: str | None = None
        if default_branch:
            try:
                branch_rules = await self._client.get_branch_rules(
                    token, owner, repo, default_branch
                )
            except GitHubForbiddenError:
                branch_rules_error = f"missing permission ({PERMISSION_HINTS['rulesets']})"
            except GitHubNotFoundError:
                branch_rules = []  # a real "no rules apply" answer
            except GitHubError as error:
                branch_rules_error = error_code(error)
        else:
            branch_rules_error = "default branch unknown"

        workflows: list[dict[str, Any]] | None = None
        workflows_error: str | None = None
        try:
            workflows = await self._client.list_workflows(token, owner, repo)
        except GitHubForbiddenError:
            workflows_error = f"missing permission ({PERMISSION_HINTS['workflows']})"
        except GitHubError as error:
            workflows_error = error_code(error)

        environments: list[dict[str, Any]] | None = None
        environments_error: str | None = None
        environment_details: dict[str, dict[str, Any]] = {}
        environment_errors: dict[str, str] = {}
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
                    # Recorded, not swallowed: a production environment we
                    # could not read must keep the aggregate off PASS.
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
        )

    async def evaluate_repository(
        self,
        installation_external_id: int,
        full_name: str,
        profile: str = policy.DEFAULT_PROFILE,
        repository_external_id: int | None = None,
    ) -> policy.PolicyEvaluation:
        """Dry-run evaluation for ONE repository. Read-only throughout.

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
        # A grant narrower than requested is reported through the rules that
        # depended on it; it is never a reason to ask for more.
        shortfall = missing_permissions(token.permissions, dict(EVALUATION_PERMISSIONS))
        repository = await self._client.get_repository(token, owner, repo)
        inputs = await self.gather_policy_inputs(token, owner, repo, repository)
        if shortfall:
            logger.info(
                "github installation token granted fewer permissions than requested",
                extra={"missing": sorted(shortfall)},
            )
        return policy.evaluate(inputs, profile)
