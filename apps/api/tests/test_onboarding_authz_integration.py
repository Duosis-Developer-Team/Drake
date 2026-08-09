"""Target-scoped onboarding authorization (Sprint 12A.2a).

The bug these tests exist for: every session mutation asked two different
questions and never noticed they were about two different things.

    "does this user hold onboarding.apply ANYWHERE?"      → yes
    "can this user SEE session X?"                        → yes
    therefore                                             → apply session X

A user with `onboarding.view` on one project and `onboarding.apply` on
another passed both halves and could apply the first project's plan. The
permission came from one place and the target from another, which is the
shape of most authorization bugs that survive review: each check is correct
on its own.

Everything here is checked against the session's OWN scope.
"""

import asyncio
import uuid as uuidlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import grant, login_all, make_role
from test_github_integration import github_harness
from test_onboarding_integration import _bootstrap, _identity, _principal, golden_tree

pytestmark = pytest.mark.integration

VIEWER_ROLE = "Onboarding Viewer 12A2A"
OPERATOR_ROLE = "Onboarding Operator 12A2A"


async def _scope(engine: AsyncEngine, external_ref: str) -> uuidlib.UUID:
    """A project scope under the organisation root.

    Real hierarchy, not a flat pair: a grant on the parent still covers the
    child, which is the direction delegation actually runs. What must not
    happen is the reverse — a grant on one project reaching its parent or a
    sibling — and that is what the assertions below are about.
    """
    async with engine.begin() as connection:
        return uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO scopes (scope_type, external_ref, display_name, parent_id)
                            SELECT 'project', :ref, :ref,
                                   (SELECT id FROM scopes
                                     WHERE scope_type = 'organization' AND external_ref = 'root')
                            ON CONFLICT (scope_type, external_ref)
                              DO UPDATE SET display_name = EXCLUDED.display_name
                            RETURNING id
                            """
                        ),
                        {"ref": external_ref},
                    )
                ).scalar_one()
            )
        )


async def _second_repository(engine: AsyncEngine, sibling_id: uuidlib.UUID) -> uuidlib.UUID:
    """Another repository under the same installation.

    Needed because a partial unique index allows one ACTIVE session per
    repository — which is the right rule, and means a two-session fixture
    needs two repositories.
    """
    async with engine.begin() as connection:
        return uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO github_repositories "
                            "(installation_id, scope_id, external_id, full_name, owner_login, "
                            " name, default_branch, onboarding_state) "
                            "SELECT installation_id, scope_id, 912777, "
                            "'Duosis-Developer-Team/Sibling', owner_login, 'Sibling', "
                            "default_branch, onboarding_state FROM github_repositories "
                            "WHERE id = :id RETURNING id"
                        ),
                        {"id": sibling_id},
                    )
                ).scalar_one()
            )
        )


async def _session_in(
    engine: AsyncEngine, repository_id: uuidlib.UUID, scope_id: uuidlib.UUID
) -> uuidlib.UUID:
    async with engine.begin() as connection:
        return uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO onboarding_sessions "
                            "(repository_id, scope_id, state, created_by) "
                            "SELECT :repo, :scope, 'ready', id FROM identities "
                            "WHERE subject = 'user-owner' LIMIT 1 RETURNING id"
                        ),
                        {"repo": repository_id, "scope": scope_id},
                    )
                ).scalar_one()
            )
        )


async def _counts(engine: AsyncEngine, session_id: uuidlib.UUID) -> tuple[int, int, int, int]:
    """(projects, apply receipts, gitops requests, onboarding audit rows)."""
    async with engine.connect() as connection:
        projects = int(
            (await connection.execute(text("SELECT count(*) FROM projects"))).scalar_one()
        )
        receipts = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM onboarding_applies WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
        gitops = int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM gitops_requests WHERE session_id = :s"),
                    {"s": session_id},
                )
            ).scalar_one()
        )
        audits = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events WHERE action LIKE 'onboarding.%' "
                        "AND target_id = :t"
                    ),
                    {"t": str(session_id)},
                )
            ).scalar_one()
        )
    return projects, receipts, gitops, audits


async def _mixed_scope_world(engine: AsyncEngine, tmp_path: Path) -> Any:
    """One repository in project A, one in project B, one user across both.

    On A the user may look and nothing else. On B they hold every onboarding
    permission there is. Every assertion after this is about whether B's
    permissions leak onto A.
    """
    harness, fake = github_harness(tmp_path)
    row_id = await _bootstrap(harness, engine, fake, golden_tree())

    # The repository stays where the installation put it — the organisation
    # root — because a composite foreign key pins a repository's scope to
    # its installation's, and routing around a real invariant to build a
    # fixture would test something the schema does not allow anyway.
    #
    # Project A IS the organisation root, and project B is a project scope
    # beneath it. The user's read grant is at the root and their operator
    # grant is at B, so the question is whether B's permissions reach up to
    # a session at the root. They must not: inheritance runs downward.
    async with engine.connect() as connection:
        scope_a = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM scopes WHERE scope_type = 'organization' "
                            "AND external_ref = 'root'"
                        )
                    )
                ).scalar_one()
            )
        )
    scope_b = await _scope(engine, "onboarding-beta")

    await login_all(harness, ["user-split"])
    await make_role(harness, engine, VIEWER_ROLE, ["onboarding.view"])
    await make_role(
        harness,
        engine,
        OPERATOR_ROLE,
        ["onboarding.view", "onboarding.manage", "onboarding.apply", "onboarding.gitops"],
    )
    await grant(engine, harness, "user-split", VIEWER_ROLE, "organization", "root")
    await grant(engine, harness, "user-split", OPERATOR_ROLE, "project", "onboarding-beta")

    session_a = await _session_in(engine, row_id, scope_a)
    session_b = await _session_in(engine, await _second_repository(engine, row_id), scope_b)
    return harness, fake, session_a, session_b


def _mutations(session_id: uuidlib.UUID) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every endpoint that changes something about a session."""
    base = f"/v1/onboarding/sessions/{session_id}"
    return [
        ("POST", f"{base}/analyze", None),
        ("POST", f"{base}/approve", {"plan_version": 1, "expected_version": 1}),
        ("POST", f"{base}/apply", {"plan_version": 1, "idempotency_key": "idor-key-0001"}),
        ("POST", f"{base}/cancel", {"expected_version": 1}),
        ("POST", f"{base}/gitops-request", {}),
    ]


