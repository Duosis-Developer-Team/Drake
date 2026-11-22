"""Who wrote a commit, and with which coding agent — from the commit alone.

Pure functions, no I/O. The evidence is what the repository already says:

- a `Co-Authored-By:` trailer naming an agent (Claude Code writes
  `Co-Authored-By: Claude …`, Copilot `Co-authored-by: Copilot …`);
- a marker the agent leaves in the message ("Generated with [Claude Code]");
- an agent that authored the commit itself (`copilot-swe-agent[bot]`,
  `devin-ai-integration[bot]`, `chatgpt-codex-connector[bot]`, …).

It is a convention, not a proof: a trailer can be removed, and a commit
without one may still have been written with help. The page says so; this
module only reads what is there. Emails are never kept — a trailer is
matched on its name part and then forgotten.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

AGENTS = ("claude_code", "copilot", "codex", "cursor", "devin", "other_ai", "none")
EVIDENCE = ("trailer", "pr_marker", "bot_author")

# Authors that are agents in their own right: the commit has no human author.
_AGENT_AUTHORS: dict[str, str] = {
    "copilot": "copilot",
    "copilot-swe-agent[bot]": "copilot",
    "copilot-pull-request-reviewer[bot]": "copilot",
    "devin-ai-integration[bot]": "devin",
    "chatgpt-codex-connector[bot]": "codex",
    "codex[bot]": "codex",
    "cursor[bot]": "cursor",
    "cursoragent": "cursor",
    "claude[bot]": "claude_code",
}

# Automation that is not a coding agent and not a person: skipped entirely.
_AUTOMATION = re.compile(
    r"^(dependabot|renovate|github-actions|pre-commit-ci|mergify|snyk-bot|semantic-release-bot)"
    r"(\[bot\])?$",
    re.IGNORECASE,
)

_TRAILER = re.compile(r"^\s*co-authored-by:\s*(?P<who>[^<\n]+?)\s*(<[^>\n]*>)?\s*$", re.I | re.M)

# The name part of a trailer → agent.
_TRAILER_AGENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bclaude\b", re.I), "claude_code"),
    (re.compile(r"\bcopilot\b", re.I), "copilot"),
    (re.compile(r"\bcodex\b|\bopenai\b|\bchatgpt\b", re.I), "codex"),
    (re.compile(r"\bcursor\b", re.I), "cursor"),
    (re.compile(r"\bdevin\b", re.I), "devin"),
    (
        re.compile(r"\bgemini\b|\baider\b|\bamazon q\b|\bwindsurf\b|\bcodeium\b|\bjules\b", re.I),
        "other_ai",
    ),
)

# Markers in the message body.
_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"generated with \[?claude code|claude\.(ai|com)/claude-code", re.I),
        "claude_code",
    ),
    (re.compile(r"generated (with|by) (github )?copilot", re.I), "copilot"),
    (re.compile(r"generated (with|by) (openai )?codex|chatgpt\.com/codex", re.I), "codex"),
    (re.compile(r"generated (with|by) cursor|cursor\.com/agents", re.I), "cursor"),
)

_SQUASH_PR = re.compile(r"\(#(?P<n>\d{1,7})\)\s*$")
_MERGE_PR = re.compile(r"^merge pull request #(?P<n>\d{1,7})\b", re.I)


def is_automation(login: str | None) -> bool:
    """A bot that is not a coding agent (Dependabot, Renovate, Actions)."""
    return bool(login) and bool(_AUTOMATION.match(login or ""))


def agent_author(login: str | None) -> str | None:
    """The agent a commit's author IS, when the author is an agent."""
    if not login:
        return None
    return _AGENT_AUTHORS.get(login.lower())


def classify(message: str, author_login: str | None) -> tuple[str, str | None]:
    """(agent, evidence). `("none", None)` when nothing in the commit says."""
    own = agent_author(author_login)
    if own:
        return own, "bot_author"
    for match in _TRAILER.finditer(message or ""):
        who = match.group("who")
        for pattern, agent in _TRAILER_AGENTS:
            if pattern.search(who):
                return agent, "trailer"
    for pattern, agent in _MARKERS:
        if pattern.search(message or ""):
            return agent, "pr_marker"
    return "none", None


def title_of(message: str) -> str:
    """The first line, bounded. Nothing below it is kept."""
    first = (message or "").strip().splitlines()[0] if (message or "").strip() else ""
    return first[:200]


def pr_number(message: str) -> int | None:
    """The PR a commit landed through, when its first line names it.

    A squash merge ends its title with "(#123)"; a merge commit opens with
    "Merge pull request #123". Commits inside a merged branch name none.
    """
    title = title_of(message)
    for pattern in (_SQUASH_PR, _MERGE_PR):
        match = pattern.search(title)
        if match:
            return int(match.group("n"))
    return None


def _normalise(value: str) -> str:
    return value.lower().replace("_", "-")


def map_services(
    paths: Iterable[str], service_keys: Iterable[str], project_key: str
) -> tuple[list[str], int]:
    """(catalog services the changed paths belong to, files that mapped to none).

    A path belongs to a service when one of its directories is the service's
    key (`backend/core-service/app.py` → `core-service`), or the key without
    the project's own prefix (`apps/api/…` → `logislot-api` in LogiSlot). The
    file name itself is never matched: `api.py` is not the `api` service.
    """
    keys = list(dict.fromkeys(service_keys))
    prefix = f"{_normalise(project_key)}-"
    aliases: dict[str, str] = {}
    for key in keys:
        norm = _normalise(key)
        aliases.setdefault(norm, key)
        if norm.startswith(prefix) and len(norm) > len(prefix):
            aliases.setdefault(norm[len(prefix) :], key)
    found: dict[str, None] = {}
    unmapped = 0
    for path in paths:
        directories = [_normalise(part) for part in path.split("/")[:-1] if part]
        hit = next((aliases[d] for d in directories if d in aliases), None)
        if hit is None:
            unmapped += 1
        else:
            found.setdefault(hit, None)
    return list(found), unmapped
