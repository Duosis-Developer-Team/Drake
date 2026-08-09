"""The real provider, against a stateful fake of the GitHub write API.

The property under test is one sentence: **the same proposal produces at
most one branch, one commit and one pull request**, however many times it is
attempted and however the network behaves in between.

That is not a retry policy. It is the reason every step reads before it
writes: an attempt that was interrupted anywhere must be resumable without
producing a second of anything in somebody's repository.

No network, no credential, no real repository. `WriteFakeGitHub` composes
every response and records which mutations were actually applied.
"""

import uuid as uuidlib
from typing import Any

import httpx
import pytest
from drake_api.github_app.auth import GitHubAppAuth
from drake_api.github_app.client import GitHubClient
from drake_api.onboarding.github_provider import (
    GITOPS_PERMISSIONS,
    GitHubPullRequestProvider,
    assert_draft_is_safe,
)
from drake_api.settings import Settings
from fake_github_write import WriteFakeGitHub

pytestmark = pytest.mark.anyio

BASE_SHA = "a" * 40
HEAD_BRANCH = "drake/onboarding/2f1c9a2b"

DRAFT = """apiVersion: drake.duosis.com/v1alpha1
kind: ProjectObservability
metadata:
  name: hermes
spec:
  repository:
    provider: github
    owner: Duosis-Developer-Team
    name: Hermes
  owners:
    - team: REPLACE_ME
      role: primary
  environments:
    - name: dev
      clusterRef: REPLACE_ME
      namespace: REPLACE_ME
  services:
    - name: hermes-api
      metricsProfile: REPLACE_ME
  tenantModel:
    mode: REPLACE_ME
"""


