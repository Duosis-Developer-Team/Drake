"""Proposing a manifest to a repository, as a pull request and nothing else.

Drake never writes to a default branch. When a repository needs a
`.drake/project.yaml` it does not have, Drake opens a pull request and a
human merges it — or does not. That distinction is the whole feature:

    catalog apply   changes DRAKE
    GitOps PR       proposes a change to the REPOSITORY

They are separate lifecycles, separate permissions and separate audit
trails. Merging a PR is not an import, and an import does not merge a PR.

What the caller supplies: a session. What the caller does NOT supply:

- a branch name, a file path or a base repository. All three are composed
  server-side. A caller who could choose them could write anywhere the
  installation can reach, which is most of an organization.
- file content. The proposal is regenerated deterministically at send time
  from the analysis, so what is reviewed and what is pushed cannot diverge.
- a commit message, a PR title or a PR body beyond a bounded reason.

The feature is OFF by default. While it is off, no token is minted and no
provider call is made — not "the call fails", but "the call does not
happen", which is the only version of disabled that is worth having.
"""

import asyncio
import contextlib
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from drake_api.github_app.onboarding_service import OnboardingError, load_repository_context
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.onboarding.gitops")

# The ONLY path Drake will ever propose. Enforced here and again by a CHECK
# constraint, because an allowlist that lives in one place is a convention
# and an allowlist that lives in two is a rule.
ALLOWED_PATH = ".drake/project.yaml"

BRANCH_PREFIX = "drake/onboarding"

MAX_ATTEMPTS = 5


class GitOpsDisabledError(OnboardingError):
    """The feature is off. Nothing was contacted; nothing was minted."""

    def __init__(self) -> None:
        super().__init__(
            "gitops_disabled",
            "GitOps pull requests are not enabled for this deployment.",
            status=409,
        )


@dataclass(frozen=True)
class PullRequestResult:
    """One provider attempt, reduced to what is safe to store."""

    outcome: str  # created | exists | retryable | terminal
    number: int | None = None
    error_code: str | None = None


class PullRequestProvider(Protocol):
    """The seam a test substitutes a local fake for.

    Deliberately narrow: create-or-reuse one pull request for one branch on
    one repository. There is no method here that could delete a branch,
    force-push, merge, or write to a default branch.
    """

    async def create_pull_request(
        self,
        *,
        installation_id: int,
        repository_id: int,
        owner: str,
        name: str,
        base_branch: str,
        base_commit_sha: str,
        head_branch: str,
        file_path: str,
        content: str,
        title: str,
        body: str,
    ) -> PullRequestResult: ...


def branch_name(session_id: uuid.UUID) -> str:
    """Server-composed and deterministic.

    Deterministic so a retry targets the SAME branch and reuses the existing
    pull request instead of opening a second one for the same proposal.
    """
    return f"{BRANCH_PREFIX}/{str(session_id)[:8]}"


def idempotency_key(session_id: uuid.UUID, base_commit_sha: str, digest: str) -> str:
    """Stable for one proposal against one base commit.

    Includes the base commit, so a genuinely new proposal after the branch
    moved is a new request rather than a silent no-op against a stale one.
    """
    material = f"{session_id}:{base_commit_sha}:{digest}"
    return hashlib.sha256(material.encode()).hexdigest()[:48]


def pull_request_body(session_id: uuid.UUID, commit_sha: str) -> str:
    """Bounded, credential-free, and composed entirely from server values."""
    return (
        "Drake proposes adding a project manifest so this repository's "
        "observability intent lives in version control.\n\n"
        f"- Onboarding session: `{str(session_id)[:8]}`\n"
        f"- Analysed commit: `{commit_sha[:12]}`\n"
        f"- Path: `{ALLOWED_PATH}`\n\n"
        "Review the manifest before merging. Merging this pull request does "
        "not import anything into Drake; the catalog is applied separately "
        "and only by an authorized operator."
    )[:2000]


