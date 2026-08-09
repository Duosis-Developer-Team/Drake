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