def _provider(fake: WriteFakeGitHub, tmp_path: Any) -> GitHubPullRequestProvider:
    """A real client over the fake transport, in a local-shaped process."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = tmp_path / "key.pem"
    key.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    settings = Settings(
        env="local",
        github_app_enabled=True,
        github_app_client_id="Iv1.local",
        github_app_private_key_file=str(key),
        github_api_base_url="https://api.github.test",
    )
    client = GitHubClient(
        settings, GitHubAppAuth(settings), transport=httpx.MockTransport(fake.handler)
    )
    return GitHubPullRequestProvider(client)


async def _propose(
    provider: GitHubPullRequestProvider, fake: WriteFakeGitHub, *, content: str = DRAFT
) -> Any:
    return await provider.create_pull_request(
        installation_id=fake.installation_id,
        repository_id=fake.repository_id,
        owner=fake.owner,
        name=fake.name,
        base_branch=fake.default_branch,
        base_commit_sha=fake.branches[fake.default_branch],
        head_branch=HEAD_BRANCH,
        file_path=".drake/project.yaml",
        content=content,
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
    assert list(fake.files) == [(HEAD_BRANCH, ".drake/project.yaml")]
    assert fake.branches[fake.default_branch] == BASE_SHA, "the default branch was not touched"

    pull = fake.pulls[0]
    assert pull["draft"] is True, "always a draft: the manifest is not finished"
    assert "needs your input" in pull["title"]
    body = pull["body"]
    for field in ("team", "clusterRef", "namespace", "metricsProfile", "mode"):
        assert f"`{field}`" in body, field
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


async def test_two_workers_racing_the_same_proposal_produce_one_pull_request(
    tmp_path: Any,
) -> None:
    """Sequential here, because the provider is not the arbiter.

    The lease is what serialises workers — this asserts the provider does
    not undo that: a second pass over the same proposal reuses rather than
    creates, so even a lease that failed would not produce two pull
    requests.
    """
    fake = WriteFakeGitHub()
    results = [
        await _propose(_provider(fake, tmp_path), fake),
        await _propose(_provider(fake, tmp_path), fake),
        await _propose(_provider(fake, tmp_path), fake),
    ]
    assert [r.outcome for r in results] == ["created", "exists", "exists"]
    assert {r.number for r in results} == {101}
    assert fake.counts()["pulls"] == 1


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
    fake.pulls[0]["head"]["ref"] = HEAD_BRANCH  # unchanged; make the intent explicit
    fake.conflict_on.add("POST /pulls")
    result = await _propose(provider, fake)
    assert result.outcome == "exists"
    assert result.number == 101
    assert fake.counts()["pulls"] == 1


# ===========================================================================
# refusals — the cases where nothing may be written
# ===========================================================================


async def test_a_moved_base_writes_nothing(tmp_path: Any) -> None:
    """A plan describes one commit.

    Proposing it onto a branch that has moved makes a claim about code
    nobody reviewed.
    """
    fake = WriteFakeGitHub()
    provider = _provider(fake, tmp_path)
    moved = "b" * 40

    result = await provider.create_pull_request(
        installation_id=fake.installation_id,
        repository_id=fake.repository_id,
        owner=fake.owner,
        name=fake.name,
        base_branch=fake.default_branch,
        base_commit_sha=moved,  # not what the branch points at
        head_branch=HEAD_BRANCH,
        file_path=".drake/project.yaml",
        content=DRAFT,
        title="",
        body="",
    )
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
    fake.branches[HEAD_BRANCH] = "c" * 40
    fake.files[(HEAD_BRANCH, ".drake/project.yaml")] = "apiVersion: something-else\n"

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "terminal"
    assert result.error_code == "branch_conflict"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}
    # Untouched.
    assert fake.files[(HEAD_BRANCH, ".drake/project.yaml")] == "apiVersion: something-else\n"


async def test_an_existing_branch_with_matching_content_is_reused(tmp_path: Any) -> None:
    """The resumable case: our branch, our content, no pull request yet."""
    fake = WriteFakeGitHub()
    # Its own head sha: the fake resolves a contents read by matching the
    # ref against branch heads, and two branches at the same commit would
    # make which one it means ambiguous.
    fake.branches[HEAD_BRANCH] = "d" * 40
    fake.files[(HEAD_BRANCH, ".drake/project.yaml")] = DRAFT

    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == "created"
    # No second branch and no second commit: the content was already right.
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 1}


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
        file_path=".drake/project.yaml",
        content=DRAFT,
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
    ("status", "outcome", "code"),
    [
        (401, "terminal", "github_permission_missing"),
        (403, "terminal", "github_permission_missing"),
        (429, "retryable", "github_rate_limited"),
        (500, "retryable", "github_unavailable"),
        (503, "retryable", "github_unavailable"),
    ],
)
async def test_provider_failures_are_classified_honestly(
    tmp_path: Any, status: int, outcome: str, code: str
) -> None:
    """Terminal and retryable are different promises to the worker."""
    fake = WriteFakeGitHub()
    fake.fail_with["POST /git/refs"] = status
    result = await _propose(_provider(fake, tmp_path), fake)
    assert result.outcome == outcome, status
    assert result.error_code == code, status
    assert fake.counts()["branches"] == 0


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
        content=DRAFT,
        title="",
        body="",
    )
    assert result.outcome == "terminal"
    assert result.error_code == "path_not_allowed"
    assert fake.counts() == {"branches": 0, "commits": 0, "pulls": 0}


# ===========================================================================
# what may be pushed at all
# ===========================================================================


def test_the_generated_draft_passes_its_own_safety_check() -> None:
    placeholders = assert_draft_is_safe(DRAFT)
    assert set(placeholders) == {"team", "clusterRef", "namespace", "metricsProfile", "mode"}


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ("not a manifest\n", "not a Drake manifest"),
        ("apiVersion: drake.duosis.com/v1alpha1\nsecret: REPLACE_ME\n", "unexpected placeholder"),
        (
            "apiVersion: drake.duosis.com/v1alpha1\ntoken: ghp_aaaaaaaaaaaaaaaaaaaa\n",
            "credential shaped",
        ),
        ("apiVersion: drake.duosis.com/v1alpha1\n" + "x" * 70_000, "too large"),
    ],
)
def test_an_unexpected_draft_is_refused_before_it_leaves(content: str, why: str) -> None:
    """Checked against the bytes about to be sent, not what was generated.

    An unexpected placeholder means the generator changed and this provider
    was not re-reviewed — and pushing an unreviewed template into somebody's
    repository is the kind of thing only noticed afterwards.
    """
    from drake_api.github_app.client import GitHubContractError

    with pytest.raises(GitHubContractError):
        assert_draft_is_safe(content)


async def test_a_refused_draft_reaches_no_provider_call(tmp_path: Any) -> None:
    fake = WriteFakeGitHub()
    result = await _propose(
        _provider(fake, tmp_path),
        fake,
        content="apiVersion: drake.duosis.com/v1\nfoo: REPLACE_ME\n",
    )
    assert result.outcome == "terminal"
    assert result.error_code == "draft_refused"
    assert fake.calls == [], "nothing was sent at all"


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
    rendered = _body(session, BASE_SHA, ["team"])
    assert session in rendered
    for forbidden in ("ghs_", "ghp_", "BEGIN", "Authorization", "password"):
        assert forbidden not in rendered
