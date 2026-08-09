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

from drake_api.github_app import catalog as repo_catalog
from drake_api.github_app.onboarding_service import OnboardingError, load_repository_context
from drake_api.onboarding import service
from drake_api.settings import Settings

logger = logging.getLogger("drake_api.onboarding.gitops")

# The ONLY path Drake will ever propose. Enforced here and again by a CHECK
# constraint, because an allowlist that lives in one place is a convention
# and an allowlist that lives in two is a rule.
ALLOWED_PATH = ".drake/project.yaml"

BRANCH_PREFIX = "drake/onboarding"

MAX_ATTEMPTS = 5

#: How long a worker owns a request once it claims it.
#:
#: Must be comfortably LONGER than any provider call can take, or a second
#: worker takes the row over while the first is still talking to GitHub and
#: two identical pull requests get opened. It is also the recovery time: a
#: worker that dies mid-dispatch leaves the row leased, and nothing else may
#: touch it until this elapses.
#:
#: So it is a trade between duplicate work and stall time, and it is
#: deliberately settled in favour of no duplicates.
DISPATCH_LEASE_SECONDS = 600

#: Backoff before a retryable failure is due again. Shorter than the lease:
#: nothing is in flight while it waits.
RETRY_BACKOFF_SECONDS = 60

#: The marker that says a provider call is happening RIGHT NOW.
#:
#: `next_attempt_at` alone cannot carry this. A leased row and a row waiting
#: out a retry backoff are both "not due yet", and cancel must be able to
#: close the second while leaving the first alone — because by the time a
#: cancel commits, an in-flight call may already have created a branch, and
#: writing `cancelled` over that would be a claim about somebody's
#: repository that Drake cannot make.
DISPATCH_MARKER = "dispatch_in_flight"

#: The session states a pending request may still be delivered for. The
#: same set `service.ALLOWED_STATES["gitops"]` allows a request FROM — a
#: request that was legal to make stays legal to send only while the
#: session is still in one of them.
CLAIMABLE_SESSION_STATES = frozenset({"needs_review", "ready", "approved"})


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

    # ONE transaction, holding the session's row lock from the state check
    # to the durable request.
    #
    # It used to be two. The first checked the state and released the lock;
    # the second inserted the request. A cancel committing in that gap left
    # a pending request against a closed session, and the worker would push
    # to a repository on behalf of a session nobody had approved:
    #
    #     gitops: state check passes → lock released
    #     cancel: session becomes `cancelled`
    #     gitops: pending request written
    #     worker: provider call for a cancelled session
    #
    # Holding the lock across the insert makes the cancel wait, and then it
    # sees the request and cancels it too.
    async with engine.begin() as connection:
        session = await service.lock_session_for(connection, session_id, "gitops")
        repository_id = session.repository_id
        commit_sha = str(session.analyzed_commit_sha or "")
        if not commit_sha:
            raise OnboardingError(
                "analysis_required",
                "Analyse the repository first: a proposal needs a base commit.",
            )
        context = await load_repository_context(connection, repository_id)
        if context.security_gate or repo_catalog.security_gate_for(context.full_name):
            # Both sources, because a gate must not depend on which
            # mechanism set it.
            raise OnboardingError(
                "security_gate_open", "This repository is closed by a manual security gate."
            )
        draft = await draft_manifest(connection, session_id)

        digest = hashlib.sha256(draft.encode("utf-8")).hexdigest()
        key = idempotency_key(session_id, commit_sha, digest)

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


