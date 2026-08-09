"""Onboarding reads: scope-filtered, bounded, repository-content-free.

Visibility follows the repository's scope, resolved through the same
`ScopeResolver` grants every other read path uses. Filtering happens in SQL
before any count, page or filter option, so a caller who cannot see a
repository cannot learn a session exists from a total.

Nothing here returns a repository file body, a manifest body, a provider
response, an installation token, a webhook signature, or a URL. Findings
carry a path, a kind and a confidence — enough to explain a proposal, and
not enough to become a copy of the repository.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from drake_api.catalog.authz import escape_like, visible_scope_ids
from drake_api.onboarding.model import REASON_TEXT, Action, SessionState
from drake_api.rbac.service import Principal

# Looking at an onboarding session is its own right: a session names
# repositories, proposed services and existing catalog rows.
VIEW_PERMISSION = "onboarding.view"
MANAGE_PERMISSION = "onboarding.manage"
APPLY_PERMISSION = "onboarding.apply"
GITOPS_PERMISSION = "onboarding.gitops"
# Integration health and the installation projection stay with the operator
# who runs the integration, which is a different job from onboarding one
# project.
INTEGRATION_PERMISSION = "integration.manage"

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 25
MAX_FINDINGS = 200

SESSION_STATES = frozenset(str(state) for state in SessionState)
PLAN_ACTIONS = frozenset(str(action) for action in Action)
GITOPS_STATES = frozenset({"pending", "active", "failed", "stale", "cancelled"})


class FilterError(ValueError):
    """A filter value outside the allowlist (422)."""


def _sentinel(ids: set[uuid.UUID]) -> list[uuid.UUID]:
    """Never let an empty visibility set become an unfiltered query."""
    return list(ids) or [uuid.UUID(int=0)]


async def scopes_for(
    connection: AsyncConnection, principal: Principal, permission: str
) -> list[uuid.UUID]:
    return _sentinel(await visible_scope_ids(connection, principal, permission))


async def can(connection: AsyncConnection, principal: Principal, permission: str) -> bool:
    """Does the principal hold *permission* ANYWHERE?

    This answers a page-level question — "is this feature worth showing at
    all" — and nothing more. It must never decide whether a particular
    session may be mutated: holding `onboarding.apply` on one project is not
    permission to apply another project's plan. Use `permitted_in` for that.
    """
    return bool(await visible_scope_ids(connection, principal, permission))


async def permitted_in(
    connection: AsyncConnection, principal: Principal, permission: str, scope_id: uuid.UUID
) -> bool:
    """Does the principal hold *permission* on THIS scope?

    `visible_scope_ids` already walks the scope tree downward, so a grant on
    an organisation still covers a project inside it — the direction real
    delegation runs. It does not walk upward or sideways, so a grant on one
    project is not a grant on its parent or on a sibling.
    """
    return scope_id in await visible_scope_ids(connection, principal, permission)


async def authorize_session(
    connection: AsyncConnection,
    principal: Principal,
    session_id: uuid.UUID,
    permission: str,
) -> uuid.UUID | None:
    """The session's scope, if the principal may use *permission* on IT.

    One function for every mutation, because the bug this replaces came from
    each endpoint asking its own slightly different question. Two checks,
    both against the session's own `scope_id`: the principal can SEE the
    session, and holds the acting permission THERE.

    Returns `None` for a session that does not exist, one the principal
    cannot see, and one it can see but may not act on — the caller turns all
    three into the same 404. Distinguishing them would let somebody with
    read access anywhere enumerate what exists everywhere else.
    """
    row = (
        await connection.execute(
            text("SELECT scope_id FROM onboarding_sessions WHERE id = :id"),
            {"id": session_id},
        )
    ).first()
    if row is None:
        return None
    scope_id = uuid.UUID(str(row[0]))
    if not await permitted_in(connection, principal, VIEW_PERMISSION, scope_id):
        return None
    if not await permitted_in(connection, principal, permission, scope_id):
        return None
    return scope_id


_SESSION_COLUMNS = """
    s.id, s.state, s.reason_code, s.analyzed_commit_sha, s.analyzed_at,
    s.approved_at, s.approved_plan_version, s.imported_project_id, s.imported_at,
    s.version, s.created_at,
    r.id, r.owner_login, r.name, r.full_name, r.default_branch, r.security_gate,
    p.plan_version, p.state, p.blocking_items, p.total_items, p.plan_digest, p.commit_sha,
    pr.project_key,
    s.scope_id
