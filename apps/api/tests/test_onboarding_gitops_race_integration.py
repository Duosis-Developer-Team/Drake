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
from test_onboarding_integration import _bootstrap, _identity, golden_tree

pytestmark = pytest.mark.integration


async def _analysed_session(
    harness: Any, engine: AsyncEngine, row_id: uuidlib.UUID
) -> tuple[uuidlib.UUID, uuidlib.UUID, Any]:
    """A session with an analysis, and the settings that allow a proposal."""
    actor = await _identity(engine)
    created = await service.create_session(
        engine, harness.app.state.settings, repository_row_id=row_id, actor_identity_id=actor
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
async def test_a_request_racing_a_cancel_never_leaves_a_deliverable_row(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Two independent connections, released together, in both orders.

    Whichever wins, the invariant is the same: a cancelled session has no
    pending request, and the provider is never called on its behalf. The
    single transaction is what makes that true — the loser waits on the
    session's row lock instead of interleaving with the winner.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())
    session_id, actor, enabled = await _analysed_session(harness, engine, row_id)
    version = await _session_version(engine, session_id)

    barrier = asyncio.Barrier(2)

    async def propose() -> Any:
        await barrier.wait()
        return await gitops.request_pull_request(
            engine, enabled, session_id=session_id, actor_identity_id=actor
        )

    async def close() -> Any:
        await barrier.wait()
        return await service.cancel(engine, session_id=session_id, expected_version=version)

    results = await asyncio.gather(propose(), close(), return_exceptions=True)
    failures = [item for item in results if isinstance(item, BaseException)]

    # Cancel first: the proposal is refused by the state machine, with a
    # bounded code rather than a database error.
    # Request first: the proposal succeeds and the cancel closes it.
    for failure in failures:
        assert isinstance(failure, service.OnboardingError), failure
        assert failure.code in ("invalid_session_state", "version_conflict")
        assert failure.status == 409

    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT state FROM onboarding_sessions WHERE id = :s"), {"s": session_id}
            )
        ).scalar_one()
    assert state == "cancelled", "the cancel must win or the race is not the one being tested"

    rows = await _requests(engine, session_id)
    assert len(rows) <= 1, rows
    assert all(row[0] != "pending" for row in rows), rows

    # Whatever the ordering, the worker sends nothing.
    provider = gitops.RecordingProvider(number=12)
    assert await gitops.process_pending(engine, enabled, provider) == 0
    assert provider.calls == []


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
