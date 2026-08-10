"""A stateful fake of the GitHub write API.

Enough of it to hold the provider to its contract: a real commit graph with
per-commit file snapshots, refs, single-file contents writes, comparisons,
and pull requests — so that "create or reuse" and "this branch carries only
Drake's proposal" both mean something.

It also fails in the specific ways that make create-or-reuse necessary: a
response that never arrives after the write was applied, a ref that already
exists, a base that moved. And it can be made to INTERLEAVE: a rendezvous on
an endpoint holds every caller that reaches it until the expected number have
arrived, so two providers genuinely race the same write instead of taking
turns.

No network, no credentials, no real repository. Every response is composed
here.
"""

import asyncio
import base64
import hashlib
import json
from typing import Any

import httpx

#: How long a rendezvous waits for its other callers before giving up. A
#: deadline rather than a hang: a test whose expected concurrency never
#: materialises should fail on its assertions, not time the suite out.
_RENDEZVOUS_TIMEOUT = 5.0


class WriteFakeGitHub:
    """One repository's worth of git history, plus the failures that matter."""

    def __init__(
        self,
        *,
        owner: str = "Duosis-Developer-Team",
        name: str = "Hermes",
        repository_id: int = 900001,
        installation_id: int = 55501,
        default_branch: str = "main",
        base_sha: str = "a" * 40,
    ) -> None:
        self.owner = owner
        self.name = name
        self.repository_id = repository_id
        self.installation_id = installation_id
        self.default_branch = default_branch
        self.base_sha = base_sha

        #: commit sha → {"parent": sha | None, "files": {path: content}}
        #:
        #: Files are a SNAPSHOT per commit, not a per-branch dictionary. A
        #: comparison has to answer "what changed between these two commits",
        #: and that question needs history, not current state.
        self.commits: dict[str, dict[str, Any]] = {base_sha: {"parent": None, "files": {}}}
        #: branch → commit sha
        self.branches: dict[str, str] = {default_branch: base_sha}
        #: open pull requests, in creation order
        self.pulls: list[dict[str, Any]] = []

        self.granted_permissions: dict[str, str] = {
            "metadata": "read",
            "contents": "write",
            "pull_requests": "write",
        }
        self.archived = False
        self.disabled = False

        #: Every request, as "METHOD /path" — the count that proves
        #: "exactly one external create".
        self.calls: list[str] = []
        #: Mutations that were APPLIED, whatever the client was told.
        self.applied: list[str] = []

        #: Endpoints whose response is dropped AFTER the state change, once
        #: each: `{"POST /git/refs", "PUT /contents", "POST /pulls"}`.
        self.swallow_response: set[str] = set()
        #: Endpoints that answer with a status, once each. The value is a
        #: status, or `(status, headers)` when the headers are the point.
        self.fail_with: dict[str, int | tuple[int, dict[str, str]]] = {}
        #: Endpoints that answer 422, once each.
        self.conflict_on: set[str] = set()

        #: endpoint → how many callers must arrive before ANY may proceed.
        #: This is what makes a race a race.
        self.rendezvous: dict[str, int] = {}
        self._arrived: dict[str, int] = {}
        self._gates: dict[str, asyncio.Event] = {}

    # --- history helpers a test composes state with -----------------------

    def _commit(self, parent: str, changes: dict[str, str | None]) -> str:
        """Apply `changes` on top of `parent` and return the new commit sha."""
        files = dict(self.commits[parent]["files"])
        for path, content in changes.items():
            if content is None:
                files.pop(path, None)
            else:
                files[path] = content
        sha = hashlib.sha1(  # noqa: S324 - fake commit ids
            f"{parent}::{sorted(files.items())}".encode()
        ).hexdigest()
        self.commits[sha] = {"parent": parent, "files": files}
        return sha

    def commit_on(self, branch: str, changes: dict[str, str | None]) -> str:
        """Put a commit on a branch OUT OF BAND — somebody else's work."""
        parent = self.branches[branch]
        sha = self._commit(parent, changes)
        self.branches[branch] = sha
        return sha

    def branch_at(self, branch: str, sha: str | None = None) -> None:
        """Create a ref out of band, at the base commit unless told otherwise."""
        self.branches[branch] = sha or self.base_sha

    def _chain(self, sha: str) -> list[str]:
        """A commit and its ancestors, newest first."""
        chain: list[str] = []
        cursor: str | None = sha
        while cursor is not None and cursor in self.commits:
            chain.append(cursor)
            cursor = self.commits[cursor]["parent"]
        return chain

    def _files_at(self, sha: str) -> dict[str, str]:
        entry = self.commits.get(sha)
        return dict(entry["files"]) if entry else {}

    def _compare(self, base: str, head: str) -> dict[str, Any] | None:
        head_chain = self._chain(head)
        base_chain = self._chain(base)
        if not head_chain or not base_chain:
            return None
        base_set = set(base_chain)
        merge_base = next((sha for sha in head_chain if sha in base_set), None)
        if merge_base is None:
            return None
        ahead = head_chain.index(merge_base)
        behind = base_chain.index(merge_base)
        if ahead and behind:
            status = "diverged"
        elif ahead:
            status = "ahead"
        elif behind:
            status = "behind"
        else:
            status = "identical"

        before = self._files_at(merge_base)
        after = self._files_at(head)
        files = [
            {
                "filename": path,
                "status": (
                    "added"
                    if path not in before
                    else ("removed" if path not in after else "modified")
                ),
            }
            for path in sorted(set(before) | set(after))
            if before.get(path) != after.get(path)
        ]
        return {
            "status": status,
            "ahead_by": ahead,
            "behind_by": behind,
            "total_commits": ahead,
            "merge_base_commit": {"sha": merge_base},
            "commits": [{"sha": sha} for sha in reversed(head_chain[:ahead])],
            "files": files,
        }

    # --- helpers ---------------------------------------------------------

    def _sha(self, *parts: str) -> str:
        return hashlib.sha1("::".join(parts).encode()).hexdigest()  # noqa: S324 - fake ids

    def _pull_for(self, head: str, base: str) -> dict[str, Any] | None:
        for pull in self.pulls:
            if pull["head"]["ref"] == head and pull["base"]["ref"] == base:
                return pull
        return None

    @staticmethod
    def _json(status: int, payload: Any) -> httpx.Response:
        return httpx.Response(status, json=payload)

    @staticmethod
    def endpoint(method: str, path: str) -> str:
        """The stable key tests attach failures and rendezvous to."""
        if path.endswith("/access_tokens"):
            return "POST /access_tokens"
        for marker in ("/git/refs", "/contents", "/pulls", "/compare"):
            if marker in path:
                return f"{method} {marker}"
        if "/git/ref/" in path:
            return "GET /git/ref"
        return f"{method} /repo"

    def _maybe_fail(self, key: str) -> httpx.Response | None:
        if key in self.fail_with:
            injected = self.fail_with.pop(key)
            status, headers = injected if isinstance(injected, tuple) else (injected, {})
            return httpx.Response(status, json={"message": "fake"}, headers=headers)
        if key in self.conflict_on:
            self.conflict_on.discard(key)
            return httpx.Response(422, json={"message": "already exists"})
        return None

    async def _rendezvous(self, key: str) -> None:
        """Hold this caller until the expected number have reached `key`.

        Every caller has therefore already done its READS by the time any of
        them writes — which is exactly the interleaving that turns two
        sequential passes into a genuine race.
        """
        needed = self.rendezvous.get(key)
        if not needed:
            return
        gate = self._gates.setdefault(key, asyncio.Event())
        self._arrived[key] = self._arrived.get(key, 0) + 1
        if self._arrived[key] >= needed:
            gate.set()
            return
        try:
            await asyncio.wait_for(gate.wait(), _RENDEZVOUS_TIMEOUT)
        except TimeoutError:  # pragma: no cover - only on a broken test
            return

    # --- transport -------------------------------------------------------

    def transport(self) -> httpx.AsyncBaseTransport:
        """An async transport, so a rendezvous can actually suspend a caller."""
        return _FakeTransport(self)

    async def ahandle(self, request: httpx.Request) -> httpx.Response:
        await self._rendezvous(self.endpoint(request.method, request.url.path))
        return self.handler(request)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.calls.append(f"{method} {path}")
        prefix = f"/repos/{self.owner}/{self.name}"

        if method == "POST" and path.endswith("/access_tokens"):
            body = json.loads(request.content or b"{}")
            # The fake grants the INTERSECTION of what was asked for and
            # what the installation has, exactly as GitHub does — so a test
            # can narrow `granted_permissions` and see the provider refuse.
            asked = body.get("permissions") or {}
            granted = {
                key: value for key, value in self.granted_permissions.items() if key in asked
            }
            return self._json(
                201,
                {
                    "token": "ghs_fake_token_value",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "permissions": granted,
                },
            )

        if method == "GET" and path == prefix:
            return self._json(
                200,
                {
                    "id": self.repository_id,
                    "name": self.name,
                    "full_name": f"{self.owner}/{self.name}",
                    "default_branch": self.default_branch,
                    "private": True,
                    "visibility": "private",
                    "archived": self.archived,
                    "disabled": self.disabled,
                },
            )

        if method == "GET" and path.startswith(f"{prefix}/git/ref/heads/"):
            branch = path.split("/git/ref/heads/", 1)[1]
            sha = self.branches.get(branch)
            if sha is None:
                return self._json(404, {"message": "Not Found"})
            return self._json(200, {"ref": f"refs/heads/{branch}", "object": {"sha": sha}})

        if method == "GET" and path.startswith(f"{prefix}/compare/"):
            failure = self._maybe_fail("GET /compare")
            if failure is not None:
                return failure
            basehead = path.split("/compare/", 1)[1]
            if "..." not in basehead:
                return self._json(404, {"message": "Not Found"})
            base, head = basehead.split("...", 1)
            comparison = self._compare(base, head)
            if comparison is None:
                return self._json(404, {"message": "Not Found"})
            return self._json(200, comparison)

        if method == "POST" and path == f"{prefix}/git/refs":
            failure = self._maybe_fail("POST /git/refs")
            if failure is not None:
                return failure
            body = json.loads(request.content or b"{}")
            branch = str(body.get("ref", "")).removeprefix("refs/heads/")
            if branch in self.branches:
                return self._json(422, {"message": "Reference already exists"})
            self.branches[branch] = str(body.get("sha"))
            self.applied.append(f"branch:{branch}")
            if "POST /git/refs" in self.swallow_response:
                self.swallow_response.discard("POST /git/refs")
                # Applied, and the caller never hears about it.
                raise httpx.ReadTimeout("response lost", request=request)
            return self._json(201, {"ref": body.get("ref"), "object": {"sha": body.get("sha")}})

        if path.startswith(f"{prefix}/contents/"):
            file_path = path.split("/contents/", 1)[1]
            if method == "GET":
                # Reads are pinned to a commit sha; the provider never asks
                # for "the branch".
                ref = request.url.params.get("ref", self.branches[self.default_branch])
                content = self._files_at(str(ref)).get(file_path)
                if content is None:
                    return self._json(404, {"message": "Not Found"})
                return self._json(
                    200,
                    {
                        "path": file_path,
                        "sha": self._sha(file_path, content),
                        "size": len(content),
                        "encoding": "base64",
                        "content": base64.b64encode(content.encode()).decode(),
                    },
                )
            if method == "PUT":
                failure = self._maybe_fail("PUT /contents")
                if failure is not None:
                    return failure
                body = json.loads(request.content or b"{}")
                branch = str(body.get("branch"))
                content = base64.b64decode(str(body.get("content"))).decode()
                parent = self.branches.get(branch)
                if parent is None:
                    return self._json(404, {"message": "Not Found"})
                expected = body.get("sha")
                current = self._files_at(parent).get(file_path)
                if current is not None and expected != self._sha(file_path, current):
                    # Optimistic concurrency, as GitHub enforces it: writing
                    # over a file needs the blob sha being replaced.
                    return self._json(409, {"message": "is at another sha"})
                commit = self._commit(parent, {file_path: content})
                self.branches[branch] = commit
                self.applied.append(f"commit:{branch}:{file_path}")
                if "PUT /contents" in self.swallow_response:
                    self.swallow_response.discard("PUT /contents")
                    raise httpx.ReadTimeout("response lost", request=request)
                return self._json(
                    200,
                    {
                        "content": {"path": file_path, "sha": self._sha(file_path, content)},
                        "commit": {"sha": commit},
                    },
                )

        if path == f"{prefix}/pulls":
            if method == "GET":
                head = str(request.url.params.get("head", ""))
                base = str(request.url.params.get("base", ""))
                head_ref = head.split(":", 1)[1] if ":" in head else head
                found = self._pull_for(head_ref, base)
                return self._json(200, [found] if found else [])
            if method == "POST":
                failure = self._maybe_fail("POST /pulls")
                if failure is not None:
                    return failure
                body = json.loads(request.content or b"{}")
                head_ref = str(body.get("head"))
                base_ref = str(body.get("base"))
                existing = self._pull_for(head_ref, base_ref)
                if existing is not None:
                    return self._json(422, {"message": "A pull request already exists"})
                pull = {
                    "number": len(self.pulls) + 101,
                    "state": "open",
                    "draft": bool(body.get("draft")),
                    "title": body.get("title"),
                    "body": body.get("body"),
                    "head": {"ref": head_ref},
                    "base": {"ref": base_ref},
                }
                self.pulls.append(pull)
                self.applied.append(f"pull:{head_ref}")
                if "POST /pulls" in self.swallow_response:
                    self.swallow_response.discard("POST /pulls")
                    raise httpx.ReadTimeout("response lost", request=request)
                return self._json(201, pull)

        return self._json(404, {"message": "Not Found"})

    # --- assertions the tests read ---------------------------------------

    def file_on(self, branch: str, path: str = ".drake/project.yaml") -> str | None:
        """What a branch currently holds at a path."""
        sha = self.branches.get(branch)
        return self._files_at(sha).get(path) if sha else None

    def counts(self) -> dict[str, int]:
        """How many of each mutation was actually APPLIED."""
        return {
            "branches": sum(1 for entry in self.applied if entry.startswith("branch:")),
            "commits": sum(1 for entry in self.applied if entry.startswith("commit:")),
            "pulls": sum(1 for entry in self.applied if entry.startswith("pull:")),
        }


class _FakeTransport(httpx.AsyncBaseTransport):
    """Async, so the fake can suspend a caller mid-request."""

    def __init__(self, fake: WriteFakeGitHub) -> None:
        self._fake = fake

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        return await self._fake.ahandle(request)