async def request_pull_request(
    engine: AsyncEngine,
    settings: Settings,
    *,
    session_id: uuid.UUID,
    actor_identity_id: uuid.UUID,
) -> dict[str, Any]:
    """Record a pending GitOps request. Audited before anything is called.

    No provider call happens here. The request is durable first, so a
    crashed worker retries it rather than losing an audited intent.
    """
    if not settings.github_gitops_pr_enabled:
        raise GitOpsDisabledError()

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT s.repository_id, s.analyzed_commit_sha, s.state "
                    "FROM onboarding_sessions s WHERE s.id = :id"
                ),
                {"id": session_id},
            )
        ).first()
        if row is None:
            raise OnboardingError("session_not_found", "No such onboarding session.", status=404)
        repository_id = uuid.UUID(str(row[0]))
        commit_sha = str(row[1] or "")
        if not commit_sha:
            raise OnboardingError(
                "analysis_required",
                "Analyse the repository first: a proposal needs a base commit.",
            )
        context = await load_repository_context(connection, repository_id)
        if context.security_gate:
            raise OnboardingError(
                "security_gate_open", "This repository is closed by a manual security gate."
            )
        draft = await _draft_manifest(connection, session_id)

    digest = hashlib.sha256(draft.encode("utf-8")).hexdigest()
    key = idempotency_key(session_id, commit_sha, digest)

    async with engine.begin() as connection:
        created = (
            await connection.execute(
                text(
                    """
                    INSERT INTO gitops_requests
                        (session_id, repository_id, actor_identity_id, branch_name, file_path,
                         base_commit_sha, content_digest, state, idempotency_key,
                         next_attempt_at)
                    VALUES (:session, :repo, :actor, :branch, :path, :base, :digest,
                            'pending', :key, now())
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "session": session_id,
                    "repo": repository_id,
                    "actor": actor_identity_id,
                    "branch": branch_name(session_id),
                    "path": ALLOWED_PATH,
                    "base": commit_sha,
                    "digest": digest,
                    "key": key,
                },
            )
        ).first()
        if created is None:
            existing = (
                await connection.execute(
                    text("SELECT id, state FROM gitops_requests WHERE idempotency_key = :key"),
                    {"key": key},
                )
            ).one()
            return {"id": str(existing[0]), "state": str(existing[1]), "created": False}
    return {"id": str(created[0]), "state": "pending", "created": True}


async def _draft_manifest(connection: AsyncConnection, session_id: uuid.UUID) -> str:
    """Regenerate the proposal deterministically from the analysis.

    Regenerated rather than stored: what is reviewed and what is pushed then
    cannot diverge, and Drake keeps no copy of a repository file.
    """
    row = (
        await connection.execute(
            text(
                "SELECT r.owner_login, r.name, r.default_branch, a.commit_sha "
                "FROM onboarding_sessions s "
                "JOIN github_repositories r ON r.id = s.repository_id "
                "LEFT JOIN onboarding_analyses a ON a.session_id = s.id "
                "WHERE s.id = :id ORDER BY a.analyzed_at DESC LIMIT 1"
            ),
            {"id": session_id},
        )
    ).one()
    from drake_api.github_app.scanner import ScanResult, generate_draft_manifest

    return generate_draft_manifest(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        ScanResult(commit_sha=str(row[3] or ""), default_branch=str(row[2])),
    )


async def process_pending(
    engine: AsyncEngine,
    settings: Settings,
    provider: PullRequestProvider,
    *,
    limit: int = 10,
) -> int:
    """One bounded pass over due GitOps requests.

    The provider call happens outside any request transaction: a slow GitHub
    must not hold a database lock, and a failing one must not roll back the
    audited request that caused it.
    """
    if not settings.github_gitops_pr_enabled:
        # Disabled means no work is claimed at all — not "claimed and then
        # skipped", which would still advance attempt counters.
        return 0

    processed = 0
    for item in await _claim(engine, limit):
        try:
            context_row = item["context"]
            result = await provider.create_pull_request(
                installation_id=int(context_row["installation_external_id"]),
                repository_id=int(context_row["external_id"]),
                owner=str(context_row["owner"]),
                name=str(context_row["name"]),
                base_branch=str(context_row["default_branch"]),
                base_commit_sha=str(item["base_commit_sha"]),
                head_branch=str(item["branch_name"]),
                file_path=ALLOWED_PATH,
                content=item["content"],
                title="Add Drake project manifest",
                body=pull_request_body(item["session_id"], str(item["base_commit_sha"])),
            )
        except Exception:
            # A provider that raised is not a provider that succeeded.
            logger.warning("gitops: provider call failed for one request")
            result = PullRequestResult("retryable", None, "transport_error")

        if result.outcome in ("created", "exists"):
            await _finish(engine, item["id"], state="active", number=result.number, error=None)
        elif result.outcome == "retryable" and int(item["attempts"]) < MAX_ATTEMPTS:
            # Left pending; the claim already scheduled the next attempt.
            continue
        else:
            await _finish(engine, item["id"], state="failed", number=None, error=result.error_code)
        processed += 1
    return processed


async def _claim(engine: AsyncEngine, limit: int) -> list[dict[str, Any]]:
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT g.id, g.session_id, g.branch_name, g.base_commit_sha, g.attempts,
                           r.owner_login, r.name, r.default_branch, r.external_id,
                           i.external_id, r.security_gate
                    FROM gitops_requests g
                    JOIN github_repositories r ON r.id = g.repository_id
                    JOIN github_installations i ON i.id = r.installation_id
                    WHERE g.state = 'pending'
                      AND (g.next_attempt_at IS NULL OR g.next_attempt_at <= now())
                    ORDER BY g.created_at
                    LIMIT :limit
                    FOR UPDATE OF g SKIP LOCKED
                    """
                ),
                {"limit": limit},
            )
        ).all()

        claimed: list[dict[str, Any]] = []
        for row in rows:
            if row[10]:
                # A gate opened after the request was made. Refuse rather
                # than push to a repository someone closed.
                await connection.execute(
                    text(
                        "UPDATE gitops_requests SET state = 'failed', "
                        "error_code = 'security_gate_open', next_attempt_at = NULL, "
                        "version = version + 1, updated_at = now() WHERE id = :id"
                    ),
                    {"id": row[0]},
                )
                continue
            claimed.append(
                {
                    "id": uuid.UUID(str(row[0])),
                    "session_id": uuid.UUID(str(row[1])),
                    "branch_name": str(row[2]),
                    "base_commit_sha": str(row[3]),
                    "attempts": int(row[4]) + 1,
                    "context": {
                        "owner": str(row[5]),
                        "name": str(row[6]),
                        "default_branch": str(row[7]),
                        "external_id": int(row[8]),
                        "installation_external_id": int(row[9]),
                    },
                }
            )
        if claimed:
            await connection.execute(
                text(
                    "UPDATE gitops_requests SET attempts = attempts + 1, "
                    "next_attempt_at = now() + interval '60 seconds' WHERE id = ANY(:ids)"
                ),
                {"ids": [entry["id"] for entry in claimed]},
            )
        for entry in claimed:
            entry["content"] = await _draft_manifest(connection, entry["session_id"])
    return claimed


