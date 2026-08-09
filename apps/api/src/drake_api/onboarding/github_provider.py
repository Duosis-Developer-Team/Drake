"""The real GitHub pull-request provider.

One job, stated as narrowly as it can be:

    the same session, at the same base commit, with the same content
    → at most one branch
    → at most one pull request

Everything about the target is server-composed. A caller supplies no
repository, no branch, no path, no commit message and no content — those
come from Drake's own projection and its own deterministic draft, because a
caller who could choose them could write anywhere the installation reaches.

**Nothing here merges, force-pushes, deletes a branch, or writes to a
default branch.** There is no code path that could: the client exposes ref
CREATE (which cannot move an existing ref), a single-file write on a
Drake-prefixed branch, and pull-request create. That is the whole surface.

**A write whose outcome is unknown is never retried.** A POST that timed out
may have been applied, and re-sending it is how one intent becomes two pull
requests. Ambiguity is resolved by READING the world back — which is also
what makes an interrupted attempt safe to resume: every step asks "is this
already true?" before doing anything.

The pull request is always a DRAFT. The manifest Drake generates leaves
operator decisions as explicit `REPLACE_ME` placeholders, and opening a
review-ready pull request would be a claim that it is finished.
"""

import base64
import logging
from typing import Any

from drake_api.github_app.auth import missing_permissions
from drake_api.github_app.client import (
    GitHubAmbiguousWriteError,
    GitHubClient,
    GitHubContractError,
    GitHubError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)
from drake_api.onboarding.gitops import ALLOWED_PATH, PullRequestResult

logger = logging.getLogger("drake_api.onboarding.github_provider")

#: The exact minimum a proposal needs. Anything beyond this is refused by
#: not being asked for: a token narrowed to these two cannot merge, change
#: a workflow, touch a secret, or alter branch protection even if the
#: installation happens to grant more.
GITOPS_PERMISSIONS: dict[str, str] = {
    "metadata": "read",
    "contents": "write",
    "pull_requests": "write",
}

#: The placeholders the generated draft is allowed to contain.
#:
#: An allowlist rather than a count: an unexpected placeholder means the
#: generator changed and this provider has not been re-reviewed, and pushing
#: an unreviewed template into somebody's repository is exactly the kind of
#: thing that only gets noticed afterwards.
_EXPECTED_PLACEHOLDERS = frozenset({"team", "clusterRef", "namespace", "metricsProfile", "mode"})

_PLACEHOLDER = "REPLACE_ME"

#: A generated manifest is a few kilobytes. This is not a tuning knob; it is
#: the point past which something has gone wrong.
_MAX_CONTENT_BYTES = 64 * 1024

_TITLE = "Add Drake project manifest (draft — needs your input)"


def _body(session_id: str, base_commit_sha: str, placeholders: list[str]) -> str:
    """What a reviewer needs to know, and nothing they should not.

    No token, no digest of anything secret, no provider message. It names
    the fields a human must fill in, because a draft that looks finished is
    a draft somebody merges without reading.
    """
    fields = "\n".join(f"- `{name}`" for name in sorted(placeholders))
    return (
        "Drake generated this manifest from what it could observe in this "
        "repository at commit "
        f"`{base_commit_sha[:12]}`.\n\n"
        "**It is not complete, and Drake will not guess the rest.** Every "
        f"`{_PLACEHOLDER}` below is a decision a person has to make:\n\n"
        f"{fields}\n\n"
        "Fill them in, then merge this pull request.\n\n"
        "---\n\n"
        "**Merging this does not import anything into Drake.** It puts the "
        "manifest in the repository, which is where Drake reads intent from. "
        "Afterwards, run the analysis again in Drake and review the plan it "
        "produces — the import happens there, and only when somebody "
        "approves it.\n\n"
        f"Onboarding session: `{session_id}`"
    )


def _placeholders_in(content: str) -> list[str]:
    """Which operator fields the draft left blank."""
    found: list[str] = []
    for line in content.splitlines():
        if _PLACEHOLDER not in line:
            continue
        stripped = line.lstrip("- ").strip()
        if stripped.startswith("#"):
            # A comment mentioning the placeholder, not a field.
            continue
        key = stripped.split(":", 1)[0].strip()
        if key:
            found.append(key)
    return found


