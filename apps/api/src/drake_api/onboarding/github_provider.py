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
already true?" before doing anything. A write GitHub refuses as conflicting
(409/422) is resolved the same way, in the context of the endpoint that
refused it: re-read the ref, the file, or the pull request, and continue only
if what is there is this proposal.

**And what a pull request carries is checked, not assumed.** Reuse means the
branch holds exactly one commit on the reviewed base, changing exactly
`.drake/project.yaml`, with exactly the proposed content. A branch carrying
the right manifest AND somebody else's commit is not this proposal, and an
open pull request over it does not make it one.

The pull request is always a DRAFT. The manifest Drake generates leaves
operator decisions as explicit `REPLACE_ME` placeholders, and opening a
review-ready pull request would be a claim that it is finished.
"""

import base64
import logging
from enum import Enum
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
    GitHubWriteConflictError,
)
from drake_api.github_app.manifest import ManifestParseError, check_policy, parse_strict
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

#: The placeholders the generated draft must contain — exactly these, at
#: exactly these locations in the parsed document.
#:
#: An allowlist rather than a count, and paths rather than field names: an
#: unexpected placeholder means the generator changed and this provider has
#: not been re-reviewed, and a MISSING one means a value a person was
#: supposed to decide arrived filled in from somewhere. Pushing either into
#: somebody's repository is the kind of thing that only gets noticed
#: afterwards.
_EXPECTED_PLACEHOLDER_PATHS = frozenset(
    {
        "spec.owners[0].team",
        "spec.environments[0].clusterRef",
        "spec.environments[0].namespace",
        "spec.services[0].metricsProfile",
        "spec.tenantModel.mode",
    }
)

_PLACEHOLDER = "REPLACE_ME"

#: What the draft must literally be. Exact, not "starts with": a manifest
#: for another api version or another kind is not something this provider
#: has been reviewed to push.
_EXPECTED_API_VERSION = "drake.duosis.com/v1alpha1"
_EXPECTED_KIND = "ProjectObservability"
_EXPECTED_PROVIDER = "github"

#: The generator states the analysed commit in its header comment. It is the
#: only place the base commit appears in the file, and it is what a reviewer
#: reads to know which state the proposal describes.
_COMMIT_HEADER = "# Commit: "

#: A generated manifest is a few kilobytes. This is not a tuning knob; it is
#: the point past which something has gone wrong.
_MAX_CONTENT_BYTES = 64 * 1024

_TITLE = "Add Drake project manifest (draft — needs your input)"


class DraftRefusedError(RuntimeError):
    """The bytes about to be written are not the manifest Drake generates."""


class _BranchConflictError(RuntimeError):
    """Drake's branch name over something that is not Drake's proposal."""


class _Branch(Enum):
    """What Drake's branch currently is, as far as this proposal cares."""

    #: No such branch. Drake may create it from the reviewed commit.
    ABSENT = "absent"
    #: The branch exists and points at the reviewed commit — created, but
    #: the manifest commit never landed. Drake may write the file.
    AT_BASE = "at_base"
    #: Exactly this proposal: one commit on the reviewed base, changing only
    #: the manifest, with exactly the content about to be proposed.
    READY = "ready"
    #: Anything else. Never overwritten, never adopted.
    CONFLICT = "conflict"


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


