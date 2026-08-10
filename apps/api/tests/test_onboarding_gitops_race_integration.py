"""A GitOps request must not outlive the session that justified it.

`request_pull_request` used to run in TWO transactions: the first took the
session's row lock, checked the state, and released it; the second wrote the
request. Cancel fits in that gap.

    gitops: state check passes → lock released
    cancel: session becomes `cancelled`
    gitops: pending request written
    worker: provider call for a session nobody approved

The worker could not catch it either — `_claim` re-checked the repository's
security gate and never re-checked the session's state.

Everything here uses `RecordingProvider`. No real GitHub client exists in
this slice and none is added by testing it.
"""

import asyncio
import contextlib
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from drake_api.onboarding import gitops, service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_github_integration import github_harness
from test_onboarding_integration import _bootstrap, _identity, _principal, golden_tree

pytestmark = pytest.mark.integration


async def _analysed_session(
    harness: Any, engine: AsyncEngine, row_id: uuidlib.UUID
) -> tuple[uuidlib.UUID, uuidlib.UUID, Any]:
    """A session with an analysis, and the settings that allow a proposal."""
    actor = await _identity(engine)
    created = await service.create_session(
        engine,
        harness.app.state.settings,
        repository_row_id=row_id,
        actor_identity_id=actor,
        principal=await _principal(harness, engine),
    )
    session_id = uuidlib.UUID(created["session_id"])
    await service.analyze(
        engine,
        harness.app.state.settings,
        harness.app.state.github_client,
        session_id=session_id,
    )
    enabled = harness.app.state.settings.model_copy(update={"github_gitops_pr_enabled": True})
    return session_id, actor, enabled


async def _requests(engine: AsyncEngine, session_id: uuidlib.UUID) -> list[tuple[str, str]]:
    async with engine.connect() as connection:
        return [
            (str(row[0]), str(row[1] or ""))
            for row in (
                await connection.execute(
                    text(
                        "SELECT state, error_code FROM gitops_requests "
                        "WHERE session_id = :s ORDER BY created_at"
                    ),
                    {"s": session_id},
                )
            ).all()
        ]


async def _session_version(engine: AsyncEngine, session_id: uuidlib.UUID) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT version FROM onboarding_sessions WHERE id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )


@pytest.mark.anyio
async def test_cancelling_a_session_closes_its_pending_request(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The ordinary case, which the race test then makes concurrent."""
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)

    created = await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )
    assert created["created"] is True
    assert await _requests(engine, session_id) == [("pending", "")]

    result = await service.cancel(
        engine,
        session_id=session_id,
        expected_version=await _session_version(engine, session_id),
    )
    assert result["state"] == "cancelled"
    assert result["gitops_requests_cancelled"] == 1
    assert await _requests(engine, session_id) == [("cancelled", "session_cancelled")]

    # And the worker finds nothing to send.
    provider = gitops.RecordingProvider(number=11)
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert provider.calls == []


@pytest.mark.anyio
async def test_cancel_first_refuses_the_request_and_sends_nothing(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Cancel takes the session's row lock first, deterministically.

    A shared `asyncio.Barrier` releases two coroutines at the same moment
    and then lets the scheduler pick an order — so it tests whichever order
    that run happened to produce, not both. Here the ordering is forced: the
    cancel's transaction holds the session row and does not commit until the
    proposal is provably waiting on it.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    version = await _session_version(engine, session_id)

    holding = asyncio.Event()
    release = asyncio.Event()

    async def cancel_holding_the_lock() -> None:
        async with engine.begin() as connection:
            # The same row lock `service.cancel` takes, held open.
            await connection.execute(
                text("SELECT id FROM onboarding_sessions WHERE id = :s FOR UPDATE"),
                {"s": session_id},
            )
            await connection.execute(
                text(
                    "UPDATE onboarding_sessions SET state = 'cancelled', "
                    "version = version + 1, updated_at = now() WHERE id = :s"
                ),
                {"s": session_id},
            )
            holding.set()
            await release.wait()
        # Commit happens on exit; the proposal is unblocked from here.

    async def propose() -> Any:
        await holding.wait()
        # The proposal now blocks on the row lock. Give it a moment to
        # actually reach that block, then let the cancel commit.
        task = asyncio.create_task(
            gitops.request_pull_request(
                engine, enabled, session_id=session_id, actor_identity_id=actor
            )
        )
        await asyncio.sleep(0.2)
        assert not task.done(), "the proposal must be waiting on the session's row lock"
        release.set()
        return await task

    canceller = asyncio.create_task(cancel_holding_the_lock())
    with pytest.raises(service.OnboardingError) as refused:
        await propose()
    await canceller

    # Refused on the state it found after waiting — not on a stale read.
    assert refused.value.code == "invalid_session_state"
    assert refused.value.status == 409
    assert await _requests(engine, session_id) == []

    provider = gitops.RecordingProvider(number=21)
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert provider.calls == []
    assert version >= 1


@pytest.mark.anyio
async def test_request_first_is_closed_by_the_cancel_that_follows(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The other ordering, forced the same way.

    The proposal commits first, so the request exists. The cancel then has
    to close it — atomically, in the transaction that cancels the session —
    or the worker would deliver a proposal for a withdrawn decision.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)

    created = await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )
    assert created["created"] is True
    assert await _requests(engine, session_id) == [("pending", "")]

    result = await service.cancel(
        engine,
        session_id=session_id,
        expected_version=await _session_version(engine, session_id),
    )
    assert result["gitops_requests_cancelled"] == 1
    assert await _requests(engine, session_id) == [("cancelled", "session_cancelled")]

    provider = gitops.RecordingProvider(number=22)
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert provider.calls == []


@pytest.mark.anyio
async def test_a_cancel_between_the_claim_and_the_provider_call_wins(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The last gap, and the reason a claim is a lease rather than a hand-off.

    `_claim` validated the session and committed; the provider call happened
    afterwards. A cancel landing in that window still reached GitHub, and
    `_finish` then overwrote the cancelled row by id.

    This stops the worker between the two — exactly where the race lived —
    commits a real cancel, and lets it continue.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    original = gitops._begin_dispatch
    cancelled_between: list[int] = []

    async def cancel_then_dispatch(*args: Any, **kwargs: Any) -> bool:
        # The claim has committed and the item is in memory. This is the
        # instant the old code called the provider.
        if not cancelled_between:
            cancelled_between.append(1)
            await service.cancel(
                engine,
                session_id=session_id,
                expected_version=await _session_version(engine, session_id),
            )
        return await original(*args, **kwargs)

    gitops._begin_dispatch = cancel_then_dispatch  # type: ignore[assignment]
    try:
        provider = gitops.RecordingProvider(number=23)
        processed = await gitops.process_pending(engine, enabled, provider)
    finally:
        gitops._begin_dispatch = original  # type: ignore[assignment]

    assert cancelled_between == [1], "the cancel has to land between claim and dispatch"
    assert processed == 0
    # The whole point: nothing was sent.
    assert provider.calls == []
    # And the cancelled row was not resurrected by `_finish`.
    assert await _requests(engine, session_id) == [("cancelled", "session_cancelled")]


@pytest.mark.anyio
async def test_finish_cannot_overwrite_a_row_that_moved_underneath_it(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`_finish` used to write by id alone.

    That is enough to resurrect a cancelled request as `active` — reporting
    an open pull request for a session somebody closed — or to let a slow
    worker's answer overwrite a newer one.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    async with engine.connect() as connection:
        request_id, version = (
            await connection.execute(
                text("SELECT id, version FROM gitops_requests WHERE session_id = :s"),
                {"s": session_id},
            )
        ).one()

    # Cancel, then let a worker that still holds the pre-cancel version try
    # to record a successful delivery.
    await service.cancel(
        engine,
        session_id=session_id,
        expected_version=await _session_version(engine, session_id),
    )
    await gitops._finish(
        engine,
        {"id": uuidlib.UUID(str(request_id)), "version": int(version)},
        state="active",
        number=99,
        error=None,
    )

    async with engine.connect() as connection:
        state, number = (
            await connection.execute(
                text("SELECT state, provider_pr_number FROM gitops_requests WHERE id = :id"),
                {"id": request_id},
            )
        ).one()
    assert state == "cancelled"
    assert number is None, "a cancelled request must not acquire a pull request number"


@pytest.mark.anyio
async def test_the_worker_refuses_a_request_whose_session_moved_on(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The second half of the rule, for a row that got past the first.

    The in-request cascade closes what cancel can see. A row written before
    that cascade existed, or moved by another path, still has to be caught
    at claim time — otherwise the guarantee depends on which code wrote the
    row rather than on what the session says now.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    # Move the session behind the cascade's back, exactly as an older row
    # would have been left.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE onboarding_sessions SET state = 'cancelled' WHERE id = :s"),
            {"s": session_id},
        )

    provider = gitops.RecordingProvider(number=13)
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert provider.calls == []
    assert await _requests(engine, session_id) == [("cancelled", "session_not_proposable")]


@pytest.mark.anyio
async def test_the_worker_refuses_a_request_whose_repository_became_gated(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Fail closed at claim time, from either gate source.

    A gate that opens between the request and the delivery is exactly the
    case the gate exists for, and which mechanism set it — the persisted
    column or the static catalogue — is not something the worker should
    depend on.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE github_repositories SET security_gate = 'manual_review' WHERE id = :r"),
            {"r": row_id},
        )

    provider = gitops.RecordingProvider(number=14)
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert provider.calls == []
    assert await _requests(engine, session_id) == [("failed", "security_gate_open")]


@pytest.mark.anyio
async def test_a_delivered_request_is_not_rewritten_by_a_later_cancel(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Only `pending` moves.

    A request that already reached the provider is a fact. Marking it
    `cancelled` afterwards would be a lie about what happened, and the
    branch it created would still exist.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    provider = gitops.RecordingProvider(number=15)
    assert await gitops.process_pending(engine, enabled, provider) == 1
    delivered = await _requests(engine, session_id)
    assert delivered[0][0] != "pending"

    result = await service.cancel(
        engine,
        session_id=session_id,
        expected_version=await _session_version(engine, session_id),
    )
    assert result["gitops_requests_cancelled"] == 0
    assert await _requests(engine, session_id) == delivered


# ===========================================================================
# the dispatch lease
# ===========================================================================


class BlockingProvider(gitops.RecordingProvider):
    """A provider that stops inside the call, on request.

    Two workers racing a live provider call is the whole point of the lease,
    and a provider that returns immediately never gives the second worker a
    window to race into.
    """

    def __init__(self, *, number: int = 1) -> None:
        super().__init__(number=number)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def create_pull_request(self, **kwargs: Any) -> gitops.PullRequestResult:
        self.calls.append(dict(kwargs))
        self.entered.set()
        await self.release.wait()
        return gitops.PullRequestResult("created", self._number, None)


async def _request_row(engine: AsyncEngine, session_id: uuidlib.UUID) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT id, state, error_code, attempts, version, next_attempt_at, "
                    "provider_pr_number FROM gitops_requests WHERE session_id = :s"
                ),
                {"s": session_id},
            )
        ).one()
    return {
        "id": row[0],
        "state": str(row[1]),
        "error_code": row[2],
        "attempts": int(row[3]),
        "version": int(row[4]),
        "next_attempt_at": row[5],
        "number": row[6],
    }


@pytest.mark.anyio
async def test_a_second_worker_cannot_take_over_a_live_dispatch(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Two workers, one in-flight request, exactly one provider call.

    `_claim` used to treat `next_attempt_at IS NULL` as due — which is what
    a row being dispatched looked like — so a second worker took the row
    over while the first was still talking to GitHub and opened the same
    pull request twice. `SKIP LOCKED` does not help: the first worker's
    claim commits BEFORE the provider call.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    provider_a = BlockingProvider(number=31)
    worker_a = asyncio.create_task(gitops.process_pending(engine, enabled, provider_a))
    await asyncio.wait_for(provider_a.entered.wait(), timeout=5)

    # A is now inside the provider call, holding the lease.
    leased = await _request_row(engine, session_id)
    assert leased["state"] == "pending"
    assert leased["error_code"] == gitops.DISPATCH_MARKER
    assert leased["attempts"] == 1

    provider_b = gitops.RecordingProvider(number=32)
    assert await gitops.process_pending(engine, enabled, provider_b) == 0
    assert provider_b.calls == [], "the lease must not be stealable while it is live"

    provider_a.release.set()
    assert await worker_a == 1
    assert len(provider_a.calls) == 1

    settled = await _request_row(engine, session_id)
    assert settled["state"] == "active"
    assert settled["number"] == 31
    assert settled["error_code"] is None
    assert settled["next_attempt_at"] is None
    # One claim, one attempt. A stolen lease would have shown two.
    assert settled["attempts"] == 1


@pytest.mark.anyio
async def test_a_worker_that_dies_mid_dispatch_is_recovered_when_its_lease_expires(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The other half of the lease: it has to end.

    A row owned by a worker that no longer exists must not be stranded. It
    is protected for the lease and reclaimed after it — and the reclaim goes
    through the provider's create-or-reuse path, so a pull request the dead
    worker had already opened is adopted rather than duplicated.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    # A worker claims and marks in flight, then dies: nothing records a
    # result. `process_pending` is stopped at exactly that point.
    original = gitops._finish
    died: list[int] = []

    async def die_before_finishing(*args: Any, **kwargs: Any) -> None:
        died.append(1)
        raise RuntimeError("worker died before recording the result")

    gitops._finish = die_before_finishing  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            await gitops.process_pending(engine, enabled, gitops.RecordingProvider(number=41))
    finally:
        gitops._finish = original  # type: ignore[assignment]
    assert died == [1]

    stranded = await _request_row(engine, session_id)
    assert stranded["state"] == "pending"
    assert stranded["error_code"] == gitops.DISPATCH_MARKER

    # While the lease is live, nobody else may touch it.
    blocked = gitops.RecordingProvider(number=42)
    assert await gitops.process_pending(engine, enabled, blocked) == 0
    assert blocked.calls == []

    # Expire the lease — the same thing time would do.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE gitops_requests SET next_attempt_at = now() - interval '1 second' "
                "WHERE session_id = :s"
            ),
            {"s": session_id},
        )

    # The provider reports the pull request the dead worker had already
    # opened. Create-or-reuse, not a second one.
    class ReusingProvider(gitops.RecordingProvider):
        async def create_pull_request(self, **kwargs: Any) -> gitops.PullRequestResult:
            self.calls.append(dict(kwargs))
            return gitops.PullRequestResult("exists", 41, None)

    recovering = ReusingProvider(number=41)
    assert await gitops.process_pending(engine, enabled, recovering) == 1
    assert len(recovering.calls) == 1

    settled = await _request_row(engine, session_id)
    assert settled["state"] == "active"
    assert settled["number"] == 41, "the existing pull request is adopted, not duplicated"
    assert settled["next_attempt_at"] is None
    # Two claims, two attempts, and bounded well under the retry ceiling.
    assert settled["attempts"] == 2
    assert settled["attempts"] < gitops.MAX_ATTEMPTS

    async with engine.connect() as connection:
        rows = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM gitops_requests WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    assert rows == 1, "recovery must not create a second request"


@pytest.mark.anyio
async def test_a_gate_opened_after_the_claim_stops_the_dispatch(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Authority to send is settled at dispatch, so the gate is checked there.

    `_claim` checks it too, but a claim is only a lease. A gate that closes
    a repository between the two is exactly what a gate is for, and the old
    dispatch check looked only at the session's state.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    original = gitops._begin_dispatch
    gated_between: list[int] = []

    async def gate_then_dispatch(*args: Any, **kwargs: Any) -> bool:
        # The claim has committed. This is where the old code went straight
        # to the provider.
        if not gated_between:
            gated_between.append(1)
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE github_repositories SET security_gate = 'manual_review' "
                        "WHERE id = :r"
                    ),
                    {"r": row_id},
                )
        return await original(*args, **kwargs)

    gitops._begin_dispatch = gate_then_dispatch  # type: ignore[assignment]
    try:
        provider = gitops.RecordingProvider(number=51)
        assert await gitops.process_pending(engine, enabled, provider) == 0
    finally:
        gitops._begin_dispatch = original  # type: ignore[assignment]

    assert gated_between == [1]
    assert provider.calls == []
    settled = await _request_row(engine, session_id)
    assert settled["state"] == "failed"
    assert settled["error_code"] == "security_gate_open"
    assert settled["next_attempt_at"] is None

    # Terminal: never reclaimed, even after any lease would have expired.
    again = gitops.RecordingProvider(number=52)
    assert await gitops.process_pending(engine, enabled, again) == 0
    assert again.calls == []


@pytest.mark.anyio
async def test_a_static_catalogue_gate_stops_the_dispatch_too(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other gate source, checked at the same boundary.

    Which mechanism closed a repository — the persisted column or the
    catalogue Drake ships — must not decide whether a pull request goes out.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    original = gitops._begin_dispatch
    gated: list[int] = []

    async def gate_then_dispatch(*args: Any, **kwargs: Any) -> bool:
        if not gated:
            gated.append(1)
            monkeypatch.setattr(
                gitops.repo_catalog, "security_gate_for", lambda _name: "manual_env_review"
            )
        return await original(*args, **kwargs)

    gitops._begin_dispatch = gate_then_dispatch  # type: ignore[assignment]
    try:
        provider = gitops.RecordingProvider(number=61)
        assert await gitops.process_pending(engine, enabled, provider) == 0
    finally:
        gitops._begin_dispatch = original  # type: ignore[assignment]

    assert gated == [1]
    assert provider.calls == []
    settled = await _request_row(engine, session_id)
    assert settled["state"] == "failed"
    assert settled["error_code"] == "security_gate_open"


# ===========================================================================
# the two ends of the lease
# ===========================================================================


@pytest.mark.anyio
async def test_a_slow_provider_is_cut_off_before_its_lease_expires(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lease only excludes if a call cannot outlive it.

    A provider that blocks longer than the lease lets it expire underneath
    itself: a second worker claims the row and both send the same pull
    request. `PullRequestProvider` is a protocol — any implementation may be
    slow or have no timeout of its own — so the bound belongs here.

    The real values are 120s under a 600s lease. This shrinks both, keeping
    the same ordering, so the boundary can actually be crossed in a test.
    """
    monkeypatch.setattr(gitops, "PROVIDER_CALL_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(gitops, "DISPATCH_LEASE_SECONDS", 3)
    monkeypatch.setattr(gitops, "RETRY_BACKOFF_SECONDS", 60)

    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    class NeverReturningProvider(gitops.RecordingProvider):
        async def create_pull_request(self, **kwargs: Any) -> gitops.PullRequestResult:
            self.calls.append(dict(kwargs))
            # Longer than the lease, never mind the timeout.
            await asyncio.sleep(30)
            raise AssertionError("the call must be cut off long before this")

    provider = NeverReturningProvider(number=71)
    started = asyncio.get_running_loop().time()
    assert await gitops.process_pending(engine, enabled, provider) == 0
    elapsed = asyncio.get_running_loop().time() - started

    # Cut off by the timeout, and comfortably before the lease would have
    # expired — which is the whole ordering guarantee.
    assert elapsed < gitops.DISPATCH_LEASE_SECONDS
    assert len(provider.calls) == 1

    settled = await _request_row(engine, session_id)
    assert settled["state"] == "pending"
    assert settled["error_code"] == "transport_timeout"
    assert settled["error_code"] != gitops.DISPATCH_MARKER, "the in-flight marker is cleared"
    assert settled["next_attempt_at"] is not None, "a retry is scheduled"
    assert settled["attempts"] == 1, "one attempt, not one per timeout"

    # And it is not due yet, so an immediate second worker sends nothing —
    # the timeout did not open a window the lease was holding shut.
    immediate = gitops.RecordingProvider(number=72)
    assert await gitops.process_pending(engine, enabled, immediate) == 0
    assert immediate.calls == []


@pytest.mark.anyio
async def test_repeated_crashes_stop_at_exactly_max_attempts(
    engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash loop must not be an unbounded provider loop.

    `MAX_ATTEMPTS` was only consulted after a provider result came back, so
    a worker dying between the call and `_finish` never reached it. The
    lease expired, the row was due, the next worker incremented `attempts`
    and called the provider again — for ever.
    """
    monkeypatch.setattr(gitops, "DISPATCH_LEASE_SECONDS", 1)

    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    original = gitops._finish

    async def die_before_finishing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("worker died before recording the result")

    provider = gitops.RecordingProvider(number=81)
    gitops._finish = die_before_finishing  # type: ignore[assignment]
    try:
        for _ in range(gitops.MAX_ATTEMPTS + 3):
            with contextlib.suppress(RuntimeError):
                await gitops.process_pending(engine, enabled, provider)
            # Expire the lease, the way time would.
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "UPDATE gitops_requests SET next_attempt_at = now() - "
                        "interval '1 second' WHERE session_id = :s AND state = 'pending'"
                    ),
                    {"s": session_id},
                )
    finally:
        gitops._finish = original  # type: ignore[assignment]

    # Exactly the ceiling, however many times the loop ran.
    assert len(provider.calls) == gitops.MAX_ATTEMPTS
    settled = await _request_row(engine, session_id)
    assert settled["attempts"] == gitops.MAX_ATTEMPTS
    assert settled["state"] == "failed"
    assert settled["error_code"] == "dispatch_attempts_exhausted"
    assert settled["next_attempt_at"] is None

    # Terminal: no later pass picks it up again.
    after = gitops.RecordingProvider(number=82)
    assert await gitops.process_pending(engine, enabled, after) == 0
    assert after.calls == []
    assert (await _request_row(engine, session_id))["attempts"] == gitops.MAX_ATTEMPTS


@pytest.mark.anyio
async def test_an_exhausted_request_is_retired_without_another_provider_call(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The same rule, reached directly rather than through a crash loop.

    A due row already at the ceiling must not buy one more provider call on
    its way to being retired — and must not be left `pending` either, where
    it would be skipped by every pass for ever.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE gitops_requests SET attempts = :max, "
                "next_attempt_at = now() - interval '1 second' WHERE session_id = :s"
            ),
            {"max": gitops.MAX_ATTEMPTS, "s": session_id},
        )

    provider = gitops.RecordingProvider(number=91)
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert provider.calls == []

    settled = await _request_row(engine, session_id)
    assert settled["state"] == "failed"
    assert settled["error_code"] == "dispatch_attempts_exhausted"
    assert settled["attempts"] == gitops.MAX_ATTEMPTS, "retiring it is not another attempt"
    assert settled["next_attempt_at"] is None

    async with engine.connect() as connection:
        rows = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM gitops_requests WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
    assert rows == 1


# ===========================================================================
# the real provider, driven through the worker
# ===========================================================================


class _CountingProvider:
    """Counts what actually reached the provider, whatever it then did."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    async def create_pull_request(self, **kwargs: Any) -> Any:
        self.calls += 1
        return await self._inner.create_pull_request(**kwargs)


async def _write_fake_for(
    engine: AsyncEngine, tmp_path: Path, row_id: uuidlib.UUID, session_id: uuidlib.UUID
) -> tuple[Any, Any, dict[str, Any]]:
    """A stateful GitHub-write fake wired to the real provider, for this row."""
    from drake_api.github_app.auth import GitHubAppAuth
    from drake_api.github_app.client import GitHubClient
    from drake_api.onboarding.github_provider import GitHubPullRequestProvider
    from drake_api.settings import Settings
    from fake_github_write import WriteFakeGitHub

    async with engine.connect() as connection:
        base_sha, branch = (
            await connection.execute(
                text(
                    "SELECT base_commit_sha, branch_name FROM gitops_requests WHERE session_id = :s"
                ),
                {"s": session_id},
            )
        ).one()
        external_id, installation_external_id, owner, name, default_branch = (
            await connection.execute(
                text(
                    "SELECT r.external_id, i.external_id, r.owner_login, r.name, r.default_branch "
                    "FROM github_repositories r "
                    "JOIN github_installations i ON i.id = r.installation_id "
                    "WHERE r.id = :r"
                ),
                {"r": row_id},
            )
        ).one()

    writes = WriteFakeGitHub(
        owner=str(owner),
        name=str(name),
        repository_id=int(external_id),
        installation_id=int(installation_external_id),
        default_branch=str(default_branch),
        base_sha=str(base_sha),
    )

    key = tmp_path / "provider-key.pem"
    if not key.exists():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key.write_bytes(
            rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    provider_settings = Settings(
        env="local",
        github_app_enabled=True,
        github_app_client_id="Iv1.local",
        github_app_private_key_file=str(key),
        github_api_base_url="https://api.github.test",
    )
    provider = GitHubPullRequestProvider(
        GitHubClient(
            provider_settings, GitHubAppAuth(provider_settings), transport=writes.transport()
        )
    )
    return writes, provider, {"branch": str(branch), "owner": str(owner), "name": str(name)}


@pytest.mark.anyio
async def test_the_worker_drives_the_real_provider_to_exactly_one_pull_request(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Request → lease → real provider → one draft pull request.

    The provider is the Sprint 12B implementation, over a stateful fake of
    the GitHub write API. No network, no credential, no real repository —
    and the assertion is on what the fake says was APPLIED, not on what the
    provider claims it did.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )
    writes, provider, target = await _write_fake_for(engine, tmp_path, row_id, session_id)
    branch, owner, name = target["branch"], target["owner"], target["name"]

    assert await gitops.process_pending(engine, enabled, provider) == 1
    assert writes.counts() == {"branches": 1, "commits": 1, "pulls": 1}
    assert branch.startswith("drake/onboarding/")
    assert writes.file_on(branch) is not None
    assert writes.file_on(branch, ".github/workflows/ci.yaml") is None
    assert writes.pulls[0]["draft"] is True

    settled = await _request_row(engine, session_id)
    assert settled["state"] == "active"
    assert settled["number"] == 101

    # A second pass sends nothing: the request is terminal, and the
    # provider would have reused rather than created in any case.
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert writes.counts()["pulls"] == 1

    # The link the browser gets is composed by Drake, from Drake's own
    # values — never from a provider response.
    async with harness.api_client() as api:
        await harness.login(api, "user-owner")
        entries = (await api.get(f"/v1/onboarding/sessions/{session_id}")).json()["gitops_requests"]
    assert len(entries) == 1
    assert entries[0]["pull_request_url"] == (f"https://github.com/{owner}/{name}/pull/101")


@pytest.mark.anyio
async def test_a_draft_that_no_longer_matches_its_digest_is_never_sent(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The content proposed must be the content the request was audited for.

    The draft is REGENERATED at claim time rather than stored — that is what
    keeps Drake from holding a copy of a repository file. The cost is that
    the generator, the projection, or the analysis can move underneath a
    pending request, and then the persisted `content_digest` describes one
    document while a different one would be pushed under its audited intent.

    Here the projection moves after the request is made. The digest is
    re-checked against the exact bytes about to leave, so nothing does: no
    installation token, no provider call, no HTTP request, no branch.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    await gitops.request_pull_request(
        engine, enabled, session_id=session_id, actor_identity_id=actor
    )
    writes, real, _ = await _write_fake_for(engine, tmp_path, row_id, session_id)
    provider = _CountingProvider(real)

    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT content_digest FROM gitops_requests WHERE session_id = :s"),
                {"s": session_id},
            )
        ).scalar_one()

    # The draft is generated from the repository projection, so moving the
    # projection moves the draft.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE github_repositories SET default_branch = 'release' WHERE id = :r"),
            {"r": row_id},
        )

    assert await gitops.process_pending(engine, enabled, provider) == 1

    assert provider.calls == 0, "the provider was never called"
    assert writes.calls == [], "nothing left the process — not even a token mint"
    assert writes.counts() == {"branches": 0, "commits": 0, "pulls": 0}

    settled = await _request_row(engine, session_id)
    assert settled["state"] == "failed"
    assert settled["error_code"] == "content_digest_mismatch"
    assert settled["number"] is None

    async with engine.connect() as connection:
        after = (
            await connection.execute(
                text("SELECT content_digest FROM gitops_requests WHERE session_id = :s"),
                {"s": session_id},
            )
        ).scalar_one()
    assert after == stored, "the audited digest is evidence; it is never rewritten"

    # Terminal, not retried: a re-analysis produces a request for what the
    # repository actually looks like now.
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert provider.calls == 0
