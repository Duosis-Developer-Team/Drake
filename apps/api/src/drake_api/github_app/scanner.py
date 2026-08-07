"""Bounded, static-only repository discovery (Sprint 5B).

What this does: reads a short allowlist of metadata files through the
GitHub Contents API, at one immutable commit SHA, under hard budgets, and
reports what it can see.

What this deliberately does NOT do, and why: the repository is untrusted
input. Nothing here clones, downloads an archive, invokes Git, starts a
shell, installs a package, runs a build or test, executes a hook, or
touches a repository binary. Discovery that runs the thing it is
discovering is not discovery.

Every read is pinned to `ref=<commit sha>`. Reading "the default branch"
across several calls would let the repository change underneath the scan
and produce a report describing a state that never existed.
"""

import base64
import binascii
import fnmatch
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from drake_api.github_app.client import (
    GitHubClient,
    GitHubContractError,
    GitHubError,
    GitHubNotFoundError,
)
from drake_api.github_app.manifest import MANIFEST_PATH

# --- budgets -------------------------------------------------------------
# Central, testable, and deliberately small. A metadata scan that needs
# more than this is not a metadata scan.


@dataclass(frozen=True)
class ScanBudget:
    max_files: int = 60
    max_file_bytes: int = 256 * 1024
    max_total_bytes: int = 1024 * 1024
    max_provider_calls: int = 80
    max_seconds: float = 15.0
    max_depth: int = 2


DEFAULT_BUDGET = ScanBudget()

# --- allowlist -----------------------------------------------------------
# Only these paths are ever read. There is no recursive crawl: each entry
# is either an exact path or a single bounded directory listing filtered by
# a glob, so a repository cannot steer the scanner somewhere it chose.

EXACT_FILES: tuple[str, ...] = (
    MANIFEST_PATH,
    "package.json",
    "pnpm-workspace.yaml",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "Dockerfile",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "Chart.yaml",
    "kustomization.yaml",
    "kustomization.yml",
    "skaffold.yaml",
)

# README has many spellings; the listing is filtered rather than guessed at.
ROOT_GLOBS: tuple[str, ...] = ("README*", "Dockerfile*", "requirements*.txt")

# (directory, glob, max entries taken). Bounded listings only.
DIRECTORY_ALLOWLIST: tuple[tuple[str, str, int], ...] = (
    (".github/workflows", "*.y*ml", 12),
    ("deploy", "*.y*ml", 8),
    ("charts", "Chart.yaml", 4),
    ("k8s", "*.y*ml", 8),
)

# Object types we refuse to follow. A symlink can point outside the tree
# and a submodule is a different repository entirely; neither is metadata
# about THIS repository at THIS commit.
_READABLE_TYPES = frozenset({"file"})


class ScanBudgetExceededError(RuntimeError):
    """A budget stopped the scan. The partial result is not a full picture."""

    def __init__(self, budget: str) -> None:
        super().__init__(f"static scan exceeded its {budget} budget")
        self.budget = budget


@dataclass
class DiscoveredFile:
    path: str
    size: int
    sha256: str


@dataclass
class Detection:
    """A suggestion, with what it was based on and how sure we are.

    Suggestions are for a human to accept or correct. Anything Drake cannot
    establish from observed metadata — tenant model, criticality, owning
    team, cluster, namespace, backup policy — is never guessed here.
    """

    kind: str
    value: str
    evidence: str
    confidence: str


@dataclass
class ScanResult:
    commit_sha: str
    default_branch: str
    files: list[DiscoveredFile] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    manifest_text: str | None = None
    manifest_found: bool = False
    truncated: bool = False
    provider_calls: int = 0
    total_bytes: int = 0

    def as_json(self) -> dict[str, Any]:
        """The bounded, secret-free summary that reaches the database.

        File CONTENT is deliberately absent: paths, sizes and digests are
        enough to explain a suggestion, and storing repository source would
        turn Drake's database into a copy of every repository it scans.
        """
        return {
            "commit_sha": self.commit_sha,
            "default_branch": self.default_branch,
            "files": [
                {"path": item.path, "size": item.size, "sha256": item.sha256} for item in self.files
            ],
            "detections": [
                {
                    "kind": item.kind,
                    "value": item.value,
                    "evidence": item.evidence,
                    "confidence": item.confidence,
                }
                for item in self.detections
            ],
            "warnings": list(self.warnings),
            "manifest_found": self.manifest_found,
            "truncated": self.truncated,
            "provider_calls": self.provider_calls,
            "total_bytes": self.total_bytes,
        }


