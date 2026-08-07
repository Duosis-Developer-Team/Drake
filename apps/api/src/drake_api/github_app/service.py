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
from drake_api.github_app import catalog, onboarding, policy
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


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str  # "new" | "duplicate" | "conflict"
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
) -> DeliveryOutcome:
    """Claim a delivery id. The UNIQUE index is the replay defence.

    Returns "new" for the single winner, "duplicate" for an identical
    replay, and "conflict" when the same delivery id arrives with a
    DIFFERENT payload digest — which is a tampering signal, not a retry.
    """
    inserted = (
        await connection.execute(
            text(
                """
                INSERT INTO github_webhook_deliveries
                    (delivery_id, event_type, payload_digest, envelope,
                     installation_external_id, repository_external_id, status)
                VALUES (:delivery_id, :event_type, :digest, CAST(:envelope AS jsonb),
                        :installation_id, :repository_id, 'accepted')
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
            },
        )
    ).first()
    if inserted is not None:
        return DeliveryOutcome(status="new", delivery_row_id=inserted[0])

    existing = (
        await connection.execute(
            text(
                "SELECT id, payload_digest FROM github_webhook_deliveries "
                "WHERE delivery_id = :delivery_id"
            ),
            {"delivery_id": delivery_id},
        )
    ).one()
    if str(existing[1]) != payload_digest:
        return DeliveryOutcome(status="conflict", delivery_row_id=existing[0])
    return DeliveryOutcome(status="duplicate", delivery_row_id=existing[0])


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

        rulesets: list[dict[str, Any]] | None = None
        rulesets_error: str | None = None
        try:
            rulesets = await self._client.list_rulesets(token, owner, repo)
        except GitHubForbiddenError:
            rulesets_error = f"missing permission ({PERMISSION_HINTS['rulesets']})"
        except GitHubNotFoundError:
            rulesets = []
        except GitHubError as error:
            rulesets_error = error_code(error)

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
                except GitHubError:
                    continue
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
            rulesets=rulesets,
            rulesets_error=rulesets_error,
            workflows=workflows,
            workflows_error=workflows_error,
            environments=environments,
            environments_error=environments_error,
            environment_details=environment_details,
            security_analysis=(security_analysis if isinstance(security_analysis, dict) else None),
            security_analysis_error=(
                None
                if isinstance(security_analysis, dict)
                else "security analysis settings are not visible to this installation"
            ),
            archived=bool(repository.get("archived")),
        )

    async def evaluate_repository(
        self, installation_external_id: int, full_name: str, profile: str = policy.DEFAULT_PROFILE
    ) -> policy.PolicyEvaluation:
        """Dry-run evaluation for ONE repository. Read-only throughout.

        The manual security gate is checked BEFORE any credential is
        minted, so a blocked repository never reaches the network.
        """
        gate = catalog.security_gate_for(full_name)
        if gate:
            raise SecurityGateBlockedError(full_name, gate)
        owner, _, repo = full_name.partition("/")
        token = await self._client.installation_token(installation_external_id)
        repository = await self._client.get_repository(token, owner, repo)
        inputs = await self.gather_policy_inputs(token, owner, repo, repository)
        return policy.evaluate(inputs, profile)