def assert_draft_is_safe(content: str) -> list[str]:
    """Refuse to push anything but the manifest Drake meant to generate.

    Checked immediately before the write, against the bytes that are about
    to leave — not against what the generator produced some time earlier.
    """
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_CONTENT_BYTES:
        raise GitHubContractError("manifest draft exceeds the write size budget")
    # A LINE, not the first line: the generator leads with a comment block
    # explaining what a person has to fill in, which is the right thing for
    # a file somebody is about to edit.
    if not any(line.startswith("apiVersion: drake.duosis.com") for line in content.splitlines()):
        raise GitHubContractError("manifest draft is not a Drake project manifest")

    placeholders = _placeholders_in(content)
    unexpected = sorted(set(placeholders) - _EXPECTED_PLACEHOLDERS)
    if unexpected:
        raise GitHubContractError(
            f"manifest draft carries unexpected placeholder fields: {unexpected}"
        )
    # Credential shapes, in the thing about to be committed to somebody
    # else's repository. The generator cannot produce one, which is why a
    # failure here means something upstream changed.
    lowered = content.lower()
    for needle in ("ghp_", "ghs_", "-----begin", "authorization:", "password:"):
        if needle in lowered:
            raise GitHubContractError("manifest draft carries credential-shaped content")
    return placeholders