# Credential shapes worth flagging in a scanned file. Only the PATH and the
# rule are ever retained — never the matched text.
_SECRET_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key-material", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("cloud-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    (
        "credential-assignment",
        re.compile(
            r"\b(?:password|api[_-]?key|secret[_-]?key|client[_-]?secret|auth[_-]?token)"
            r"\b\s*[:=]\s*['\"]?\S{8,}",
            re.IGNORECASE,
        ),
    ),
)


class _Meter:
    """Counts everything the budgets bound, in one place."""

    def __init__(self, budget: ScanBudget, clock: Any = time.monotonic) -> None:
        self.budget = budget
        self._clock = clock
        self._started = clock()
        self.calls = 0
        self.files = 0
        self.total_bytes = 0

    def call(self) -> None:
        self.calls += 1
        if self.calls > self.budget.max_provider_calls:
            raise ScanBudgetExceededError("provider call")
        self.deadline()

    def deadline(self) -> None:
        if self._clock() - self._started > self.budget.max_seconds:
            raise ScanBudgetExceededError("time")

    def file(self, size: int) -> None:
        self.files += 1
        if self.files > self.budget.max_files:
            raise ScanBudgetExceededError("file count")
        self.total_bytes += size
        if self.total_bytes > self.budget.max_total_bytes:
            raise ScanBudgetExceededError("total bytes")


def _decode(entry: dict[str, Any], budget: ScanBudget) -> str | None:
    """Decode a Contents API file entry, or refuse it.

    Returns None for anything that is not a plain, small, UTF-8 text file:
    binaries, oversized files, and entries GitHub itself truncated.
    """
    if str(entry.get("type") or "") not in _READABLE_TYPES:
        return None
    size = int(entry.get("size") or 0)
    if size > budget.max_file_bytes:
        return None
    if str(entry.get("encoding") or "") != "base64":
        # GitHub returns `encoding: none` for files it declines to inline.
        return None
    try:
        raw = base64.b64decode(str(entry.get("content") or ""), validate=False)
    except (binascii.Error, ValueError):
        return None
    if len(raw) > budget.max_file_bytes:
        return None
    if b"\x00" in raw[:4096]:
        return None  # binary
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _scan_for_secret_shapes(path: str, text: str) -> list[dict[str, str]]:
    """Report the PATH of a credential-shaped finding, never the value."""
    return [
        {"path": path, "rule": rule, "message": "Credential-shaped content found in this file."}
        for rule, pattern in _SECRET_SHAPES
        if pattern.search(text)
    ]


def _detect(path: str, text: str) -> list[Detection]:
    """Suggest runtimes and services from observed metadata only."""
    detections: list[Detection] = []
    name = path.rsplit("/", 1)[-1]

    if name == "pyproject.toml":
        if "fastapi" in text:
            detections.append(Detection("runtime", "fastapi", f"{path} declares fastapi", "high"))
        else:
            detections.append(Detection("runtime", "python", f"{path} present", "medium"))
    elif name == "package.json":
        if '"next"' in text:
            detections.append(Detection("runtime", "nextjs", f"{path} declares next", "high"))
        else:
            detections.append(Detection("runtime", "node", f"{path} present", "medium"))
    elif name == "go.mod":
        detections.append(Detection("runtime", "go", f"{path} present", "high"))
    elif name.startswith("Dockerfile"):
        detections.append(Detection("packaging", "container", f"{path} present", "high"))
    elif name == "Chart.yaml" or path.startswith("charts/"):
        detections.append(Detection("deployment", "helm", f"{path} present", "medium"))
    elif name.startswith("kustomization"):
        detections.append(Detection("deployment", "kustomize", f"{path} present", "medium"))
    elif path.startswith(".github/workflows/"):
        detections.append(Detection("ci", "github-actions", f"{path} present", "high"))
    elif name.startswith("compose") or name.startswith("docker-compose"):
        detections.append(Detection("deployment", "compose", f"{path} present", "low"))
    return detections