"""

_SESSION_JOINS = """
    FROM onboarding_sessions s
    JOIN github_repositories r ON r.id = s.repository_id
    LEFT JOIN LATERAL (
        SELECT * FROM onboarding_plans x
        WHERE x.session_id = s.id
        ORDER BY x.plan_version DESC
        LIMIT 1
    ) p ON true
    LEFT JOIN projects pr ON pr.id = s.imported_project_id
"""


def _session_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "state": row[1],
        "reason_code": row[2],
        "reason": REASON_TEXT.get(row[2] or "", ""),
        # Null until something has actually been analysed. A session that
        # has looked at nothing must not claim a commit.
        "analyzed_commit_sha": row[3],
        "analyzed_at": row[4].isoformat() if row[4] else None,
        "approved_at": row[5].isoformat() if row[5] else None,
        "approved_plan_version": row[6],
        "imported_project_id": str(row[7]) if row[7] else None,
        "imported_at": row[8].isoformat() if row[8] else None,
        "version": row[9],
        "created_at": row[10].isoformat(),
        "repository": {
            "id": str(row[11]),
            "owner": row[12],
            "name": row[13],
            "full_name": row[14],
            "default_branch": row[15],
            # A gate is a fact an operator needs. The REASON lives in the
            # server-side catalogue, not in a field a payload could set.
            "security_gate": row[16],
        },
        "plan": (
            None
            if row[17] is None
            else {
                "plan_version": row[17],
                "state": row[18],
                "blocking_items": row[19],
                "total_items": row[20],
                "plan_digest": str(row[21])[:16],
                "commit_sha": row[22],
            }
        ),
        "imported_project_key": row[23],
    }


async def list_sessions(
    connection: AsyncConnection,
    principal: Principal,
    *,
    state: str | None = None,
    repository_id: uuid.UUID | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    if state is not None and state not in SESSION_STATES:
        raise FilterError("unsupported session state")

    scopes = await scopes_for(connection, principal, VIEW_PERMISSION)
    conditions = ["s.scope_id = ANY(:scopes)"]
    params: dict[str, Any] = {"scopes": scopes, "limit": limit, "offset": offset}
    if state is not None:
        conditions.append("s.state = :state")
        params["state"] = state
    if repository_id is not None:
        conditions.append("s.repository_id = :repository")
        params["repository"] = repository_id

    where = " AND ".join(conditions)
    # Counted over the SAME predicate as the page, so a total can never
    # hint at a session the list will not show.
    total = (
        await connection.execute(
            text(f"SELECT count(*) {_SESSION_JOINS} WHERE {where}"),
            params,
        )
    ).scalar_one()
    rows = (
        await connection.execute(
            text(
                f"""
                SELECT {_SESSION_COLUMNS} {_SESSION_JOINS}
                WHERE {where}
                ORDER BY s.created_at DESC, s.id
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).all()
    return {
        "items": [_session_row(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# repository candidates — what an operator may start a session on
# ---------------------------------------------------------------------------

#: Why a repository cannot be started, in the order the reasons apply.
#: Bounded codes: an operator sees a sentence Drake wrote, never a provider's.
STARTABLE_BLOCKERS = (
    "security_gate_open",
    "repository_unavailable",
    "session_in_progress",
)


async def repository_candidates(
    connection: AsyncConnection,
    principal: Principal,
    *,
    search: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Repositories this principal may actually START an onboarding on.

    Scoped by `onboarding.manage`, not by read access, because that is the
    permission the button behind this list needs. Listing what somebody
    cannot act on would be a working repository-name enumerator for anyone
    with read access anywhere.

    The scope filter is part of the SQL rather than applied afterwards, so
    pagination and any count are computed over the rows the caller may see.
    Filtering a page after selecting it leaks totals through page sizes.
    """
    scopes = await scopes_for(connection, principal, MANAGE_PERMISSION)
    params: dict[str, Any] = {"scopes": scopes, "limit": limit + 1}
    where = ["r.scope_id = ANY(:scopes)"]
    if search:
        # The escaper is what stops `%` in a search box from widening the
        # match past the caller's own scopes.
        params["search"] = f"%{escape_like(search)}%"
        where.append("r.full_name ILIKE :search ESCAPE '\\'")
    if cursor:
        # Keyset, on the same total order the query sorts by, so a page
        # boundary cannot repeat or skip a row when the set changes.
        params["cursor"] = cursor
        # Same collation as the ORDER BY below, and deliberately `C`: a
        # locale collation orders names differently on different databases,
        # so a cursor issued by one instance could skip or repeat rows on
        # another. Byte order is the same everywhere.
        where.append('r.full_name COLLATE "C" > :cursor COLLATE "C"')

    rows = (
        await connection.execute(
            text(
                f"""
                SELECT r.id, r.full_name, r.default_branch, r.onboarding_state,
                       r.security_gate, r.archived, r.disabled, r.access_state,
                       (SELECT s.id FROM onboarding_sessions s
                         WHERE s.repository_id = r.id
                           AND s.state NOT IN ('imported', 'cancelled')
                         ORDER BY s.created_at DESC LIMIT 1) AS active_session
                FROM github_repositories r
                WHERE {" AND ".join(where)}
                ORDER BY r.full_name COLLATE "C"
                LIMIT :limit
                """  # noqa: S608 - every fragment is a module literal
            ),
            params,
        )
    ).all()

    items: list[dict[str, Any]] = []
    for row in rows[:limit]:
        gate = row[4]
        unavailable = bool(row[5]) or bool(row[6]) or str(row[7] or "accessible") != "accessible"
        active_session = str(row[8]) if row[8] else None
        reason_code: str | None = None
        if gate:
            reason_code = "security_gate_open"
        elif unavailable:
            reason_code = "repository_unavailable"
        elif active_session:
            # Not a refusal so much as a redirect: the operator wants the
            # session that already exists, not a second one beside it.
            reason_code = "session_in_progress"
        items.append(
            {
                "id": str(row[0]),
                "full_name": str(row[1]),
                "default_branch": str(row[2] or ""),
                "onboarding_state": str(row[3]),
                # Derived, not the raw provider fields: a client decides on
                # this word, and `archived`/`disabled`/`access_state` are
                # three ways of saying one thing to an operator.
                "access_state": "accessible" if not unavailable else "unavailable",
                "security_gate": gate,
                "active_session_id": active_session,
                "startable": reason_code is None,
                "reason_code": reason_code,
            }
        )
    next_cursor = items[-1]["full_name"] if len(rows) > limit and items else None
    return {"items": items, "next_cursor": next_cursor}


async def get_session(
    connection: AsyncConnection, principal: Principal, session_id: uuid.UUID
) -> dict[str, Any] | None:
    scopes = await scopes_for(connection, principal, VIEW_PERMISSION)
    row = (
        await connection.execute(
            text(
                f"""
                SELECT {_SESSION_COLUMNS} {_SESSION_JOINS}
                WHERE s.id = :id AND s.scope_id = ANY(:scopes)
                """
            ),
            {"id": session_id, "scopes": scopes},
        )
    ).first()
    if row is None:
        return None
    session = _session_row(row)
    # UI gating only. Every mutation re-checks; hiding a button has never
    # been an authorization boundary.
    #
    # Scoped to THIS session. They used to answer "does this user hold the
    # permission anywhere", so a user with `onboarding.apply` on one project
    # saw an enabled Apply button on a session belonging to another — and a
    # button that is enabled for someone who may not press it is a promise
    # the API has to break.
    scope_id = uuid.UUID(str(row[24]))
    session["can_manage"] = await permitted_in(connection, principal, MANAGE_PERMISSION, scope_id)
    session["can_apply"] = await permitted_in(connection, principal, APPLY_PERMISSION, scope_id)
    session["can_gitops"] = await permitted_in(connection, principal, GITOPS_PERMISSION, scope_id)
    return session


async def session_findings(
    connection: AsyncConnection, principal: Principal, session_id: uuid.UUID
) -> dict[str, Any] | None:
    """Safe findings from the newest analysis of this session."""
    if await get_session(connection, principal, session_id) is None:
        return None
    # Resolved through the session's newest PLAN, not by `session_id`.
    #
    # An analysis row is unique on `(repository, commit, analyzer_version)`
    # — one analysis per commit, which is right — so a second session on the
    # same repository at the same commit REUSES the first session's row and
    # never gets one of its own. Filtering by `session_id` therefore made a
    # session that had been analysed report "not analysed yet" beside a plan
    # built from that very analysis. The plan knows which analysis it came
    # from; that is the honest link.
    #
    # The fallback covers a session analysed but not yet planned.
    analysis = (
        await connection.execute(
            text(
                "SELECT a.id, a.commit_sha, a.analyzer_version, a.status, a.truncated, "
                "a.manifest_found, a.files_read, a.bytes_read, a.provider_calls, "
                "a.error_code, a.analyzed_at "
                "FROM onboarding_analyses a "
                "WHERE a.id = (SELECT p.analysis_id FROM onboarding_plans p "
                "              WHERE p.session_id = :id "
                "              ORDER BY p.plan_version DESC LIMIT 1) "
                "   OR a.session_id = :id "
                "ORDER BY a.analyzed_at DESC LIMIT 1"
            ),
            {"id": session_id},
        )
    ).first()
    if analysis is None:
        return {"analysis": None, "findings": []}

    rows = (
        await connection.execute(
            text(
                "SELECT finding_type, safe_path, confidence, evidence_kind, "
                "proposed_target, review_reason FROM onboarding_findings "
                "WHERE analysis_id = :id ORDER BY finding_type, safe_path LIMIT :limit"
            ),
            {"id": analysis[0], "limit": MAX_FINDINGS},
        )
    ).all()
    return {
        "analysis": {
            "id": str(analysis[0]),
            "commit_sha": analysis[1],
            "analyzer_version": analysis[2],
            "status": analysis[3],
            # A budget stop is reported, never smoothed over. An incomplete
            # analysis is not a complete one with fewer results.
            "truncated": analysis[4],
            "manifest_found": analysis[5],
            "files_read": analysis[6],
            "bytes_read": analysis[7],
            "provider_calls": analysis[8],
            "error_code": analysis[9],
            "analyzed_at": analysis[10].isoformat(),
        },
        "findings": [
            {
                "finding_type": row[0],
                # The path only. Never the content at it.
                "safe_path": row[1],
                "confidence": row[2],
                "evidence_kind": row[3],
                "proposed_target": row[4],
                "review_reason": row[5],
            }
            for row in rows
        ],
    }


async def session_plan(
    connection: AsyncConnection,
    principal: Principal,
    session_id: uuid.UUID,
    *,
    plan_version: int | None = None,
) -> dict[str, Any] | None:
    if await get_session(connection, principal, session_id) is None:
        return None
    clause = "AND plan_version = :version" if plan_version is not None else ""
    params: dict[str, Any] = {"id": session_id}
    if plan_version is not None:
        params["version"] = plan_version
    plan = (
        await connection.execute(
            text(
                f"""
                SELECT id, plan_version, state, commit_sha, manifest_digest,
                       analyzer_version, plan_digest, blocking_items, total_items, created_at
                FROM onboarding_plans WHERE session_id = :id {clause}
                ORDER BY plan_version DESC LIMIT 1
                """  # noqa: S608 - the only variable fragment is fixed text
            ),
            params,
        )
    ).first()
    if plan is None:
        return {"plan": None, "items": []}

    rows = (
        await connection.execute(
            text(
                "SELECT entity_kind, action, item_key, proposed_name, existing_entity_id, "
                "existing_name, reason_code, detail FROM onboarding_plan_items "
                "WHERE plan_id = :id ORDER BY entity_kind, item_key"
            ),
            {"id": plan[0]},
        )
    ).all()
    return {
        "plan": {
            "id": str(plan[0]),
            "plan_version": plan[1],
            "state": plan[2],
            # The exact commit this plan describes. A review of a commit is
            # not a review of its successor, so the UI shows it.
            "commit_sha": plan[3],
            "manifest_digest": str(plan[4])[:16] if plan[4] else None,
            "analyzer_version": plan[5],
            "plan_digest": str(plan[6])[:16],
            "blocking_items": plan[7],
            "total_items": plan[8],
            "created_at": plan[9].isoformat(),
            "applicable": plan[2] == "ready" and int(plan[7]) == 0,
        },
        "items": [
            {
                "entity_kind": row[0],
                "action": row[1],
                "item_key": row[2],
                "proposed_name": row[3],
                "existing_entity_id": str(row[4]) if row[4] else None,
                "existing_name": row[5],
                "reason_code": row[6],
                "reason": REASON_TEXT.get(row[6] or "", ""),
                "detail": dict(row[7] or {}),
                "blocking": row[1] in ("conflict", "unmapped", "unsupported"),
            }
            for row in rows
        ],
    }


async def session_gitops(
    connection: AsyncConnection, principal: Principal, session_id: uuid.UUID
) -> list[dict[str, Any]]:
    if await get_session(connection, principal, session_id) is None:
        return []
    rows = (
        await connection.execute(
            text(
                "SELECT id, state, branch_name, file_path, base_commit_sha, "
                "provider_pr_number, error_code, created_at, version "
                "FROM gitops_requests WHERE session_id = :id ORDER BY created_at DESC LIMIT 20"
            ),
            {"id": session_id},
        )
    ).all()
    return [
        {
            "id": str(row[0]),
            # `pending` is not `active`: a pull request Drake has not
            # created yet is not open, and the label says so.
            "state": row[1],
            "branch_name": row[2],
            "file_path": row[3],
            "base_commit_sha": row[4],
            "provider_pr_number": row[5],
            "error_code": row[6],
            "created_at": row[7].isoformat(),
            "version": row[8],
        }
        for row in rows
    ]


async def integration_evidence(connection: AsyncConnection, principal: Principal) -> dict[str, Any]:
    """Onboarding-side Integration Health.

    Counts, states and timestamps only. There is no branch here that can
    return an installation token, a webhook signature, a provider message,
    or a repository path.
    """
    scopes = await scopes_for(connection, principal, VIEW_PERMISSION)
    row = (
        await connection.execute(
            text(
                """
                SELECT
                    count(*) AS sessions,
                    count(*) FILTER (WHERE s.state = 'needs_review') AS needs_review,
                    count(*) FILTER (WHERE s.state = 'ready') AS ready,
                    count(*) FILTER (WHERE s.state = 'imported') AS imported,
                    count(*) FILTER (WHERE s.state = 'stale') AS stale,
                    count(*) FILTER (WHERE s.state = 'provider_unavailable')
                        AS provider_unavailable
                FROM onboarding_sessions s
                WHERE s.scope_id = ANY(:scopes)
                """
            ),
            {"scopes": scopes},
        )
    ).first()
    assert row is not None
    analyses = (
        await connection.execute(
            text(
                """
                SELECT
                    count(*) AS total,
                    count(*) FILTER (WHERE a.truncated) AS truncated,
                    count(*) FILTER (WHERE a.status = 'failed') AS failed,
                    max(a.analyzed_at) AS last_analyzed_at
                FROM onboarding_analyses a
                JOIN onboarding_sessions s ON s.id = a.session_id
                WHERE s.scope_id = ANY(:scopes)
                """
            ),
            {"scopes": scopes},
        )
    ).first()
    assert analyses is not None
    gitops = (
        await connection.execute(
            text(
                """
                SELECT
                    count(*) FILTER (WHERE g.state = 'pending') AS pending,
                    count(*) FILTER (WHERE g.state = 'active') AS active,
                    count(*) FILTER (WHERE g.state = 'failed') AS failed
                FROM gitops_requests g
                JOIN onboarding_sessions s ON s.id = g.session_id
                WHERE s.scope_id = ANY(:scopes)
                """
            ),
            {"scopes": scopes},
        )
    ).first()
    assert gitops is not None
    return {
        "sessions": int(row[0]),
        "needs_review": int(row[1]),
        "ready": int(row[2]),
        "imported": int(row[3]),
        "stale": int(row[4]),
        "provider_unavailable": int(row[5]),
        "analyses": int(analyses[0]),
        # A truncated analysis describes part of a repository. Surfaced so
        # nobody reads a partial picture as a complete one.
        "analyses_truncated": int(analyses[1]),
        "analyses_failed": int(analyses[2]),
        "last_analyzed_at": analyses[3].isoformat() if analyses[3] else None,
        "gitops_pending": int(gitops[0]),
        "gitops_active": int(gitops[1]),
        "gitops_failed": int(gitops[2]),
    }


def filter_options() -> dict[str, Any]:
    """The accepted vocabulary — static, so it enumerates nothing."""
    return {
        "session_states": sorted(SESSION_STATES),
        "plan_actions": sorted(PLAN_ACTIONS),
        "gitops_states": sorted(GITOPS_STATES),
        "blocking_actions": sorted({"conflict", "unmapped", "unsupported"}),
    }