class GitHubPullRequestProvider:
    """`PullRequestProvider`, backed by the real GitHub API.

    Constructed only where a real GitHub App is configured. Production
    startup refuses the GitOps flags unless one is, and never constructs the
    recording double.
    """

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

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
    ) -> PullRequestResult:
        """Create or reuse. Never create twice.

        `title` and `body` from the caller are ignored: this composes its
        own, because the text of a pull request opened in somebody's
        repository is part of what Drake is saying, not a parameter.
        """
        if file_path != ALLOWED_PATH:
            return PullRequestResult("terminal", None, "path_not_allowed")

        try:
            placeholders = assert_draft_is_safe(content)
        except GitHubContractError:
            logger.warning("gitops: refusing to push a draft that failed its safety check")
            return PullRequestResult("terminal", None, "draft_refused")

        try:
            return await self._reconcile(
                installation_id=installation_id,
                repository_id=repository_id,
                owner=owner,
                name=name,
                base_branch=base_branch,
                base_commit_sha=base_commit_sha,
                head_branch=head_branch,
                content=content,
                placeholders=placeholders,
            )
        except GitHubRateLimitedError:
            return PullRequestResult("retryable", None, "github_rate_limited")
        except GitHubUnavailableError:
            return PullRequestResult("retryable", None, "github_unavailable")
        except GitHubAmbiguousWriteError:
            # The next attempt re-reads and reconciles; that is the whole
            # design. Retryable, and safe to be.
            return PullRequestResult("retryable", None, "github_write_ambiguous")
        except GitHubForbiddenError:
            return PullRequestResult("terminal", None, "github_permission_missing")
        except GitHubError as error:
            # Drake's own bounded code, never GitHub's message.
            logger.warning("gitops: provider refused (%s)", error.code)
            return PullRequestResult("terminal", None, error.code)

    async def _reconcile(
        self,
        *,
        installation_id: int,
        repository_id: int,
        owner: str,
        name: str,
        base_branch: str,
        base_commit_sha: str,
        head_branch: str,
        content: str,
        placeholders: list[str],
    ) -> PullRequestResult:
        # 1. A token for THIS repository and nothing else, carrying the
        #    exact minimum. Scoping by numeric id, not by name: a rename
        #    between projection and now must not silently retarget the
        #    write.
        token = await self._client.installation_token(
            installation_id,
            repository_ids=[repository_id],
            permissions=dict(GITOPS_PERMISSIONS),
        )
        shortfall = missing_permissions(token.permissions, dict(GITOPS_PERMISSIONS))
        if shortfall:
            # Terminal. Retrying cannot grant a permission; a human has to
            # accept the App's updated permission set.
            logger.warning("gitops: installation is missing %s", sorted(shortfall))
            return PullRequestResult("terminal", None, "github_permission_missing")

        # 2. Identity, re-checked at the provider. The projection said this
        #    repository has this numeric id; if it does not, something moved
        #    and Drake is about to write to the wrong place.
        repository = await self._client.get_repository(token, owner, name)
        if int(repository.get("id") or 0) != repository_id:
            return PullRequestResult("terminal", None, "repository_identity_mismatch")
        if bool(repository.get("archived")) or bool(repository.get("disabled")):
            return PullRequestResult("terminal", None, "repository_unavailable")

        # 3. The base has to still be what was reviewed. A plan describes
        #    one commit, and proposing it onto a branch that has moved makes
        #    a claim about code nobody looked at.
        current_base = await self._client.get_branch_head(token, owner, name, base_branch)
        if current_base is None:
            return PullRequestResult("terminal", None, "base_branch_missing")
        if current_base != base_commit_sha:
            # Zero mutations. The request goes stale and a re-analysis
            # produces a proposal for the commit that is actually there.
            return PullRequestResult("terminal", None, "base_moved")

        # 4. Reuse before create, always. If the proposal already has an
        #    open pull request, that IS the answer — opening a second one
        #    would split the review.
        existing = await self._client.find_pull_request(
            token, owner, name, head=head_branch, base=base_branch
        )
        if existing is not None:
            number = int(existing.get("number") or 0)
            if number <= 0:
                raise GitHubContractError("pull request response carried no number")
            return PullRequestResult("exists", number, None)

        # 5. The branch. Created only from the reviewed commit, and only
        #    reused when it is genuinely ours and genuinely matches.
        head = await self._client.get_branch_head(token, owner, name, head_branch)
        if head is None:
            await self._client.create_branch(token, owner, name, head_branch, base_commit_sha)
        elif head != base_commit_sha and not await self._file_matches(
            token, owner, name, head_branch, content
        ):
            # A branch with Drake's name that is not Drake's proposal — a
            # leftover, a rename, or somebody else's work. Never overwrite
            # it, and never claim its contents as this proposal's.
            return PullRequestResult("terminal", None, "branch_conflict")

        # 6. The file. Written only if it is not already exactly right,
        #    because a no-op commit is still a commit in somebody's history.
        if not await self._file_matches(token, owner, name, head_branch, content):
            await self._write_file(token, owner, name, head_branch, content)

        # 7. The pull request. Search again first: an earlier attempt may
        #    have created one and lost the response.
        existing = await self._client.find_pull_request(
            token, owner, name, head=head_branch, base=base_branch
        )
        if existing is not None:
            return PullRequestResult("exists", int(existing.get("number") or 0), None)

        try:
            created = await self._client.create_pull_request(
                token,
                owner,
                name,
                head=head_branch,
                base=base_branch,
                title=_TITLE,
                body=_body(head_branch.rsplit("/", 1)[-1], base_commit_sha, placeholders),
            )
        except GitHubContractError:
            # 422 is what GitHub answers when the pull request already
            # exists. Look, rather than assume either way.
            found = await self._client.find_pull_request(
                token, owner, name, head=head_branch, base=base_branch
            )
            if found is not None:
                return PullRequestResult("exists", int(found.get("number") or 0), None)
            raise

        number = int(created.get("number") or 0)
        if number <= 0:
            raise GitHubContractError("pull request response carried no number")
        return PullRequestResult("created", number, None)

    async def _file_matches(
        self, token: Any, owner: str, name: str, branch: str, content: str
    ) -> bool:
        """Is the manifest on this branch already exactly what we would write?"""
        entry = await self._read_file(token, owner, name, branch)
        if entry is None:
            return False
        return entry[0] == content

    async def _read_file(
        self, token: Any, owner: str, name: str, branch: str
    ) -> tuple[str, str] | None:
        """`(content, blob sha)` for the manifest on a branch, or `None`."""
        head = await self._client.get_branch_head(token, owner, name, branch)
        if head is None:
            return None
        try:
            entry = await self._client.get_content(token, owner, name, ALLOWED_PATH, head)
        except GitHubNotFoundError:
            return None
        raw = entry.get("content")
        sha = entry.get("sha")
        if not isinstance(raw, str) or not isinstance(sha, str):
            return None
        try:
            decoded = base64.b64decode(raw.encode(), validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        return decoded, sha

    async def _write_file(
        self, token: Any, owner: str, name: str, branch: str, content: str
    ) -> None:
        existing = await self._read_file(token, owner, name, branch)
        await self._client.put_file(
            token,
            owner,
            name,
            path=ALLOWED_PATH,
            branch=branch,
            message="Add Drake project manifest (draft)",
            content_base64=base64.b64encode(content.encode("utf-8")).decode(),
            expected_sha=existing[1] if existing else None,
        )