@pytest.mark.anyio
async def test_a_viewer_on_one_project_cannot_mutate_it_using_another_projects_grant(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The whole bug, in one test.

    `user-split` really does hold `onboarding.apply` — on project B. Session
    A is in project A, where they hold only `onboarding.view`. Before this
    change every mutation below succeeded in reaching the service, because
    the permission check never looked at which session was being acted on.
    """
    harness, fake, session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    before = await _counts(engine, session_a)
    fake.calls.clear()

    async with harness.api_client() as client:
        await harness.login(client, "user-split")
        me = (await client.get("/v1/me")).json()
        headers = {"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url}

        # Visible: the user does hold `onboarding.view` here.
        detail = await client.get(f"/v1/onboarding/sessions/{session_a}")
        assert detail.status_code == 200, detail.text

        # And every mutation flag is false, because they are answered about
        # THIS session's scope rather than about the user in general.
        body = detail.json()
        assert body["can_manage"] is False
        assert body["can_apply"] is False
        assert body["can_gitops"] is False

        for method, url, payload in _mutations(session_a):
            response = await client.request(method, url, json=payload, headers=headers)
            # The same 404 an unknown session gives. A 403 would confirm the
            # session exists, which is the fact the scoping protects.
            assert response.status_code == 404, f"{method} {url} → {response.status_code}"
            assert response.json()["error"]["message"] == "not found"

    # Nothing was called and nothing was written.
    assert fake.calls == [], fake.calls
    assert await _counts(engine, session_a) == before


@pytest.mark.anyio
async def test_a_guessed_session_id_is_indistinguishable_from_a_forbidden_one(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A 404 that differs by even one byte is an existence oracle."""
    harness, _fake, session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    invented = uuidlib.uuid4()

    async with harness.api_client() as client:
        await harness.login(client, "user-split")
        me = (await client.get("/v1/me")).json()
        headers = {"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url}
        for (method, real_url, payload), (_m, fake_url, _p) in zip(
            _mutations(session_a), _mutations(invented), strict=True
        ):
            real = await client.request(method, real_url, json=payload, headers=headers)
            unknown = await client.request(method, fake_url, json=payload, headers=headers)
            assert real.status_code == unknown.status_code == 404
            assert real.json()["error"]["code"] == unknown.json()["error"]["code"]
            assert real.json()["error"]["message"] == unknown.json()["error"]["message"]

        # The read surface answers the same way.
        assert (await client.get(f"/v1/onboarding/sessions/{invented}")).status_code == 404


@pytest.mark.anyio
async def test_the_same_user_may_act_where_the_grant_actually_is(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A guard that refuses everything is not a guard, it is an outage.

    The same user, the same credentials, a session in the project where
    their operator grant lives: the flags are true and the endpoints answer
    on the merits — the state machine's answer, not authorization's.
    """
    harness, _fake, _session_a, session_b = await _mixed_scope_world(engine, tmp_path)

    async with harness.api_client() as client:
        await harness.login(client, "user-split")
        me = (await client.get("/v1/me")).json()
        headers = {"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url}

        detail = await client.get(f"/v1/onboarding/sessions/{session_b}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["can_manage"] is True
        assert detail.json()["can_apply"] is True
        assert detail.json()["can_gitops"] is True

        # Authorization passes; these fail on their own contracts instead —
        # no plan to approve, not approved, and a state with no analysis.
        approve = await client.post(
            f"/v1/onboarding/sessions/{session_b}/approve",
            json={"plan_version": 1, "expected_version": 1},
            headers=headers,
        )
        assert approve.status_code == 404
        assert approve.json()["error"]["code"] == "plan_not_found"

        apply_response = await client.post(
            f"/v1/onboarding/sessions/{session_b}/apply",
            json={"plan_version": 1, "idempotency_key": "scoped-key-0001"},
            headers=headers,
        )
        assert apply_response.status_code == 404
        assert apply_response.json()["error"]["code"] == "plan_not_found"

        # Cancel is legal from `ready`, holds the row lock, and works.
        cancelled = await client.post(
            f"/v1/onboarding/sessions/{session_b}/cancel",
            json={"expected_version": 1},
            headers=headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["state"] == "cancelled"


@pytest.mark.anyio
async def test_a_parent_scope_grant_still_reaches_a_child_session(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Downward inheritance has to keep working.

    Scoping the check to the exact target would be easy to over-tighten into
    "the grant must name this scope", which would break every organisation-
    level operator and push people toward granting everything everywhere.
    """
    harness, _fake, session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-org"])
    await grant(engine, harness, "user-org", OPERATOR_ROLE, "organization", "root")

    async with harness.api_client() as client:
        await harness.login(client, "user-org")
        detail = await client.get(f"/v1/onboarding/sessions/{session_a}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["can_manage"] is True
    assert detail.json()["can_apply"] is True
    assert detail.json()["can_gitops"] is True


@pytest.mark.anyio
async def test_repository_candidates_are_scoped_to_the_permission_the_button_needs(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The picker lists what you can START, not what you can read.

    Listing read-visible repositories would make the Start button 404 for
    half the list, and would turn the onboarding screen into a repository-
    name enumerator for anyone with read access anywhere.
    """
    harness, _fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)

    async with harness.api_client() as client:
        await harness.login(client, "user-split")
        # The repository lives in project A, where this user may only look.
        listed = await client.get("/v1/onboarding/repositories")
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == []
        assert listed.json()["next_cursor"] is None

    await login_all(harness, ["user-org2"])
    await grant(engine, harness, "user-org2", OPERATOR_ROLE, "organization", "root")
    async with harness.api_client() as client:
        await harness.login(client, "user-org2")
        listed = await client.get("/v1/onboarding/repositories")
    assert listed.status_code == 200, listed.text
    items = {entry["full_name"]: entry for entry in listed.json()["items"]}
    assert "Duosis-Developer-Team/Hermes" in items
    # Byte order, not locale order, so the cursor means the same thing on
    # every database this ever runs against.
    assert list(items) == sorted(items)

    entry = items["Duosis-Developer-Team/Hermes"]
    # An open session already exists, so the honest answer is "go there",
    # not "start a second one beside it".
    assert entry["startable"] is False
    assert entry["reason_code"] == "session_in_progress"
    assert entry["active_session_id"] is not None

    # A repository Drake has only heard of is NOT startable: an analysis
    # needs a reconciled projection to start from, and `discovered` means
    # Drake knows the repository exists and nothing else about it.
    free = [entry for entry in items.values() if entry["active_session_id"] is None]
    assert free, items
    assert all(entry["startable"] is False for entry in free)
    assert all(entry["reason_code"] == "repository_not_ready" for entry in free)
    # Nothing provider-shaped travels to the browser.
    body = listed.text
    for forbidden in ("://", "installation", "node_id", "ghs_"):
        assert forbidden not in body, forbidden


# ===========================================================================
# the state machine, and the pagination the picker depends on
# ===========================================================================


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("state", "action", "payload", "code"),
    [
        ("imported", "analyze", None, "invalid_session_state"),
        ("imported", "cancel", {"expected_version": 1}, "already_imported"),
        ("cancelled", "analyze", None, "invalid_session_state"),
        ("cancelled", "cancel", {"expected_version": 1}, "invalid_session_state"),
        ("analyzing", "analyze", None, "invalid_session_state"),
        ("analyzing", "cancel", {"expected_version": 1}, "invalid_session_state"),
        # The feature flag answers before the state machine does, which is
        # the right order: a disabled capability is not a state problem.
        ("draft", "gitops-request", {}, "gitops_disabled"),
    ],
)
async def test_a_terminal_or_busy_session_refuses_the_action(
    engine: AsyncEngine, tmp_path: Path, state: str, action: str, payload: Any, code: str
) -> None:
    """Hiding a button is not a state machine.

    An endpoint that accepts whatever the UI happens to send can resurrect a
    cancelled session, re-open an imported one, or start a second analysis
    over a running one. Each of those rewrites a record somebody already
    acted on, and none of them is prevented by the button being grey.
    """
    harness, fake, session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-state"])
    await grant(engine, harness, "user-state", OPERATOR_ROLE, "organization", "root")
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE onboarding_sessions SET state = :state WHERE id = :id"),
            {"state": state, "id": session_a},
        )
    before = await _counts(engine, session_a)
    fake.calls.clear()

    async with harness.api_client() as client:
        await harness.login(client, "user-state")
        me = (await client.get("/v1/me")).json()
        response = await client.post(
            f"/v1/onboarding/sessions/{session_a}/{action}",
            json=payload,
            headers={"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url},
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code
    # A refused transition is not a partial one.
    assert fake.calls == [], fake.calls
    assert await _counts(engine, session_a) == before
    async with engine.connect() as connection:
        after = (
            await connection.execute(
                text("SELECT state FROM onboarding_sessions WHERE id = :id"), {"id": session_a}
            )
        ).scalar_one()
    assert after == state, "a refusal must not move the session"


@pytest.mark.anyio
async def test_two_simultaneous_cancels_produce_one_cancel(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Double-click is a concurrency test that runs in production.

    Reading the state, deciding, then writing leaves a window where both
    requests read `ready` and both decide to cancel. The row lock closes it:
    the second waits, then sees what the first did.
    """
    import asyncio

    from drake_api.onboarding import service as onboarding_service

    _harness, _fake, session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    barrier = asyncio.Barrier(2)

    async def worker() -> Any:
        await barrier.wait()
        return await onboarding_service.cancel(engine, session_id=session_a, expected_version=1)

    results = await asyncio.gather(worker(), worker(), return_exceptions=True)
    succeeded = [item for item in results if not isinstance(item, BaseException)]
    refused = [item for item in results if isinstance(item, BaseException)]
    assert len(succeeded) == 1, results
    assert len(refused) == 1, results
    # The loser is refused by a bounded code, not by a database error.
    assert isinstance(refused[0], onboarding_service.OnboardingError)
    assert refused[0].code in ("version_conflict", "invalid_session_state")

    async with engine.connect() as connection:
        state, version = (
            await connection.execute(
                text("SELECT state, version FROM onboarding_sessions WHERE id = :id"),
                {"id": session_a},
            )
        ).one()
    assert state == "cancelled"
    assert version == 2, "one cancel, one version bump"


@pytest.mark.anyio
async def test_the_repository_cursor_walks_every_row_exactly_once(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A picker that repeats or skips a repository is worse than no picker."""
    harness, _fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-page"])
    await grant(engine, harness, "user-page", OPERATOR_ROLE, "organization", "root")

    async with harness.api_client() as client:
        await harness.login(client, "user-page")
        everything = (await client.get("/v1/onboarding/repositories")).json()["items"]
        assert len(everything) >= 3, everything

        walked: list[str] = []
        cursor: str | None = None
        for _ in range(len(everything) + 2):
            url = "/v1/onboarding/repositories?limit=1"
            if cursor:
                url = f"{url}&cursor={cursor}"
            page = (await client.get(url)).json()
            walked.extend(entry["full_name"] for entry in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    assert cursor is None, "the walk has to terminate"
    assert walked == [entry["full_name"] for entry in everything]
    assert len(walked) == len(set(walked)), "no repository appears twice"


@pytest.mark.anyio
async def test_a_search_wildcard_cannot_widen_the_match(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`%` in a search box is input, not syntax."""
    harness, _fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-search"])
    await grant(engine, harness, "user-search", OPERATOR_ROLE, "organization", "root")

    async with harness.api_client() as client:
        await harness.login(client, "user-search")
        matched = (await client.get("/v1/onboarding/repositories?search=Hermes")).json()
        wildcard = (await client.get("/v1/onboarding/repositories?search=%25")).json()
        underscore = (await client.get("/v1/onboarding/repositories?search=_ermes")).json()

    assert [entry["full_name"] for entry in matched["items"]] == ["Duosis-Developer-Team/Hermes"]
    # Escaped, so they match a literal `%` and a literal `_` — of which
    # there are none — rather than everything and any-single-character.
    assert wildcard["items"] == []
    assert underscore["items"] == []


# ===========================================================================
# startability is a server invariant, not a greyed-out option
# ===========================================================================


async def _reconciled(engine: AsyncEngine, repository_id: uuidlib.UUID) -> None:
    """Put a repository in the state an onboarding may start from."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE github_repositories SET reconciliation_state = 'complete', "
                "onboarding_state = 'ready', archived = false, disabled = false, "
                "access_state = 'accessible', security_gate = NULL WHERE id = :id"
            ),
            {"id": repository_id},
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("security_gate", "'manual_env_review'", "security_gate_open"),
        ("archived", "true", "repository_unavailable"),
        ("disabled", "true", "repository_unavailable"),
        ("access_state", "'removed'", "repository_unavailable"),
        ("access_state", "'suspended'", "repository_unavailable"),
        ("reconciliation_state", "'never'", "repository_not_ready"),
        ("onboarding_state", "'discovered'", "repository_not_ready"),
    ],
)
async def test_a_direct_call_cannot_open_a_session_the_picker_would_refuse(
    engine: AsyncEngine, tmp_path: Path, column: str, value: str, reason: str
) -> None:
    """A greyed-out option is a courtesy. This is the rule.

    The picker computed `startable` from archived / disabled / access_state
    / gate, and `POST /sessions` re-checked only the gate — so a direct call
    opened a session on a repository the list had already reported as
    unavailable. Both now ask the same function.
    """
    harness, fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-start"])
    await grant(engine, harness, "user-start", OPERATOR_ROLE, "organization", "root")

    async with engine.connect() as connection:
        row_id = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM github_repositories "
                            "WHERE full_name = 'Duosis-Developer-Team/Hermes'"
                        )
                    )
                ).scalar_one()
            )
        )
    # Clear the active session so `session_in_progress` is not what refuses.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE onboarding_sessions SET state = 'cancelled' WHERE repository_id = :r"),
            {"r": row_id},
        )
    await _reconciled(engine, row_id)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                f"UPDATE github_repositories SET {column} = {value} WHERE id = :id"  # noqa: S608
            ),
            {"id": row_id},
        )

    before = await _counts(engine, _session_a)
    async with engine.connect() as connection:
        sessions_before = int(
            (
                await connection.execute(text("SELECT count(*) FROM onboarding_sessions"))
            ).scalar_one()
        )
    fake.calls.clear()

    async with harness.api_client() as client:
        await harness.login(client, "user-start")
        me = (await client.get("/v1/me")).json()
        # The picker says no...
        listed = (await client.get("/v1/onboarding/repositories?limit=50")).json()
        entry = next(
            item for item in listed["items"] if item["full_name"] == "Duosis-Developer-Team/Hermes"
        )
        assert entry["startable"] is False
        assert entry["reason_code"] == reason

        # ...and so does the endpoint, for the same reason.
        created = await client.post(
            "/v1/onboarding/sessions",
            json={"repository_id": str(row_id)},
            headers={"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url},
        )

    assert created.status_code == 409, created.text
    assert created.json()["error"]["code"] == reason
    # No provider call, no token, nothing written.
    assert fake.calls == [], fake.calls
    assert await _counts(engine, _session_a) == before
    async with engine.connect() as connection:
        sessions_after = int(
            (
                await connection.execute(text("SELECT count(*) FROM onboarding_sessions"))
            ).scalar_one()
        )
    assert sessions_after == sessions_before


@pytest.mark.anyio
async def test_two_simultaneous_creates_produce_one_session(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Select-then-insert loses this race; the database does not.

    Both callers read "no active session" and both insert. The partial
    unique index stops the second — as an IntegrityError, which reaches the
    caller as a 500 for something that is not an error at all. `ON CONFLICT`
    against that index makes the loser's answer ordinary: the session that
    exists, and `created: false`.
    """
    from drake_api.onboarding import service as onboarding_service

    harness, _fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    async with engine.connect() as connection:
        row_id = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM github_repositories "
                            "WHERE full_name = 'Duosis-Developer-Team/Hermes'"
                        )
                    )
                ).scalar_one()
            )
        )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE onboarding_sessions SET state = 'cancelled' WHERE repository_id = :r"),
            {"r": row_id},
        )
    await _reconciled(engine, row_id)
    actor = await _identity(engine)
    settings = harness.app.state.settings

    barrier = asyncio.Barrier(2)

    async def worker() -> Any:
        await barrier.wait()
        return await onboarding_service.create_session(
            engine,
            settings,
            repository_row_id=row_id,
            actor_identity_id=actor,
            principal=await _principal(harness, engine),
        )

    results = await asyncio.gather(worker(), worker(), return_exceptions=True)
    failures = [item for item in results if isinstance(item, BaseException)]
    assert failures == [], failures

    # One session, one id, and exactly one caller told it created it.
    assert results[0]["session_id"] == results[1]["session_id"]
    assert sorted(item["created"] for item in results) == [False, True]

    async with engine.connect() as connection:
        live = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM onboarding_sessions WHERE repository_id = :r "
                        "AND state NOT IN ('imported', 'cancelled', 'failed')"
                    ),
                    {"r": row_id},
                )
            ).scalar_one()
        )
    assert live == 1
    # No audit assertion here: this calls the SERVICE, and the audit is
    # written by the router. Asserting on it from this test is what made the
    # earlier version worthless — it searched for an action name that does
    # not exist (`onboarding.session.created`) and accepted zero rows, so it
    # passed while proving nothing. The HTTP test below counts it for real.