async def cancel_pending_for_session(connection: AsyncConnection, session_id: uuid.UUID) -> int:
    """Close any request that has not reached the provider yet.

    Called from `cancel`, in ITS transaction, while it holds the session's
    row lock. A request that survives its session is one the worker will
    happily push on behalf of a decision somebody withdrew.

    Only `pending` rows that are NOT in flight move. A request already
    delivered, or one a worker is calling the provider for at this moment,
    is a fact — and rewriting it would be a claim about somebody's
    repository that Drake is in no position to make. `next_attempt_at` is
    cleared so nothing re-claims what this closes.
    """
    result = await connection.execute(
        text(
            "UPDATE gitops_requests SET state = 'cancelled', "
            "error_code = 'session_cancelled', next_attempt_at = NULL, "
            "version = version + 1, updated_at = now() "
            "WHERE session_id = :session AND state = 'pending' "
            # Not one a worker has already taken in flight. By the time this
            # commits, that call may have created a branch — writing
            # `cancelled` over it would be a claim about somebody's
            # repository that Drake is in no position to make.
            #
            # A row merely waiting out a retry backoff IS closed: nothing is
            # in flight for it, so there is nothing to be wrong about.
            "AND error_code IS DISTINCT FROM :marker"
        ),
        {"session": session_id, "marker": DISPATCH_MARKER},
    )
    return int(result.rowcount or 0)


async def draft_manifest(connection: AsyncConnection, session_id: uuid.UUID) -> str:
    """Regenerate the proposal deterministically from the analysis.

    Regenerated rather than stored: what is reviewed and what is pushed then
    cannot diverge, and Drake keeps no copy of a repository file.
    """
    # The analysis is resolved through the session's newest plan, falling
    # back to one this session owns. An analysis row is unique per
    # `(repository, commit, analyzer_version)`, so a second session on the
    # same repository at the same commit reuses the first session's row —
    # joining on `session_id` alone found nothing and produced a draft with
    # an empty commit.
    row = (
        await connection.execute(
            text(
                "SELECT r.owner_login, r.name, r.default_branch, a.commit_sha "
                "FROM onboarding_sessions s "
                "JOIN github_repositories r ON r.id = s.repository_id "
                "LEFT JOIN onboarding_analyses a "
                "  ON a.id = (SELECT p.analysis_id FROM onboarding_plans p "
                "             WHERE p.session_id = s.id "
                "             ORDER BY p.plan_version DESC LIMIT 1) "
                "  OR a.session_id = s.id "
                "WHERE s.id = :id ORDER BY a.analyzed_at DESC NULLS LAST LIMIT 1"
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

    That is also where the last race lived. `_claim` validated the session
    and committed, and the provider was called afterwards — so a cancel
    landing in between still reached GitHub:

        claim:  session proposable, request pending, COMMIT
        cancel: session cancelled, request cancelled
        worker: provider.create_pull_request(...) anyway

    So a claim is now a LEASE, not a hand-off. The row stays `pending`,
    which is what cancel is allowed to close, and `_begin_dispatch` re-reads
    it — against the version the claim saw, on the session's CURRENT state —
    immediately before the call.

    In-flight is `state = 'pending' AND next_attempt_at IS NULL`: a request
    with no future attempt scheduled because one is happening right now.
    `active` could not carry that meaning — the schema requires a provider
    PR number for it, which is the point, since `active` asserts a pull
    request exists.

    That transition is the exact instant a request becomes in-flight.
    Before it, cancel wins and nothing is sent. After it, a branch may
    really exist, so cancel leaves it alone rather than claiming otherwise.
    """
    if not settings.github_gitops_pr_enabled:
        # Disabled means no work is claimed at all — not "claimed and then
        # skipped", which would still advance attempt counters.
        return 0

    processed = 0
    for item in await _claim(engine, limit):
        if not await _begin_dispatch(engine, item):
            # Cancelled, superseded, or the session moved while this request
            # waited its turn. Nothing is sent and nothing is rewritten.
            continue
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
            await _finish(engine, item, state="active", number=result.number, error=None)
        elif result.outcome == "retryable" and int(item["attempts"]) < MAX_ATTEMPTS:
            # Back to `pending` for the next attempt — and back to being
            # something a cancel may close, which is correct: nothing was
            # created, so there is nothing in flight to protect.
            await _finish(engine, item, state="pending", number=None, error=result.error_code)
            continue
        else:
            await _finish(engine, item, state="failed", number=None, error=result.error_code)
        processed += 1
    return processed


async def _begin_dispatch(engine: AsyncEngine, item: dict[str, Any]) -> bool:
    """Confirm this worker may still send, then mark the call in flight.

    A claim is a lease, so authority to send is only settled here — which
    means everything the claim checked has to be checked AGAIN, against the
    row as it is now:

    - the request is still `pending`, still leased to this worker (version),
      and the lease has not expired;
    - the session is still one a proposal may be sent for;
    - the repository's persisted gate is still closed;
    - and the static catalogue's gate too, which is a Python-side fact and
      so is checked before the statement runs.

    A gate that opened between claim and dispatch is exactly the case a gate
    exists for. It makes this a terminal refusal rather than a skip: the
    request is failed with `security_gate_open` and never reclaimed.
    """
    if repo_catalog.security_gate_for(str(item.get("full_name") or "")):
        await _refuse(engine, item, "security_gate_open")
        return False

    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    UPDATE gitops_requests AS g
                    SET error_code = :marker, version = g.version + 1, updated_at = now()
                    FROM onboarding_sessions AS s, github_repositories AS r
                    WHERE g.id = :id
                      AND g.version = :version
                      AND g.state = 'pending'
                      AND g.next_attempt_at > now()
                      AND s.id = g.session_id
                      AND s.state = ANY(:states)
                      AND r.id = g.repository_id
                      AND r.security_gate IS NULL
                    RETURNING g.version
                    """
                ),
                {
                    "id": item["id"],
                    "version": item["version"],
                    "marker": DISPATCH_MARKER,
                    "states": sorted(CLAIMABLE_SESSION_STATES),
                },
            )
        ).first()
    if row is None:
        # Cancelled, gated, superseded, or the lease was lost. Distinguish
        # the gate, because that one must not be retried.
        if await _repository_is_gated(engine, item):
            await _refuse(engine, item, "security_gate_open")
        return False
    # The version the provider call answers for. `_finish` writes only
    # against this, so a cancel or another worker is never overwritten.
    item["version"] = int(row[0])
    return True


