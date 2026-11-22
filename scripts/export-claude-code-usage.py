#!/usr/bin/env python3
# ruff: noqa: UP017, S603, S607 - runs on a developer's own Python (3.9+, no
# datetime.UTC) and calls git with fixed arguments.
"""Export your past Claude Code usage for Tracesis's Development map.

    python3 scripts/export-claude-code-usage.py --login <github-login> > claude-code-usage.json

Claude Code's telemetry only reports sessions started after it was switched
on. This reads the history Claude Code keeps on this machine
(~/.claude/projects/**/*.jsonl, subagents included) and writes, per day and
repository, what Tracesis shows: cost, tokens, requests, prompts, sessions,
models and tool names. Only work in clones of the Duosis-Developer-Team
organisation counts (by each session's working directory and its `origin`).

What leaves this machine: those counts, the repository names, your login and
your Claude account's uuid (from ~/.claude.json, so the import lands on the
same person as your live telemetry). What never does: prompts, answers,
tool inputs or outputs, file contents, paths, your e-mail.

By default it stops where your telemetry starts (the moment
connect-claude-code.sh switched it on), so nothing is counted twice; pass
--until to choose. Cost is Claude Code's own API-price estimate.

Standard library only, Python 3.9+.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

FORMAT = "tracesis.claude-code-usage.v1"
ORG = "duosis-developer-team"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# $ per million tokens: input, output, cache read, cache write 5m, cache write 1h
PRICES = {
    "claude-fable-5-2": (8, 40, 0.20, 10, 16),
    "claude-opus-5-5": (4, 20, 0.20, 5, 8),
    "claude-opus-5": (5, 25, 0.50, 6.25, 10),
    "claude-sonnet-5-5": (2, 10, 0.10, 2.5, 4),
    "claude-sonnet-5": (2, 10, 0.20, 2.5, 4),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),
}
_ORIGIN = re.compile(r"github\.com[/:]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def cost_of(model, usage):
    """Dollar cost of one API response, or None when the model has no price here."""
    price = PRICES.get(model)
    if price is None:
        return None
    created = usage.get("cache_creation") or {}
    w1h = created.get("ephemeral_1h_input_tokens")
    w5m = created.get("ephemeral_5m_input_tokens")
    written = usage.get("cache_creation_input_tokens") or 0
    if w1h is None and w5m is None:
        w5m, w1h = written, 0
    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    read = usage.get("cache_read_input_tokens") or 0
    # Haiku 5.5 bills prompts over 100K tokens at the higher tier.
    scale = 5 if model == "claude-haiku-5-5" and inp + read + written > 100_000 else 1
    return (
        scale
        * (
            inp * price[0]
            + out * price[1]
            + read * price[2]
            + (w5m or 0) * price[3]
            + (w1h or 0) * price[4]
        )
        / 1e6
    )


class Repos:
    """cwd -> 'owner/name' of its origin when that is an org repository, else None."""

    def __init__(self):
        self._seen = {}

    def of(self, cwd):
        if not cwd:
            return None
        if cwd in self._seen:
            return self._seen[cwd]
        found = None
        path = cwd
        # A worktree or a checkout that has since moved: try the nearest
        # directory that still exists.
        while path and not os.path.isdir(path):
            parent = os.path.dirname(path)
            if parent == path:
                path = ""
                break
            path = parent
        if path:
            try:
                url = subprocess.run(
                    ["git", "-C", path, "remote", "get-url", "origin"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                url = ""
            match = _ORIGIN.search(url)
            if match and match.group(1).lower() == ORG:
                found = f"{match.group(1)}/{match.group(2)}".lower()
        self._seen[cwd] = found
        return found


def telemetry_start(home):
    """When connect-claude-code.sh switched telemetry on, from its settings backup, or None."""
    settings = os.path.join(home, ".claude", "settings.json")
    try:
        with open(settings, encoding="utf-8") as handle:
            env = json.load(handle).get("env") or {}
    except (OSError, ValueError):
        return None
    if str(env.get("CLAUDE_CODE_ENABLE_TELEMETRY")) != "1":
        return None
    for backup in sorted(glob.glob(settings + ".bak-*")):
        stamp = backup.rsplit(".bak-", 1)[1]
        try:
            with open(backup, encoding="utf-8") as handle:
                before = json.load(handle).get("env") or {}
        except (OSError, ValueError):
            continue
        if str(before.get("CLAUDE_CODE_ENABLE_TELEMETRY")) == "1":
            continue
        try:
            # The script names backups in local time.
            return datetime.strptime(stamp, "%Y%m%d%H%M%S").astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def account_uuid(home):
    try:
        with open(os.path.join(home, ".claude.json"), encoding="utf-8") as handle:
            account = (json.load(handle).get("oauthAccount") or {}).get("accountUuid")
    except (OSError, ValueError):
        return None
    return (
        account
        if isinstance(account, str) and re.fullmatch(r"[0-9a-fA-F-]{8,64}", account)
        else None
    )


def parse_time(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def session_homes(paths):
    """sessionId -> the directory the session was started in.

    A session belongs to the repository it was started in, as Claude Code's
    own telemetry counts it: the shell may wander off to a scratch directory
    halfway through, and that work is still that session's.
    """
    homes = {}
    for path in paths:
        if os.sep + "subagents" + os.sep in path:
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(o, dict) and o.get("cwd") and o.get("sessionId"):
                        homes.setdefault(str(o["sessionId"]), str(o["cwd"]))
                        break
        except OSError:
            continue
    return homes


def read(home, since, until):
    repos = Repos()
    messages = {}  # (message id, request id) -> record
    prompts = {}  # uuid -> record
    names = {}  # tool_use id -> tool name
    results = {}  # tool_use id -> record
    paths = glob.glob(os.path.join(home, ".claude", "projects", "**", "*.jsonl"), recursive=True)
    homes = session_homes(paths)
    for path in paths:
        try:
            handle = open(path, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(o, dict):
                    continue
                stamp = o.get("timestamp")
                if not isinstance(stamp, str):
                    continue
                try:
                    moment = parse_time(stamp)
                except ValueError:
                    continue
                if moment < since or moment >= until:
                    continue
                sid = str(o.get("sessionId") or "")
                repo = repos.of(homes.get(sid) or o.get("cwd"))
                if repo is None:
                    continue
                m = o.get("message") if isinstance(o.get("message"), dict) else {}
                day = moment.date().isoformat()
                # "<synthetic>" replies are written by Claude Code itself, not by an API call.
                if (
                    o.get("type") == "assistant"
                    and isinstance(m.get("usage"), dict)
                    and not str(m.get("model") or "").startswith("<")
                ):
                    key = (m.get("id"), o.get("requestId"))
                    record = messages.get(key)
                    if record is None:
                        messages[key] = {
                            "day": day,
                            "sid": sid,
                            "repo": repo,
                            "model": str(m.get("model") or ""),
                            "usage": m["usage"],
                        }
                    elif (m["usage"].get("output_tokens") or 0) > (
                        record["usage"].get("output_tokens") or 0
                    ):
                        record["usage"] = m["usage"]
                    for block in m.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            names[block.get("id")] = str(block.get("name") or "")
                elif (
                    o.get("type") == "user"
                    and not o.get("isMeta")
                    and not o.get("isCompactSummary")
                ):
                    content = m.get("content")
                    blocks = content if isinstance(content, list) else []
                    tool_results = [
                        b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"
                    ]
                    for b in tool_results:
                        results[b.get("tool_use_id")] = {
                            "day": day,
                            "repo": repo,
                            "error": bool(b.get("is_error")),
                        }
                    if not tool_results and not o.get("isSidechain") and o.get("promptId"):
                        prompts.setdefault(o.get("uuid"), {"day": day, "sid": sid, "repo": repo})
    tools = [dict(r, name=names[t]) for t, r in results.items() if names.get(t)]
    return list(messages.values()), list(prompts.values()), tools


def aggregate(messages, prompts, tools):
    days = defaultdict(lambda: defaultdict(float))
    sessions = defaultdict(set)
    breakdown = defaultdict(lambda: defaultdict(float))
    unpriced = set()
    for r in messages:
        u = r["usage"]
        k = (r["day"], r["repo"])
        cost = cost_of(r["model"], u)
        if cost is None:
            unpriced.add(r["model"] or "unknown")
            cost = 0.0
        g = days[k]
        g["api_requests"] += 1
        g["cost_usd"] += cost
        g["input_tokens"] += u.get("input_tokens") or 0
        g["output_tokens"] += u.get("output_tokens") or 0
        g["cache_read_tokens"] += u.get("cache_read_input_tokens") or 0
        g["cache_creation_tokens"] += u.get("cache_creation_input_tokens") or 0
        sessions[k].add(r["sid"])
        if r["model"]:
            b = breakdown[(r["day"], r["repo"], "model", r["model"][:200])]
            b["calls"] += 1
            b["cost_usd"] += cost
            b["tokens"] += sum(
                u.get(f) or 0
                for f in (
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
    for p in prompts:
        k = (p["day"], p["repo"])
        days[k]["prompts"] += 1
        sessions[k].add(p["sid"])
    for t in tools:
        kind = "mcp" if t["name"].startswith("mcp__") else "tool"
        b = breakdown[(t["day"], t["repo"], kind, t["name"][:200])]
        b["calls"] += 1
        b["failures"] += 1 if t["error"] else 0
        if t["name"] in EDIT_TOOLS and not t["error"]:
            days[(t["day"], t["repo"])]["edits_accepted"] += 1
    for k, s in sessions.items():
        days[k]["sessions"] = len(s - {""})
    counts = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "api_requests",
        "prompts",
        "sessions",
        "edits_accepted",
    )
    day_rows = [
        {
            "day": day,
            "repository": repo,
            "cost_usd": round(g["cost_usd"], 6),
            **{c: int(g[c]) for c in counts},
        }
        for (day, repo), g in sorted(days.items())
    ]
    breakdown_rows = [
        {
            "day": day,
            "repository": repo,
            "kind": kind,
            "name": name,
            "calls": int(b["calls"]),
            "failures": int(b["failures"]),
            "cost_usd": round(b["cost_usd"], 6),
            "tokens": int(b["tokens"]),
        }
        for (day, repo, kind, name), b in sorted(breakdown.items())
    ]
    return day_rows, breakdown_rows, sorted(unpriced)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export your Claude Code usage for Tracesis.")
    parser.add_argument("--login", required=True, help="your GitHub login")
    parser.add_argument("--days", type=int, default=30, help="how many days back (default 30)")
    parser.add_argument(
        "--until", help="ISO time to stop at (default: when your telemetry started, else now)"
    )
    parser.add_argument("--home", default=os.path.expanduser("~"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not _LOGIN.match(args.login):
        parser.error("--login is a GitHub login: letters, digits and dashes")
    if not 1 <= args.days <= 90:
        parser.error("--days is between 1 and 90")
    now = datetime.now(timezone.utc)
    if args.until:
        until, why = parse_time(args.until), "--until"
    else:
        started = telemetry_start(args.home)
        until, why = (started, "telemetry switched on") if started else (now, "now")
    since = datetime.combine(
        (until - timedelta(days=args.days)).date(), datetime.min.time(), tzinfo=timezone.utc
    )
    messages, prompts, tools = read(args.home, since, until)
    day_rows, breakdown_rows, unpriced = aggregate(messages, prompts, tools)
    repos = sorted({r["repository"] for r in day_rows})
    totals = {
        "repositories": len(repos),
        "days": len({r["day"] for r in day_rows}),
        "cost_usd": round(sum(r["cost_usd"] for r in day_rows), 2),
        "api_requests": sum(r["api_requests"] for r in day_rows),
        "prompts": sum(r["prompts"] for r in day_rows),
        "output_tokens": sum(r["output_tokens"] for r in day_rows),
    }
    out = {
        "format": FORMAT,
        "login": args.login,
        "account_uuid": account_uuid(args.home),
        "generated_at": now.isoformat(timespec="seconds"),
        "since": since.isoformat(timespec="seconds"),
        "until": until.isoformat(timespec="seconds"),
        "days": day_rows,
        "breakdown": breakdown_rows,
        "unpriced_models": unpriced,
        "totals": totals,
    }
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")
    print(
        f"{args.login}: {totals['repositories']} repositories ({', '.join(repos) or 'none'}), "
        f"{totals['days']} days, ${totals['cost_usd']:.2f}, {totals['api_requests']} requests, "
        f"until {until.isoformat(timespec='minutes')} ({why})"
        + (f"; no price for {', '.join(unpriced)}" if unpriced else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