@pytest.mark.anyio
async def test_the_cursor_separates_two_repositories_with_the_same_name(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """`full_name` is not unique, so a name-only cursor skips a row.

    A repository can be deleted and recreated, and two provider ids can
    carry the same name. With `WHERE full_name > cursor` the second row at a
    page boundary is silently dropped — the operator never sees it and
    nothing reports that anything is missing.
    """
    harness, _fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-dup"])
    await grant(engine, harness, "user-dup", OPERATOR_ROLE, "organization", "root")

    # A second row with the SAME full name and a different provider id.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO github_repositories "
                "(installation_id, scope_id, external_id, full_name, owner_login, name, "
                " default_branch, onboarding_state) "
                "SELECT installation_id, scope_id, 912999, full_name, owner_login, name, "
                " default_branch, onboarding_state FROM github_repositories "
                "WHERE full_name = 'Duosis-Developer-Team/Hermes' LIMIT 1"
            )
        )

    async with harness.api_client() as client:
        await harness.login(client, "user-dup")
        everything = (await client.get("/v1/onboarding/repositories?limit=50")).json()["items"]
        duplicates = [
            item for item in everything if item["full_name"] == "Duosis-Developer-Team/Hermes"
        ]
        assert len(duplicates) == 2, duplicates

        # Walk one row at a time — the boundary the old cursor fell over.
        walked: list[str] = []
        cursor: str | None = None
        for _ in range(len(everything) + 3):
            url = "/v1/onboarding/repositories?limit=1"
            if cursor:
                url = f"{url}&cursor={cursor}"
            page = (await client.get(url)).json()
            walked.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    assert cursor is None, "the walk has to terminate"
    assert walked == [item["id"] for item in everything]
    assert len(walked) == len(set(walked)), "no row twice"
    # Both same-named rows survived the boundary.
    assert all(item["id"] in walked for item in duplicates)