class RepositoryScanner:
    """Reads allowlisted metadata at one commit, under hard budgets."""

    def __init__(self, client: GitHubClient, budget: ScanBudget = DEFAULT_BUDGET) -> None:
        self._client = client
        self._budget = budget

    async def scan(self, token: Any, owner: str, repo: str, default_branch: str) -> ScanResult:
        """Resolve the branch to a SHA once, then read only against it."""
        meter = _Meter(self._budget)

        meter.call()
        commit_sha = await self._client.resolve_branch_head(token, owner, repo, default_branch)

        result = ScanResult(commit_sha=commit_sha, default_branch=default_branch)
        try:
            await self._read_exact_files(token, owner, repo, meter, result)
            await self._read_root_globs(token, owner, repo, meter, result)
            await self._read_allowlisted_directories(token, owner, repo, meter, result)
        except ScanBudgetExceededError as exceeded:
            # A budget stop is an honest, reportable outcome — not a failure
            # to hide and not a complete picture to act on.
            result.truncated = True
            result.warnings.append(
                {
                    "path": "(scan)",
                    "rule": f"budget-{exceeded.budget.replace(' ', '-')}",
                    "message": (
                        f"The scan stopped at its {exceeded.budget} budget; "
                        "the result is incomplete."
                    ),
                }
            )

        result.provider_calls = meter.calls
        result.total_bytes = meter.total_bytes
        return result

    async def _read_one(
        self,
        token: Any,
        owner: str,
        repo: str,
        path: str,
        meter: _Meter,
        result: ScanResult,
    ) -> str | None:
        meter.call()
        try:
            entry = await self._client.get_content(token, owner, repo, path, result.commit_sha)
        except GitHubNotFoundError:
            return None
        except GitHubContractError:
            # A directory where a file was expected, or a shape we do not
            # accept. Neither is worth failing the whole scan over.
            return None
        if not isinstance(entry, dict):
            return None

        entry_type = str(entry.get("type") or "")
        if entry_type in ("symlink", "submodule"):
            result.warnings.append(
                {
                    "path": path,
                    "rule": f"unsupported-{entry_type}",
                    "message": (
                        f"Skipped a {entry_type}: it points outside this commit's file tree."
                    ),
                }
            )
            return None

        text = _decode(entry, self._budget)
        if text is None:
            return None

        meter.file(len(text.encode("utf-8")))
        result.files.append(
            DiscoveredFile(
                path=path,
                size=int(entry.get("size") or len(text)),
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
        result.warnings.extend(_scan_for_secret_shapes(path, text))
        result.detections.extend(_detect(path, text))
        return text

    async def _read_exact_files(
        self, token: Any, owner: str, repo: str, meter: _Meter, result: ScanResult
    ) -> None:
        for path in EXACT_FILES:
            text = await self._read_one(token, owner, repo, path, meter, result)
            if path == MANIFEST_PATH and text is not None:
                result.manifest_found = True
                result.manifest_text = text

    async def _read_root_globs(
        self, token: Any, owner: str, repo: str, meter: _Meter, result: ScanResult
    ) -> None:
        meter.call()
        try:
            entries = await self._client.list_directory(token, owner, repo, "", result.commit_sha)
        except (GitHubNotFoundError, GitHubContractError):
            return
        seen = {item.path for item in result.files}
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get("type") or "") != "file":
                continue
            path = str(entry.get("path") or "")
            if not path or path in seen:
                continue
            if not any(fnmatch.fnmatch(path, pattern) for pattern in ROOT_GLOBS):
                continue
            await self._read_one(token, owner, repo, path, meter, result)

    async def _read_allowlisted_directories(
        self, token: Any, owner: str, repo: str, meter: _Meter, result: ScanResult
    ) -> None:
        for directory, pattern, take in DIRECTORY_ALLOWLIST:
            if directory.count("/") + 1 > self._budget.max_depth:
                continue
            meter.call()
            try:
                entries = await self._client.list_directory(
                    token, owner, repo, directory, result.commit_sha
                )
            except (GitHubNotFoundError, GitHubContractError):
                continue
            taken = 0
            for entry in entries:
                if taken >= take:
                    break
                if not isinstance(entry, dict) or str(entry.get("type") or "") != "file":
                    continue
                path = str(entry.get("path") or "")
                name = path.rsplit("/", 1)[-1]
                if not fnmatch.fnmatch(name, pattern):
                    continue
                await self._read_one(token, owner, repo, path, meter, result)
                taken += 1


def generate_draft_manifest(owner: str, name: str, default_branch: str, result: ScanResult) -> str:
    """Compose a conservative `.drake/project.yaml` from observed metadata.

    Only the repository block is stated as fact, because only it was
    observed. Everything a human must decide is left as an explicit
    placeholder with a comment saying so — a plausible-looking guess at a
    cluster or a criticality is worse than an obvious blank, because it
    invites being committed unread.
    """
    runtimes = [item.value for item in result.detections if item.kind == "runtime"]
    runtime = runtimes[0] if runtimes else "fastapi"
    service_name = f"{name.lower()}-api"

    lines = [
        "# Generated by Drake from static repository discovery.",
        f"# Commit: {result.commit_sha}",
        "#",
        "# Drake filled in only what it could observe. Every REPLACE_ME below",
        "# is a decision a human has to make; Drake does not guess ownership,",
        "# criticality, clusters or namespaces.",
        "#",
        "# Commit this file to the repository as .drake/project.yaml, then run",
        "# the scan again. Drake imports manifests from the repository, never",
        "# a copy edited in the browser.",
        "apiVersion: drake.duosis.com/v1alpha1",
        "kind: ProjectObservability",
        "metadata:",
        f"  name: {name.lower()}",
        f"  displayName: {name}",
        "spec:",
        "  repository:",
        "    provider: github",
        f"    owner: {owner}",
        f"    name: {name}",
        f"    defaultBranch: {default_branch}",
        "  owners:",
        "    - team: REPLACE_ME            # owning team",
        "      role: primary",
        "  environments:",
        "    - name: dev",
        "      runtime: kubernetes",
        f"      branch: {default_branch}",
        "      clusterRef: REPLACE_ME      # an existing Drake cluster",
        "      namespace: REPLACE_ME",
        "      criticality: medium",
        "  services:",
        f"    - name: {service_name}",
        "      component: api",
        f"      runtime: {runtime}",
        "      metricsProfile: REPLACE_ME  # e.g. fastapi-v1",
        "  tenantModel:",
        "    mode: REPLACE_ME              # none | shared | dedicated",
    ]
    return "\n".join(lines) + "\n"


# Fields a generated draft cannot fill in. Surfaced to the operator rather
# than inferred, and the reason each one is a decision, not an observation.
OPERATOR_INPUTS: tuple[dict[str, str], ...] = (
    {"field": "spec.owners[].team", "reason": "Ownership is an organizational decision."},
    {
        "field": "spec.environments[].clusterRef",
        "reason": "Must reference a cluster Drake already knows.",
    },
    {"field": "spec.environments[].namespace", "reason": "Not derivable from repository content."},
    {
        "field": "spec.environments[].criticality",
        "reason": "A business judgement, not a property of the code.",
    },
    {"field": "spec.services[].metricsProfile", "reason": "Chosen from Drake's metric catalogue."},
    {"field": "spec.tenantModel.mode", "reason": "Depends on how the service is operated."},
)


def missing_operator_inputs(manifest_text: str | None) -> list[dict[str, str]]:
    """Which operator decisions are still unmade in a draft."""
    if not manifest_text:
        return [dict(item) for item in OPERATOR_INPUTS]
    return [dict(item) for item in OPERATOR_INPUTS if "REPLACE_ME" in manifest_text]


def error_is_retryable(error: GitHubError) -> bool:
    """A provider problem is honest and retryable; it is never a preview."""
    return error.code in ("github_unavailable", "github_rate_limited")