async def _finish(
    engine: AsyncEngine,
    request_id: uuid.UUID,
    *,
    state: str,
    number: int | None,
    error: str | None,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE gitops_requests
                SET state = :state, provider_pr_number = :number, error_code = :error,
                    next_attempt_at = NULL, version = version + 1, updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": request_id, "state": state, "number": number, "error": error},
        )


class RecordingProvider:
    """A local fake. The only provider used anywhere in this sprint.

    Records what it was asked to do so a test can assert on the branch, the
    path and the base commit — and, more importantly, assert that nothing
    was asked of it at all when the feature is disabled.
    """

    def __init__(self, *, fail_with: str | None = None, number: int = 1) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_with = fail_with
        self._number = number

    async def create_pull_request(self, **kwargs: Any) -> PullRequestResult:
        self.calls.append(dict(kwargs))
        if self._fail_with is not None:
            return PullRequestResult("terminal", None, self._fail_with)
        return PullRequestResult("created", self._number, None)


class GitOpsWorker:
    """Lifespan-owned loop. Exists only when the feature flag is on."""

    def __init__(
        self, engine: AsyncEngine, settings: Settings, provider: PullRequestProvider
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._provider = provider
        self._interval = max(15.0, settings.gitops_worker_interval_seconds)
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name="gitops-worker")

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
                await process_pending(self._engine, self._settings, self._provider)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("gitops worker: cycle failed")
            await asyncio.sleep(self._interval)


def utcnow() -> datetime:
    return datetime.now(UTC)