@pytest.mark.anyio
async def test_a_cursor_this_endpoint_did_not_issue_is_refused(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """Bounded 422, not a 500 and not an unfiltered page."""
    harness, _fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-cursor"])
    await grant(engine, harness, "user-cursor", OPERATOR_ROLE, "organization", "root")

    async with harness.api_client() as client:
        await harness.login(client, "user-cursor")
        for bogus in ("not-base64!!", "Zm9v", "%%%"):
            response = await client.get(f"/v1/onboarding/repositories?cursor={bogus}")
            assert response.status_code == 422, (bogus, response.text)


@pytest.mark.anyio
async def test_two_simultaneous_http_creates_produce_one_session_and_one_audit(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The same race through the real endpoint, with the audit counted.

    The service-level test above proves the database arbitrates. This one
    proves the ENDPOINT does the right thing with that answer: both callers
    get the same session, exactly one is told it created it, and exactly one
    success audit exists — under the action name the router actually writes.
    """
    harness, _fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-http"])
    await grant(engine, harness, "user-http", OPERATOR_ROLE, "organization", "root")

    async with engine.connect() as connection:
        row_id = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM github_repositories "
                            "WHERE full_name = 'Duosis-Developer-Team/Hermes'"
                        )
                    )
                ).scalar_one()
            )
        )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE onboarding_sessions SET state = 'cancelled' WHERE repository_id = :r"),
            {"r": row_id},
        )
    await _reconciled(engine, row_id)

    async with harness.api_client() as client:
        await harness.login(client, "user-http")
        me = (await client.get("/v1/me")).json()
        headers = {"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url}

        barrier = asyncio.Barrier(2)

        async def worker() -> Any:
            await barrier.wait()
            return await client.post(
                "/v1/onboarding/sessions",
                json={"repository_id": str(row_id)},
                headers=headers,
            )

        first, second = await asyncio.gather(worker(), worker())

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    bodies = [first.json(), second.json()]
    assert bodies[0]["session_id"] == bodies[1]["session_id"]
    # Exactly one caller created it. Not "at most one" — zero would mean the
    # endpoint invented a session nobody is recorded as having opened.
    assert sorted(body["created"] for body in bodies) == [False, True]

    session_id = bodies[0]["session_id"]
    async with engine.connect() as connection:
        live = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM onboarding_sessions WHERE repository_id = :r "
                        "AND state NOT IN ('imported', 'cancelled', 'failed')"
                    ),
                    {"r": row_id},
                )
            ).scalar_one()
        )
        audits = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE action = 'onboarding.session.create' "
                        "AND result = 'success' AND target_id = :t"
                    ),
                    {"t": session_id},
                )
            ).scalar_one()
        )
    assert live == 1
    assert audits == 1, "exactly one create audit, against the session that was created"


@pytest.mark.anyio
async def test_a_failed_session_does_not_block_a_new_one_in_either_place(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The picker and the endpoint have to agree about what `failed` means.

    The picker treated any session outside `imported`/`cancelled` as
    occupying the repository, so it reported `session_in_progress` and
    offered to open the failed one. The partial unique index — which is what
    actually stops a second session — excludes `failed`, so a direct POST
    created a new draft beside it. The UI sent operators one way and the API
    did the other.
    """
    harness, _fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-failed"])
    await grant(engine, harness, "user-failed", OPERATOR_ROLE, "organization", "root")

    async with engine.connect() as connection:
        row_id = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM github_repositories "
                            "WHERE full_name = 'Duosis-Developer-Team/Hermes'"
                        )
                    )
                ).scalar_one()
            )
        )
    # Exactly one session on this repository, and it failed.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE onboarding_sessions SET state = 'failed' WHERE repository_id = :r"),
            {"r": row_id},
        )
    await _reconciled(engine, row_id)

    async with harness.api_client() as client:
        await harness.login(client, "user-failed")
        me = (await client.get("/v1/me")).json()

        listed = (await client.get("/v1/onboarding/repositories?limit=50")).json()
        entry = next(
            item for item in listed["items"] if item["full_name"] == "Duosis-Developer-Team/Hermes"
        )
        # A failed session does not occupy the repository: the picker offers
        # to start, and does not point at a session nobody can continue.
        assert entry["startable"] is True
        assert entry["reason_code"] is None
        assert entry["active_session_id"] is None

        created = await client.post(
            "/v1/onboarding/sessions",
            json={"repository_id": str(row_id)},
            headers={"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url},
        )

    # And the endpoint agrees — a new session, not a redirect to the failed
    # one and not a refusal.
    assert created.status_code == 201, created.text
    assert created.json()["created"] is True

    async with engine.connect() as connection:
        states = sorted(
            str(row[0])
            for row in (
                await connection.execute(
                    text("SELECT state FROM onboarding_sessions WHERE repository_id = :r"),
                    {"r": row_id},
                )
            ).all()
        )
    assert states == ["draft", "failed"]


@pytest.mark.anyio
async def test_a_repository_that_leaves_the_callers_scope_cannot_be_onboarded(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """The scope decision is made against the row that gets written.

    Eligibility used to be read in one connection and the insert done in
    another. Between them a repository could be gated, archived, disabled,
    lose access — or move to a scope the caller has no rights in — and the
    session was still opened on the strength of the earlier read.

    Both now happen in one transaction, against a locked row, with the scope
    predicate part of the locked read.
    """
    harness, fake, _session_a, _session_b = await _mixed_scope_world(engine, tmp_path)
    await login_all(harness, ["user-moved"])
    await grant(engine, harness, "user-moved", OPERATOR_ROLE, "organization", "root")

    async with engine.connect() as connection:
        row_id = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM github_repositories "
                            "WHERE full_name = 'Duosis-Developer-Team/Hermes'"
                        )
                    )
                ).scalar_one()
            )
        )
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE onboarding_sessions SET state = 'cancelled' WHERE repository_id = :r"),
            {"r": row_id},
        )
    await _reconciled(engine, row_id)

    # Move the repository into a scope this caller has no grant in.
    #
    # A composite foreign key pins `(installation_id, scope_id)`, so the
    # installation moves with it — and the constraint is not deferrable, so
    # the two updates are ordered rather than done together: point the
    # repository at a new installation row in the target scope.
    # A scope OUTSIDE the caller's tree. `_scope` parents under the
    # organisation root, which a root-level grant would still cover — and
    # covering it would be correct, so the test has to move the repository
    # somewhere the caller genuinely has nothing.
    async with engine.begin() as connection:
        other = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO scopes (scope_type, external_ref, display_name, "
                            " parent_id) VALUES ('organization', 'other-tenant', "
                            " 'Other Tenant', NULL) "
                            "ON CONFLICT (scope_type, external_ref) DO UPDATE "
                            " SET display_name = EXCLUDED.display_name RETURNING id"
                        )
                    )
                ).scalar_one()
            )
        )
    async with engine.begin() as connection:
        moved_installation = uuidlib.UUID(
            str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO github_installations (external_id, scope_id, "
                            " account_login, account_type, state) "
                            "VALUES (912555, :scope, 'elsewhere', 'Organization', 'active') "
                            "RETURNING id"
                        ),
                        {"scope": other},
                    )
                ).scalar_one()
            )
        )
        await connection.execute(
            text(
                "UPDATE github_repositories SET scope_id = :scope, installation_id = :inst "
                "WHERE id = :id"
            ),
            {"scope": other, "inst": moved_installation, "id": row_id},
        )

    before = await _counts(engine, _session_a)
    fake.calls.clear()
    async with harness.api_client() as client:
        await harness.login(client, "user-moved")
        me = (await client.get("/v1/me")).json()
        # It is gone from the picker...
        listed = (await client.get("/v1/onboarding/repositories?limit=50")).json()
        assert all(item["full_name"] != "Duosis-Developer-Team/Hermes" for item in listed["items"])
        # ...and the endpoint refuses it, with the same answer an unknown
        # repository gets.
        created = await client.post(
            "/v1/onboarding/sessions",
            json={"repository_id": str(row_id)},
            headers={"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url},
        )
        unknown = await client.post(
            "/v1/onboarding/sessions",
            json={"repository_id": str(uuidlib.uuid4())},
            headers={"X-CSRF-Token": me["csrf_token"], "Origin": harness.client_base_url},
        )

    assert created.status_code == 404, created.text
    assert unknown.status_code == 404
    assert created.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert fake.calls == []
    assert await _counts(engine, _session_a) == before
    async with engine.connect() as connection:
        opened = int(
            (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM onboarding_sessions WHERE repository_id = :r "
                        "AND state = 'draft'"
                    ),
                    {"r": row_id},
                )
            ).scalar_one()
        )
    assert opened == 0