def _placeholder_paths(node: Any, path: str = "") -> list[str]:
    """Where the parsed draft still says `REPLACE_ME`.

    Walks the DOCUMENT, not the text. A text scan cannot tell a placeholder
    in a value from one in a comment, a key, or a string that merely
    contains the word — and "which fields are blank" is a question about
    what the parser will see, since that is what everything downstream acts
    on.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = str(key) if not path else f"{path}.{key}"
            if _PLACEHOLDER in str(key):
                # A placeholder used as a FIELD NAME. Never generated, and
                # not something to describe to a reviewer as a blank to fill
                # in — the shape itself is wrong.
                found.append(f"{child}<key>")
            found.extend(_placeholder_paths(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_placeholder_paths(item, f"{path}[{index}]"))
    elif isinstance(node, str) and _PLACEHOLDER in node:
        # Exactly the placeholder, or a string that embeds it. The second is
        # reported under the same path and then fails the allowlist compare
        # below, because a value like `REPLACE_ME-prod` is a guess wearing a
        # placeholder's clothes.
        found.append(path if node == _PLACEHOLDER else f"{path}<embedded>")
    return found


def assert_draft_is_safe(
    content: str, *, owner: str, name: str, default_branch: str, base_commit_sha: str
) -> list[str]:
    """Refuse to push anything but the manifest Drake meant to generate.

    Checked immediately before the write, against the bytes that are about
    to leave — not against what the generator produced some time earlier.

    It PARSES. A line-by-line scan accepted a document with duplicate keys
    (a reviewer sees one value, the parser uses another) and a document that
    is not a mapping at all, which is the shape of "leading text that
    happens to contain the right line". The parse is `manifest.parse_strict`
    — the same safe loader, duplicate-key refusal and shape bounds the
    import boundary uses, reused rather than re-implemented so the two
    cannot drift into disagreeing about what is safe.

    What it does NOT do is validate against the completed-manifest schema. A
    draft is deliberately incomplete: it carries `REPLACE_ME` where a person
    has to decide, and holding it to the schema for a finished manifest
    would mean either failing every draft or weakening that schema.

    The repository context is a parameter, not something inferred from the
    text, because the question is whether the draft describes the repository
    it is about to be written to.
    """
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_CONTENT_BYTES:
        raise DraftRefusedError("manifest draft exceeds the write size budget")

    try:
        document = parse_strict(content)
    except ManifestParseError as error:
        # `rule`, never the parser's prose: this ends up in a log line.
        raise DraftRefusedError(f"manifest draft did not parse safely ({error.rule})") from error

    if document.get("apiVersion") != _EXPECTED_API_VERSION:
        raise DraftRefusedError("manifest draft is not a Drake project manifest")
    if document.get("kind") != _EXPECTED_KIND:
        raise DraftRefusedError("manifest draft is not a project observability manifest")

    spec = document.get("spec")
    if not isinstance(spec, dict):
        raise DraftRefusedError("manifest draft has no spec")
    repository = spec.get("repository")
    if not isinstance(repository, dict):
        raise DraftRefusedError("manifest draft names no repository")
    if repository.get("provider") != _EXPECTED_PROVIDER:
        raise DraftRefusedError("manifest draft names a non-github repository provider")
    # EXACT, including case. The import boundary compares case-insensitively
    # because GitHub itself is; a write boundary should not, because these
    # are the values Drake is about to assert about somebody's repository.
    declared = (
        repository.get("owner"),
        repository.get("name"),
        repository.get("defaultBranch"),
    )
    if declared != (owner, name, default_branch):
        raise DraftRefusedError("manifest draft describes a different repository")

    # The commit the draft claims to describe. It appears once, in the
    # generator's header, and it is what a reviewer reads to know which
    # state this proposal is about.
    stated = [
        line[len(_COMMIT_HEADER) :].strip()
        for line in content.splitlines()
        if line.startswith(_COMMIT_HEADER)
    ]
    if stated != [base_commit_sha]:
        raise DraftRefusedError("manifest draft does not state the base commit being proposed")

    found = sorted(set(_placeholder_paths(document)))
    if found != sorted(_EXPECTED_PLACEHOLDER_PATHS):
        # Both directions. An unexpected placeholder means the generator
        # changed and this provider was not re-reviewed; a missing one means
        # a decision a person was supposed to make arrived already answered.
        raise DraftRefusedError("manifest draft does not carry exactly the expected placeholders")

    # Credential and policy shapes, in the thing about to be committed to
    # somebody else's repository. `check_policy` is the same content policy
    # the import boundary applies — inline credentials, private key
    # material, bearer tokens, plaintext endpoints — so a draft can never
    # carry something a manifest would be refused for.
    findings = check_policy(document)
    if findings:
        raise DraftRefusedError(
            f"manifest draft violates manifest content policy ({findings[0].rule})"
        )
    lowered = content.lower()
    for needle in ("ghp_", "ghs_", "-----begin", "authorization:", "password:"):
        if needle in lowered:
            # Belt and braces: `check_policy` reads parsed VALUES, and this
            # reads the raw bytes, so a credential hidden in a comment is
            # caught too.
            raise DraftRefusedError("manifest draft carries credential-shaped content")
    return found


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
            placeholders = assert_draft_is_safe(
                content,
                owner=owner,
                name=name,
                default_branch=base_branch,
                base_commit_sha=base_commit_sha,
            )
        except DraftRefusedError as refused:
            logger.warning("gitops: refusing to push a draft (%s)", refused)
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
        except _BranchConflictError:
            return PullRequestResult("terminal", None, "branch_conflict")
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

        # 4. What is on Drake's branch, and where did it come from?
        #
        #    Asked BEFORE anything is reused, because "an open pull request
        #    exists for this head and base" says nothing about what the head
        #    carries. A branch holding the expected manifest plus somebody
        #    else's commit, or plus a second file, would otherwise be
        #    presented under Drake's name as Drake's proposal.
        state = await self._inspect_branch(
            token, owner, name, head_branch, base_commit_sha, content
        )

        # 5. Reuse before create, but only what is genuinely ours. If the
        #    proposal already has an open pull request AND the branch really
        #    carries only this proposal, that IS the answer — opening a
        #    second one would split the review.
        existing = await self._client.find_pull_request(
            token, owner, name, head=head_branch, base=base_branch
        )
        if existing is not None:
            if state is not _Branch.READY:
                # An open pull request over a branch Drake cannot vouch for.
                # Adopting it would attach Drake's result to a diff nobody
                # reviewed under this proposal's name.
                raise _BranchConflictError()
            number = int(existing.get("number") or 0)
            if number <= 0:
                raise GitHubContractError("pull request response carried no number")
            return PullRequestResult("exists", number, None)

        if state is _Branch.CONFLICT:
            raise _BranchConflictError()

        # 6. The branch. Created only from the reviewed commit.
        if state is _Branch.ABSENT:
            try:
                await self._client.create_branch(token, owner, name, head_branch, base_commit_sha)
            except GitHubWriteConflictError:
                # Somebody created it between the read and the write — most
                # likely another worker on this same proposal. Re-read: if
                # what is there is this proposal, carry on with it; if not,
                # it is a conflict, and either way nothing is re-sent.
                state = await self._inspect_branch(
                    token, owner, name, head_branch, base_commit_sha, content
                )
                if state is _Branch.CONFLICT or state is _Branch.ABSENT:
                    raise _BranchConflictError() from None
            else:
                state = _Branch.AT_BASE

        # 7. The file. Written only if it is not already exactly right,
        #    because a no-op commit is still a commit in somebody's history.
        if state is not _Branch.READY:
            try:
                await self._write_file(token, owner, name, head_branch, content)
            except GitHubWriteConflictError:
                # A stale blob sha, or the file appearing between the read
                # and the write. Same rule: read what is there now.
                state = await self._inspect_branch(
                    token, owner, name, head_branch, base_commit_sha, content
                )
                if state is not _Branch.READY:
                    raise _BranchConflictError() from None

        # 8. The pull request. Search again first: an earlier attempt may
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
        except GitHubWriteConflictError:
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

    async def _inspect_branch(
        self,
        token: Any,
        owner: str,
        name: str,
        head_branch: str,
        base_commit_sha: str,
        content: str,
    ) -> "_Branch":
        """What is on Drake's branch — and is it only Drake's proposal?

        The invariant this exists to enforce:

            the pull request Drake creates or reuses carries exactly one
            change on top of the reviewed base — `.drake/project.yaml`, with
            exactly the content Drake is proposing.

        Matching content alone is not enough. A branch can hold the expected
        manifest AND a second commit, or AND another file, and a check that
        only asked "is the manifest right?" would call that Drake's work.

        So `READY` requires all of it: the base is the exact merge base, the
        branch is not behind it, there is exactly ONE commit on top, that
        commit changes exactly ONE file, that file is the manifest path, and
        its content is byte-for-byte what is about to be proposed. Anything
        else is `CONFLICT` — never overwritten, never adopted.
        """
        head = await self._client.get_branch_head(token, owner, name, head_branch)
        if head is None:
            return _Branch.ABSENT
        if head == base_commit_sha:
            # The branch exists at the reviewed commit and carries nothing
            # yet — an attempt that created the ref and stopped.
            return _Branch.AT_BASE

        try:
            comparison = await self._client.compare_commits(
                token, owner, name, base=base_commit_sha, head=head
            )
        except GitHubNotFoundError:
            # Unrelated histories — GitHub has no comparison to give. Not
            # knowing how a branch relates to the reviewed base is exactly
            # the case for refusing it.
            return _Branch.CONFLICT
        if str(comparison.get("status") or "") != "ahead":
            # Diverged, or behind: the branch is not a clean proposal on the
            # commit that was reviewed.
            return _Branch.CONFLICT
        if int(comparison.get("behind_by") or 0) != 0:
            return _Branch.CONFLICT
        merge_base = (comparison.get("merge_base_commit") or {}).get("sha")
        if merge_base != base_commit_sha:
            # Ancestry, exactly. Without this the branch could be ahead of a
            # DIFFERENT commit that happens to contain the base's changes.
            return _Branch.CONFLICT
        commits = comparison.get("commits") or []
        if int(comparison.get("total_commits") or 0) != 1 or len(commits) != 1:
            # A foreign commit rode along.
            return _Branch.CONFLICT
        files = comparison.get("files") or []
        if [str((entry or {}).get("filename") or "") for entry in files] != [ALLOWED_PATH]:
            # A second file, or a different one.
            return _Branch.CONFLICT
        if not await self._file_matches(token, owner, name, head_branch, content, at=head):
            # Right path, right shape, different bytes.
            return _Branch.CONFLICT
        return _Branch.READY

    async def _file_matches(
        self, token: Any, owner: str, name: str, branch: str, content: str, at: str | None = None
    ) -> bool:
        """Is the manifest on this branch already exactly what we would write?"""
        entry = await self._read_file(token, owner, name, branch, at)
        if entry is None:
            return False
        return entry[0] == content

    async def _read_file(
        self, token: Any, owner: str, name: str, branch: str, at: str | None = None
    ) -> tuple[str, str] | None:
        """`(content, blob sha)` for the manifest on a branch, or `None`.

        Pinned to a commit: `at` when the caller already resolved the head,
        otherwise resolved here. Reading "the branch" across two calls would
        let it move between them.
        """
        head = at or await self._client.get_branch_head(token, owner, name, branch)
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
        if existing is not None and existing[0] == content:
            # Already exactly right. A no-op commit is still a commit in
            # somebody's history.
            return
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
