"""A stateful fake of the GitHub write API.

Enough of it to hold the provider to its contract: refs, single-file
contents writes, and pull requests, with real state so that "create or
reuse" means something. It also fails in the specific ways that make
create-or-reuse necessary — a response that never arrives after the write
was applied, a branch that already exists, a base that moved.

No network, no credentials, no real repository. Every response is composed
here.
"""

import base64
import hashlib
import json
from typing import Any

import httpx


class WriteFakeGitHub:
    """One repository's worth of git state, plus the failures that matter."""

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

        #: branch → commit sha
        self.branches: dict[str, str] = {default_branch: base_sha}
        #: (branch, path) → file content
        self.files: dict[tuple[str, str], str] = {}
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
        #: Endpoints that answer with a status, once each.
        self.fail_with: dict[str, int] = {}
        #: Endpoints that answer 422, once each.
        self.conflict_on: set[str] = set()

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

    def _maybe_fail(self, key: str) -> httpx.Response | None:
        if key in self.fail_with:
            status = self.fail_with.pop(key)
            headers = {"x-ratelimit-remaining": "0"} if status == 429 else {}
            return httpx.Response(status, json={"message": "fake"}, headers=headers)
        if key in self.conflict_on:
            self.conflict_on.discard(key)
            return httpx.Response(422, json={"message": "already exists"})
        return None

    # --- transport -------------------------------------------------------

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
                ref = request.url.params.get("ref", self.default_branch)
                branch = next((b for b, sha in self.branches.items() if sha == ref), ref)
                content = self.files.get((branch, file_path))
                if content is None:
                    return self._json(404, {"message": "Not Found"})
                return self._json(
                    200,
                    {
                        "path": file_path,
                        "sha": self._sha(branch, file_path, content),
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
                self.files[(branch, file_path)] = content
                commit = self._sha(branch, file_path, content, "commit")
                self.branches[branch] = commit
                self.applied.append(f"commit:{branch}:{file_path}")
                if "PUT /contents" in self.swallow_response:
                    self.swallow_response.discard("PUT /contents")
                    raise httpx.ReadTimeout("response lost", request=request)
                return self._json(
                    200,
                    {
                        "content": {
                            "path": file_path,
                            "sha": self._sha(branch, file_path, content),
                        },
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

    def counts(self) -> dict[str, int]:
        """How many of each mutation was actually APPLIED."""
        return {
            "branches": sum(1 for entry in self.applied if entry.startswith("branch:")),
            "commits": sum(1 for entry in self.applied if entry.startswith("commit:")),
            "pulls": sum(1 for entry in self.applied if entry.startswith("pull:")),
        }
