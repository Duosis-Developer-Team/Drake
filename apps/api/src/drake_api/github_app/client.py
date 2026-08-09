"""Read-only GitHub REST client with a typed failure taxonomy.

Every call is outbound-only, bounded (timeout, response size, retries),
never follows redirects, and classifies failures so callers can tell
"the answer is no" from "we could not find out". That distinction is the
whole point: an unreadable answer must never look like a passing check
(ADR-0020 §4).
"""

import asyncio
import json
import random
import re
from dataclasses import dataclass
from typing import Any

import httpx

from drake_api.github_app.auth import (
    GitHubAppAuth,
    GitHubAuthError,
    InstallationToken,
    InstallationTokenCache,
    TokenScope,
)
from drake_api.settings import Settings

API_VERSION = "2022-11-28"
# A full 40-hex commit sha. Used to refuse any read that is not pinned.
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")

#: What may appear in a URL path segment Drake composes.
#:
#: Owner and repository names come from Drake's own projection, not from a
#: request — but they are still interpolated into a URL, and a name carrying
#: `/` or `..` would address a different resource than the one meant.
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._-]{1,100}")

#: The branch names Drake is allowed to create. The prefix is server-owned;
#: this is the shape check that keeps it that way at the HTTP boundary.
_BRANCH_NAME = re.compile(r"drake/[a-z0-9][a-z0-9/-]{0,80}")

#: The one file Drake may write.
_ALLOWED_WRITE_PATH = ".drake/project.yaml"

#: How large a body Drake will send. The manifest draft is a few kilobytes;
#: anything approaching this is a bug or an attack, and either way it should
#: not leave the process.
_MAX_REQUEST_BYTES = 128 * 1024

#: The only GitHub API origins a production deployment may talk to.
#:
#: `github_api_base_url` is configurable for the local fake server, and a
#: configurable API origin plus a write credential is an exfiltration
#: primitive: point it somewhere else and Drake pushes a repository's
#: manifest, and its installation token, to whoever is listening.
_PRODUCTION_API_ORIGINS = ("https://api.github.com",)
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_ATTEMPTS = 3
_MAX_PAGES = 20
_PER_PAGE = 100


class GitHubError(RuntimeError):
    """Base class. Messages are bounded and never carry credentials."""

    code = "github_error"


class GitHubUnavailableError(GitHubError):
    """Transport failure or 5xx — retryable, and never a policy PASS."""

    code = "github_unavailable"


class GitHubRateLimitedError(GitHubError):
    code = "github_rate_limited"

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class GitHubForbiddenError(GitHubError):
    """403 — typically an installation permission Drake was not granted."""

    code = "github_forbidden"


class GitHubNotFoundError(GitHubError):
    code = "github_not_found"


class GitHubContractError(GitHubError):
    """The response was not what the documented contract promises."""

    code = "github_contract"


class GitHubAmbiguousWriteError(GitHubError):
    """A write whose outcome is unknown.

    The request left the process and no response came back, so it may or may
    not have been applied. Retrying it is the one thing a caller must not
    do — that is how one intent becomes two branches or two pull requests.
    The only safe resolution is to read the world back and reconcile.
    """

    code = "github_write_ambiguous"


@dataclass(frozen=True)
class GitHubResponse:
    status_code: int
    payload: Any
    headers: dict[str, str]