async def _repository_is_gated(engine: AsyncEngine, item: dict[str, Any]) -> bool:
    async with engine.connect() as connection:
        gate = (
            await connection.execute(
                text(
                    "SELECT r.security_gate FROM gitops_requests g "
                    "JOIN github_repositories r ON r.id = g.repository_id WHERE g.id = :id"
                ),
                {"id": item["id"]},
            )
        ).scalar_one_or_none()
    return bool(gate)


async def _refuse(engine: AsyncEngine, item: dict[str, Any], code: str) -> None:
    """Close a request the worker may not send, without sending it.

    Fenced like every other write: only over the row this worker leased, and
    only while it is still `pending`, so a cancelled or already-finished row
    is never rewritten.
    """
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE gitops_requests SET state = 'failed', error_code = :code, "
                "next_attempt_at = NULL, version = version + 1, updated_at = now() "
                "WHERE id = :id AND version = :version AND state = 'pending'"
            ),
            {"id": item["id"], "version": item["version"], "code": code},
        )


async def _claim(engine: AsyncEngine, limit: int) -> list[dict[str, Any]]:
    """Take ownership of due requests, for a bounded time.

    A claim is a LEASE. It moves `next_attempt_at` into the future, so after
    this commits no other worker sees the row as due — and a worker that
    dies holding it blocks the row for the lease and no longer.

    Due is `next_attempt_at <= now()`. It used to also match `IS NULL`,
    which is what a row being dispatched looked like, so a second worker
    took over a live provider call and sent the same pull request twice.
    `SKIP LOCKED` cannot prevent that: the first worker's transaction
    commits before it talks to the provider.
    """
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT g.id, g.session_id, g.branch_name, g.base_commit_sha, g.attempts,
                           r.owner_login, r.name, r.default_branch, r.external_id,
                           i.external_id, r.security_gate, s.state, r.full_name, g.version
                    FROM gitops_requests g
                    JOIN github_repositories r ON r.id = g.repository_id
                    JOIN github_installations i ON i.id = r.installation_id
                    JOIN onboarding_sessions s ON s.id = g.session_id
                    WHERE g.state = 'pending'
                      AND g.next_attempt_at IS NOT NULL
                      AND g.next_attempt_at <= now()
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
            # Both gate sources, at claim time. A gate that opened after the
            # request was made must stop it, and which mechanism set the
            # gate is not something the worker should depend on.
            if row[10] or repo_catalog.security_gate_for(str(row[12])):
                await connection.execute(
                    text(
                        "UPDATE gitops_requests SET state = 'failed', "
                        "error_code = 'security_gate_open', next_attempt_at = NULL, "
                        "version = version + 1, updated_at = now() WHERE id = :id"
                    ),
                    {"id": row[0]},
                )
                continue
            if str(row[11]) not in CLAIMABLE_SESSION_STATES:
                # The session moved on — cancelled, imported, or re-opened
                # for another analysis. A request is an intent about ONE
                # reviewed state, and pushing it after that state changed
                # would act on a decision nobody currently holds.
                #
                # The in-request cancel cascade already closes these; this
                # is the second half of the same rule, for a row written
                # before that cascade existed or moved by another path.
                await connection.execute(
                    text(
                        "UPDATE gitops_requests SET state = 'cancelled', "
                        "error_code = 'session_not_proposable', next_attempt_at = NULL, "
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
                    "full_name": str(row[12]),
                    "version": int(row[13]),
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
            # Taking the lease IS the claim, and it returns the version each
            # worker owns. Everything written later is fenced on it.
            versions = {
                str(row[0]): int(row[1])
                for row in (
                    await connection.execute(
                        text(
                            "UPDATE gitops_requests SET attempts = attempts + 1, "
                            "version = version + 1, updated_at = now(), "
                            "next_attempt_at = now() + make_interval(secs => :lease) "
                            "WHERE id = ANY(:ids) RETURNING id, version"
                        ),
                        {
                            "ids": [entry["id"] for entry in claimed],
                            "lease": DISPATCH_LEASE_SECONDS,
                        },
                    )
                ).all()
            }
            for entry in claimed:
                entry["version"] = versions[str(entry["id"])]
        for entry in claimed:
            entry["content"] = await draft_manifest(connection, entry["session_id"])
    return claimed


async def _finish(
    engine: AsyncEngine,
    item: dict[str, Any],
    *,
    state: str,
    number: int | None,
    error: str | None,
) -> None:
    """Record the outcome — only over the row this worker took in flight.

    Guarded on the exact row this worker took in flight: same id, same
    version, still `pending`, still carrying THIS worker's in-flight marker.
    Without that it wrote by id alone and would happily resurrect a request
    somebody had cancelled, or overwrite a terminal row with an older
    worker's answer.

    A `retryable` outcome clears the marker and re-arms `next_attempt_at` to
    a short backoff, which returns the row to being due and to being
    something cancel may close — correct, because nothing is in flight to
    protect any more.
    """
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE gitops_requests
                SET state = :state, provider_pr_number = :number, error_code = :error,
                    next_attempt_at = CASE
                        WHEN :state = 'pending' THEN now() + make_interval(secs => :backoff)
                        ELSE NULL
                    END,
                    version = version + 1, updated_at = now()
                WHERE id = :id AND version = :version AND state = 'pending'
                  AND error_code = :marker
                """
            ),
            {
                "id": item["id"],
                "version": item["version"],
                "state": state,
                "number": number,
                "error": error,
                "marker": DISPATCH_MARKER,
                "backoff": RETRY_BACKOFF_SECONDS,
            },
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