class GitHubClient:
    """Thin, bounded REST client. The transport is injectable for tests."""

    def __init__(
        self,
        settings: Settings,
        auth: GitHubAppAuth,
        token_cache: InstallationTokenCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._auth = auth
        self._transport = transport
        self._tokens = token_cache or InstallationTokenCache(
            refresh_buffer_seconds=settings.github_token_refresh_buffer_seconds
        )

    @property
    def tokens(self) -> InstallationTokenCache:
        return self._tokens

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.github_api_base_url,
            timeout=self._settings.github_http_timeout_seconds,
            follow_redirects=False,
            transport=self._transport,
        )

    @staticmethod
    def _headers(credential: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {credential}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "drake-control-plane",
        }

    @staticmethod
    def _classify(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        remaining = response.headers.get("x-ratelimit-remaining")
        if status in (403, 429) and remaining == "0":
            retry_after = response.headers.get("retry-after")
            raise GitHubRateLimitedError(
                "github rate limit exhausted",
                retry_after_seconds=int(retry_after) if (retry_after or "").isdigit() else None,
            )
        if status == 401:
            raise GitHubForbiddenError("github refused the credential")
        if status == 403:
            raise GitHubForbiddenError("github refused the request (insufficient permission)")
        if status == 404:
            raise GitHubNotFoundError("github resource not found")
        if status >= 500:
            raise GitHubUnavailableError(f"github responded {status}")
        raise GitHubContractError(f"github responded {status}")

    async def _request(
        self,
        method: str,
        path: str,
        credential: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> GitHubResponse:
        last_error: GitHubError | None = None
        for attempt in range(_MAX_ATTEMPTS):
            if attempt:
                # Bounded jittered backoff; never a tight retry loop.
                delay = min(2.0 * (2 ** (attempt - 1)), 8.0)
                await asyncio.sleep(delay + random.uniform(0, 0.25))  # noqa: S311 - jitter only
            oversized = False
            try:
                async with self._client() as client:
                    request = client.build_request(
                        method,
                        path,
                        headers=self._headers(credential),
                        params=params,
                        json=json_body,
                    )
                    response = await client.send(request, stream=True)
                    try:
                        # Read incrementally and abandon the connection the
                        # moment the budget is crossed. Reading `.content`
                        # first would buffer the whole upstream response and
                        # only then measure it, which is not a limit at all.
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > _MAX_RESPONSE_BYTES:
                                oversized = True
                                break
                            chunks.append(chunk)
                        body = b"".join(chunks)
                    finally:
                        await response.aclose()
            except httpx.HTTPError as error:
                last_error = GitHubUnavailableError("github is unreachable")
                last_error.__cause__ = error
                continue
            if oversized:
                raise GitHubContractError("github response exceeded the size budget")
            try:
                self._classify(response)
            except GitHubUnavailableError as error:
                last_error = error
                continue
            payload: Any = None
            if body:
                try:
                    payload = json.loads(body)
                except ValueError as error:
                    raise GitHubContractError("github response was not JSON") from error
            return GitHubResponse(
                status_code=response.status_code,
                payload=payload,
                headers={key.lower(): value for key, value in response.headers.items()},
            )
        raise last_error or GitHubUnavailableError("github is unreachable")

    # --- app-level (JWT) -------------------------------------------------

    async def list_installations(self) -> list[dict[str, Any]]:
        jwt_token = self._auth.mint_app_jwt()
        response = await self._request(
            "GET", "/app/installations", jwt_token.token, params={"per_page": _PER_PAGE}
        )
        if not isinstance(response.payload, list):
            raise GitHubContractError("installations response was not a list")
        return response.payload

    async def get_installation(self, installation_id: int) -> dict[str, Any]:
        jwt_token = self._auth.mint_app_jwt()
        response = await self._request(
            "GET", f"/app/installations/{installation_id}", jwt_token.token
        )
        if not isinstance(response.payload, dict):
            raise GitHubContractError("installation response was not an object")
        return response.payload

    async def installation_token(
        self,
        installation_id: int,
        *,
        repository_ids: list[int] | None = None,
        permissions: dict[str, str] | None = None,
    ) -> InstallationToken:
        """Mint (or reuse) an installation token, scoped where possible.

        The cache key is the requested SCOPE, not the installation id: a
        token narrowed to one repository must not be handed to a caller
        asking about another, and a metadata-only token must not satisfy a
        caller that asked for more.
        """
        scope = TokenScope.build(installation_id, repository_ids, permissions)
        cached = self._tokens.get(scope)
        if cached is not None:
            return cached
        jwt_token = self._auth.mint_app_jwt()
        body: dict[str, Any] = {}
        if repository_ids:
            body["repository_ids"] = repository_ids
        if permissions:
            body["permissions"] = permissions
        response = await self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            jwt_token.token,
            json_body=body or None,
        )
        payload = response.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("token"), str):
            raise GitHubContractError("installation token response was malformed")
        expires_raw = payload.get("expires_at")
        if not isinstance(expires_raw, str):
            raise GitHubContractError("installation token response had no expiry")
        try:
            import datetime as dt

            expires_at = dt.datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise GitHubContractError("installation token expiry was malformed") from error
        granted = payload.get("permissions")
        token = InstallationToken(
            token=payload["token"],
            expires_at=expires_at,
            permissions=granted if isinstance(granted, dict) else {},
            repository_selection=str(payload.get("repository_selection") or "selected"),
        )
        if permissions:
            # Narrower than requested is an honest outcome to report, never a
            # reason to retry for more.
            token = InstallationToken(
                token=token.token,
                expires_at=token.expires_at,
                permissions=token.permissions,
                repository_selection=token.repository_selection,
            )
        self._tokens.put(scope, token)
        return token

    # --- installation-level ---------------------------------------------

    async def _paginated(
        self, path: str, credential: str, key: str | None = None
    ) -> list[dict[str, Any]]:
        """Collect every page, or say plainly that we could not.

        Stopping at the page cap with a full final page means there was
        more to read. Returning the partial list would let a policy rule
        conclude "no violation found" from an answer that was cut short, so
        the truncation is raised instead of returned.
        """
        collected: list[dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            response = await self._request(
                "GET", path, credential, params={"per_page": _PER_PAGE, "page": page}
            )
            payload = response.payload
            items = payload.get(key) if (key and isinstance(payload, dict)) else payload
            if not isinstance(items, list):
                raise GitHubContractError(f"{path} response was not a list")
            collected.extend(item for item in items if isinstance(item, dict))
            if len(items) < _PER_PAGE:
                return collected
        raise GitHubContractError(f"{path} exceeded the page budget; the listing is incomplete")

    async def list_installation_repositories(
        self, token: InstallationToken
    ) -> list[dict[str, Any]]:
        return await self._paginated("/installation/repositories", token.token, key="repositories")

    async def get_repository(
        self, token: InstallationToken, owner: str, repo: str
    ) -> dict[str, Any]:
        response = await self._request("GET", f"/repos/{owner}/{repo}", token.token)
        if not isinstance(response.payload, dict):
            raise GitHubContractError("repository response was not an object")
        return response.payload

    async def get_branch_protection(
        self, token: InstallationToken, owner: str, repo: str, branch: str
    ) -> dict[str, Any]:
        response = await self._request(
            "GET", f"/repos/{owner}/{repo}/branches/{branch}/protection", token.token
        )
        if not isinstance(response.payload, dict):
            raise GitHubContractError("branch protection response was not an object")
        return response.payload

    async def get_branch_rules(
        self, token: InstallationToken, owner: str, repo: str, branch: str
    ) -> list[dict[str, Any]]:
        """Rules ACTUALLY applying to a branch, per the documented API.

        `GET /repos/{owner}/{repo}/rulesets` returns ruleset *summaries* —
        no `rules` member at all — so treating an entry there as evidence of
        a rule is reading something the response never contained. This
        endpoint returns the effective rules for the branch, already
        resolved across repository and organization rulesets and already
        filtered to active enforcement.
        """
        response = await self._request(
            "GET", f"/repos/{owner}/{repo}/rules/branches/{branch}", token.token
        )
        if not isinstance(response.payload, list):
            raise GitHubContractError("branch rules response was not a list")
        return [item for item in response.payload if isinstance(item, dict)]

    async def list_rulesets(
        self, token: InstallationToken, owner: str, repo: str
    ) -> list[dict[str, Any]]:
        """Ruleset SUMMARIES. These carry no `rules` member by contract."""
        return await self._paginated(f"/repos/{owner}/{repo}/rulesets", token.token)

    async def list_workflows(
        self, token: InstallationToken, owner: str, repo: str
    ) -> list[dict[str, Any]]:
        return await self._paginated(
            f"/repos/{owner}/{repo}/actions/workflows", token.token, key="workflows"
        )

    async def list_environments(
        self, token: InstallationToken, owner: str, repo: str
    ) -> list[dict[str, Any]]:
        return await self._paginated(
            f"/repos/{owner}/{repo}/environments", token.token, key="environments"
        )

    # --- source metadata (Sprint 5B, Contents: read) ---------------------

    async def resolve_branch_head(
        self, token: InstallationToken, owner: str, repo: str, branch: str
    ) -> str:
        """Resolve a branch to the immutable commit SHA it points at.

        Every later read is pinned to this SHA. Reading "the default
        branch" across several calls would let the repository move
        underneath the scan and produce a report of a state that never
        existed all at once.
        """
        response = await self._request(
            "GET", f"/repos/{owner}/{repo}/commits/{branch}", token.token
        )
        payload = response.payload
        if not isinstance(payload, dict):
            raise GitHubContractError("commit response was not an object")
        sha = payload.get("sha")
        if not isinstance(sha, str) or not _COMMIT_SHA.fullmatch(sha):
            raise GitHubContractError("commit response carried no usable sha")
        return sha

    # --- mutations -------------------------------------------------------
    #
    # Deliberately separate from `_request`. That method retries on
    # transport failure, which is right for a GET and wrong for anything
    # that changes state: a POST that timed out may have been applied, and
    # re-sending it blindly is how one intent becomes two branches or two
    # pull requests.
    #
    # A write that ends ambiguously raises, and the provider resolves it by
    # READING the world back — create-or-reuse, not create-again.

    def _assert_write_origin(self) -> None:
        """A production write may only go to GitHub itself."""
        if not self._settings.is_production_like:
            return
        base = self._settings.github_api_base_url.rstrip("/")
        if base not in _PRODUCTION_API_ORIGINS:
            raise GitHubContractError(
                "github write refused: the configured API origin is not github.com"
            )

    @staticmethod
    def _segment(value: str, what: str) -> str:
        if not _PATH_SEGMENT.fullmatch(value):
            raise GitHubContractError(f"unsafe {what} in a composed github url")
        return value

    async def _mutate(
        self,
        method: str,
        path: str,
        credential: str,
        *,
        json_body: dict[str, Any],
    ) -> GitHubResponse:
        """One attempt. No retry, ever.

        Everything else `_request` does — response size budget, no
        redirects, status classification — applies; the difference is that a
        failure here is reported rather than repeated.
        """
        self._assert_write_origin()
        encoded = json.dumps(json_body).encode()
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise GitHubContractError("github request body exceeded the size budget")

        oversized = False
        try:
            async with self._client() as client:
                request = client.build_request(
                    method, path, headers=self._headers(credential), json=json_body
                )
                response = await client.send(request, stream=True)
                try:
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_RESPONSE_BYTES:
                            oversized = True
                            break
                        chunks.append(chunk)
                    body = b"".join(chunks)
                finally:
                    await response.aclose()
        except httpx.HTTPError as error:
            # AMBIGUOUS: the request may or may not have been applied. The
            # caller must reconcile by reading, never by retrying.
            raise GitHubAmbiguousWriteError("github write outcome is unknown") from error

        if oversized:
            raise GitHubContractError("github response exceeded the size budget")
        self._classify(response)
        payload: Any = None
        if body:
            try:
                payload = json.loads(body)
            except ValueError as error:
                raise GitHubContractError("github response was not JSON") from error
        return GitHubResponse(
            status_code=response.status_code,
            payload=payload,
            headers={key.lower(): value for key, value in response.headers.items()},
        )

    async def get_branch_head(
        self, token: InstallationToken, owner: str, repo: str, branch: str
    ) -> str | None:
        """The commit a branch points at, or `None` if it does not exist."""
        owner = self._segment(owner, "owner")
        repo = self._segment(repo, "repository")
        try:
            response = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/git/ref/heads/{branch}",
                token.token,
            )
        except GitHubNotFoundError:
            return None
        payload = response.payload
        if not isinstance(payload, dict):
            raise GitHubContractError("ref response was not an object")
        sha = (payload.get("object") or {}).get("sha")
        if not isinstance(sha, str) or not _COMMIT_SHA.fullmatch(sha):
            raise GitHubContractError("ref response carried no commit sha")
        return sha

    async def create_branch(
        self, token: InstallationToken, owner: str, repo: str, branch: str, sha: str
    ) -> None:
        """Create a ref. Never update, never force, never delete.

        `POST /git/refs` cannot move an existing ref — that needs PATCH with
        `force`, which Drake does not call. So a branch that already exists
        makes this fail rather than silently rewrite somebody's work.
        """
        owner = self._segment(owner, "owner")
        repo = self._segment(repo, "repository")
        if not _BRANCH_NAME.fullmatch(branch):
            raise GitHubContractError("refusing to create a branch outside Drake's prefix")
        if not _COMMIT_SHA.fullmatch(sha):
            raise GitHubContractError("a branch must be created from a full commit sha")
        await self._mutate(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            token.token,
            json_body={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    async def put_file(
        self,
        token: InstallationToken,
        owner: str,
        repo: str,
        *,
        path: str,
        branch: str,
        message: str,
        content_base64: str,
        expected_sha: str | None = None,
    ) -> dict[str, Any]:
        """Write ONE allowlisted file, on a Drake-owned branch."""
        owner = self._segment(owner, "owner")
        repo = self._segment(repo, "repository")
        if path != _ALLOWED_WRITE_PATH:
            raise GitHubContractError("refusing to write outside the allowlisted manifest path")
        if not _BRANCH_NAME.fullmatch(branch):
            raise GitHubContractError("refusing to commit outside Drake's branch prefix")
        body: dict[str, Any] = {
            "message": message,
            "content": content_base64,
            "branch": branch,
        }
        if expected_sha is not None:
            # Optimistic concurrency: updating a file requires the blob sha
            # it is replacing, so a concurrent change fails instead of being
            # overwritten.
            body["sha"] = expected_sha
        response = await self._mutate(
            "PUT", f"/repos/{owner}/{repo}/contents/{path}", token.token, json_body=body
        )
        payload = response.payload
        if not isinstance(payload, dict):
            raise GitHubContractError("content write response was not an object")
        return payload

    async def find_pull_request(
        self, token: InstallationToken, owner: str, repo: str, *, head: str, base: str
    ) -> dict[str, Any] | None:
        """The open pull request for an EXACT head/base pair, if there is one."""
        owner = self._segment(owner, "owner")
        repo = self._segment(repo, "repository")
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            token.token,
            params={"head": f"{owner}:{head}", "base": base, "state": "open", "per_page": 10},
        )
        payload = response.payload
        if not isinstance(payload, list):
            raise GitHubContractError("pull request list response was not a list")
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            # GitHub filters server-side, but the match is re-checked here:
            # reusing "some open pull request" instead of THE one for this
            # proposal would attach Drake's result to somebody else's work.
            entry_head = (entry.get("head") or {}).get("ref") or ""
            entry_base = (entry.get("base") or {}).get("ref") or ""
            if entry_head == head and entry_base == base:
                return entry
        return None

    async def create_pull_request(
        self,
        token: InstallationToken,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        """Open a DRAFT pull request. Never merge, never close, never update."""
        owner = self._segment(owner, "owner")
        repo = self._segment(repo, "repository")
        if not _BRANCH_NAME.fullmatch(head):
            raise GitHubContractError("refusing to open a pull request from a foreign branch")
        response = await self._mutate(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            token.token,
            json_body={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                # Always a draft. The manifest Drake generates has operator
                # fields it deliberately does not guess, so a review-ready
                # pull request would be a claim that it is finished.
                "draft": True,
            },
        )
        payload = response.payload
        if not isinstance(payload, dict):
            raise GitHubContractError("pull request response was not an object")
        return payload

    async def get_content(
        self, token: InstallationToken, owner: str, repo: str, path: str, ref: str
    ) -> dict[str, Any]:
        """One file, at one commit. Never a directory, never an archive."""
        if not _COMMIT_SHA.fullmatch(ref):
            raise GitHubContractError("content reads must be pinned to a commit sha")
        response = await self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", token.token, params={"ref": ref}
        )
        payload = response.payload
        if isinstance(payload, list):
            # The path is a directory; the caller asked for a file.
            raise GitHubContractError("content path is a directory")
        if not isinstance(payload, dict):
            raise GitHubContractError("content response was not an object")
        return payload

    async def list_directory(
        self, token: InstallationToken, owner: str, repo: str, path: str, ref: str
    ) -> list[dict[str, Any]]:
        """One bounded directory listing, at one commit."""
        if not _COMMIT_SHA.fullmatch(ref):
            raise GitHubContractError("directory reads must be pinned to a commit sha")
        response = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}".rstrip("/"),
            token.token,
            params={"ref": ref},
        )
        payload = response.payload
        if not isinstance(payload, list):
            raise GitHubContractError("directory listing was not a list")
        return [item for item in payload if isinstance(item, dict)]

    async def get_environment(
        self, token: InstallationToken, owner: str, repo: str, environment: str
    ) -> dict[str, Any]:
        response = await self._request(
            "GET", f"/repos/{owner}/{repo}/environments/{environment}", token.token
        )
        if not isinstance(response.payload, dict):
            raise GitHubContractError("environment response was not an object")
        return response.payload


def error_code(error: Exception) -> str:
    """Bounded, audit-safe classification for any failure."""
    if isinstance(error, GitHubError):
        return error.code
    if isinstance(error, GitHubAuthError):
        return "github_auth"
    return "github_error"
