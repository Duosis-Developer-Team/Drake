"""Import a developer's own Claude Code history into the Development map.

Claude Code's telemetry only reports sessions started after it was switched
on, and the store keeps a week. A developer who wants their earlier work on
the map runs `scripts/export-claude-code-usage.py` on their own machine
(counts only: tokens, cost, models, tool names — never prompt or answer
text) and hands the JSON to an operator, who runs, inside the API pod:

    python -m drake_api.dev_activity.usage_import usage.json [--identity UUID] [--dry-run]

Each repository is resolved like the collector resolves a live session
(catalog project, watched organisation, a name GitHub has since moved), so a
clone that still says `drake` lands on Tracesis. Rows are kept under the
identity `backfill:<Claude account uuid>` with the developer's login as the
person hint; the harvest never replaces them. Re-importing the same file
replaces the same rows: it never double counts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from drake_api.dev_activity.collector import (
    BACKFILL_PREFIX,
    Renames,
    Repository,
    Resolver,
    _resolver,
)

FORMAT = "tracesis.claude-code-usage.v1"
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
ACCOUNT = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{7,63}$")
# A GitHub login (owner) and a repository name; "." and ".." are no name.
REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}/(?!\.{1,2}$)[a-z0-9._-]{1,100}$")
KINDS = {"model", "tool", "mcp"}
DAY_COUNTS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "api_requests",
    "prompts",
    "sessions",
    "edits_accepted",
)
MAX_ROWS = 20000


class ImportRefusedError(ValueError):
    """The file is not one this importer accepts; nothing was written."""


@dataclass
class Plan:
    login: str
    identity: str
    days: list[dict[str, Any]] = field(default_factory=list)
    breakdown: list[dict[str, Any]] = field(default_factory=list)


def _count(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ImportRefusedError(f"{what} must be a whole number of at least 0")
    return value


def _cost(value: Any, what: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ImportRefusedError(f"{what} must be a number")
    try:
        cost = Decimal(str(value))
    except InvalidOperation as error:
        raise ImportRefusedError(f"{what} must be a number") from error
    if not cost.is_finite() or cost < 0 or cost > Decimal("100000000"):
        raise ImportRefusedError(f"{what} is out of range")
    return cost.quantize(Decimal("0.000001"))


def _day(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ImportRefusedError("a day is not an ISO date") from error


def _repository(value: Any) -> str:
    name = str(value or "").strip().lower()
    if not REPOSITORY.match(name):
        raise ImportRefusedError("a repository is not owner/name")
    return name


def parse(raw: str, identity: str | None = None) -> Plan:
    """Validate an export. Raises ImportRefusedError; never partially accepts."""
    if "@" in raw:
        # An address has no business in this file, and the tables refuse one.
        raise ImportRefusedError("the file contains an '@'; usage exports never carry addresses")
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ImportRefusedError("the file is not JSON") from error
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ImportRefusedError(f"not a {FORMAT} file")
    login = str(data.get("login") or "")
    if not LOGIN.match(login):
        raise ImportRefusedError("login must be a GitHub login")
    account = (identity or str(data.get("account_uuid") or "")).strip()
    if not ACCOUNT.match(account):
        raise ImportRefusedError("no Claude account uuid: pass --identity")
    days, breakdown = data.get("days"), data.get("breakdown") or []
    if not isinstance(days, list) or not isinstance(breakdown, list):
        raise ImportRefusedError("days and breakdown must be lists")
    if len(days) + len(breakdown) > MAX_ROWS:
        raise ImportRefusedError("the file is larger than one developer's history")
    plan = Plan(login=login, identity=f"{BACKFILL_PREFIX}{account.lower()}")
    seen: set[tuple[date, str]] = set()
    for item in days:
        if not isinstance(item, dict):
            raise ImportRefusedError("a day is not an object")
        day, repository = _day(item.get("day")), _repository(item.get("repository"))
        row: dict[str, Any] = {
            "day": day,
            "repository": repository,
            "cost_usd": _cost(item.get("cost_usd", 0), "cost_usd"),
            **{k: _count(item.get(k, 0), k) for k in DAY_COUNTS},
        }
        key = (day, repository)
        if key in seen:
            raise ImportRefusedError("a day and repository appear twice")
        seen.add(key)
        plan.days.append(row)
    seen_b: set[tuple[date, str, str, str]] = set()
    for item in breakdown:
        if not isinstance(item, dict):
            raise ImportRefusedError("a breakdown row is not an object")
        kind = str(item.get("kind") or "")
        name = str(item.get("name") or "").strip()
        if kind not in KINDS or not (1 <= len(name) <= 200):
            raise ImportRefusedError("a breakdown row has an unknown kind or no name")
        day, repository = _day(item.get("day")), _repository(item.get("repository"))
        row = {
            "day": day,
            "repository": repository,
            "kind": kind,
            "name": name,
            "calls": _count(item.get("calls", 0), "calls"),
            "failures": _count(item.get("failures", 0), "failures"),
            "cost_usd": _cost(item.get("cost_usd", 0), "cost_usd"),
            "tokens": _count(item.get("tokens", 0), "tokens"),
        }
        key_b = (day, repository, kind, name)
        if key_b in seen_b:
            raise ImportRefusedError("a breakdown row appears twice")
        seen_b.add(key_b)
        plan.breakdown.append(row)
    return plan


@dataclass
class Outcome:
    written_days: int = 0
    written_breakdown: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    totals: dict[str, dict[str, Any]] = field(default_factory=dict)
    landed: dict[str, str] = field(default_factory=dict)


class _DryRunError(Exception):
    """Unwinds the transaction of a dry run, carrying what it would have written."""

    def __init__(self, outcome: Outcome) -> None:
        super().__init__("dry run")
        self.outcome = outcome


def _where(resolver: Resolver, full: str) -> Repository | None:
    owner, _, name = full.partition("/")
    return resolver(owner=owner, name=name)


async def apply(
    engine: AsyncEngine,
    plan: Plan,
    *,
    orgs: list[str] | tuple[str, ...] = (),
    renames: Renames | None = None,
    dry_run: bool = False,
) -> Outcome:
    """Write the plan (or, dry, write it and roll it back). One transaction."""
    try:
        return await _apply(engine, plan, orgs=orgs, renames=renames, dry_run=dry_run)
    except _DryRunError as dry:
        return dry.outcome


async def _apply(
    engine: AsyncEngine,
    plan: Plan,
    *,
    orgs: list[str] | tuple[str, ...],
    renames: Renames | None,
    dry_run: bool,
) -> Outcome:
    resolver = await _resolver(engine, orgs)
    names = {r["repository"] for r in plan.days} | {r["repository"] for r in plan.breakdown}
    if renames is not None:
        unknown = set()
        for full in names:
            owner, _, name = full.partition("/")
            hit = resolver.unknown(owner=owner, name=name)
            if hit:
                unknown.add(hit)
        await renames.teach(engine, resolver, unknown)
    where = {full: _where(resolver, full) for full in names}
    outcome = Outcome()
    totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"days": 0, "cost_usd": Decimal(0), "api_requests": 0, "prompts": 0}
    )
    for full, repository in where.items():
        if repository is not None:
            outcome.landed[full] = repository.full_name
    async with engine.begin() as connection:
        days_in_file = sorted({r["day"] for r in plan.days} | {r["day"] for r in plan.breakdown})
        if days_in_file:
            # Re-importing replaces this person's imported rows on these days.
            for table in ("dev_agent_days", "dev_agent_breakdown"):
                await connection.execute(
                    text(
                        f"DELETE FROM {table} WHERE identity = :i AND tool = 'claude_code' "  # noqa: S608 - fixed table names
                        "AND day = ANY(:days)"
                    ),
                    {"i": plan.identity, "days": days_in_file},
                )
        merged: dict[tuple[date, str], dict[str, Any]] = {}
        for row in plan.days:
            repository = where[row["repository"]]
            if repository is None:
                outcome.skipped[row["repository"]] = outcome.skipped.get(row["repository"], 0) + 1
                continue
            key = (row["day"], repository.full_name)
            cur = merged.get(key)
            if cur is None:
                merged[key] = {**row, "repository": repository.full_name, "_r": repository}
            else:
                # Two names (old and new) of one repository on one day.
                cur["cost_usd"] += row["cost_usd"]
                for k in DAY_COUNTS:
                    cur[k] = cur[k] + row[k] if k != "sessions" else max(cur[k], row[k])
        for (day, full), row in sorted(merged.items()):
            repository = row["_r"]
            await connection.execute(
                text(
                    """
                    INSERT INTO dev_agent_days (day, repository, repository_external_id,
                        project_id, identity, tool, person_hint, cost_usd, input_tokens,
                        output_tokens, cache_read_tokens, cache_creation_tokens, api_requests,
                        api_errors, prompts, sessions, edits_accepted, edits_rejected, updated_at)
                    VALUES (:day, :r, :e, :p, :i, 'claude_code', :hint, :cost_usd,
                        :input_tokens, :output_tokens, :cache_read_tokens,
                        :cache_creation_tokens, :api_requests, 0, :prompts, :sessions,
                        :edits_accepted, 0, now())
                    ON CONFLICT (day, repository, identity, tool) DO UPDATE SET
                        repository_external_id = EXCLUDED.repository_external_id,
                        project_id = EXCLUDED.project_id, person_hint = EXCLUDED.person_hint,
                        cost_usd = EXCLUDED.cost_usd, input_tokens = EXCLUDED.input_tokens,
                        output_tokens = EXCLUDED.output_tokens,
                        cache_read_tokens = EXCLUDED.cache_read_tokens,
                        cache_creation_tokens = EXCLUDED.cache_creation_tokens,
                        api_requests = EXCLUDED.api_requests, prompts = EXCLUDED.prompts,
                        sessions = EXCLUDED.sessions, edits_accepted = EXCLUDED.edits_accepted,
                        updated_at = now()
                    """
                ),
                {
                    "day": day,
                    "r": full,
                    "e": repository.external_id,
                    "p": repository.project_id,
                    "i": plan.identity,
                    "hint": plan.login,
                    "cost_usd": row["cost_usd"],
                    **{k: row[k] for k in DAY_COUNTS},
                },
            )
            outcome.written_days += 1
            t = totals[full]
            t["days"] += 1
            t["cost_usd"] += row["cost_usd"]
            t["api_requests"] += row["api_requests"]
            t["prompts"] += row["prompts"]
        merged_b: dict[tuple[date, str, str, str], dict[str, Any]] = {}
        for row in plan.breakdown:
            repository = where[row["repository"]]
            if repository is None:
                continue
            key_b = (row["day"], repository.full_name, row["kind"], row["name"])
            cur = merged_b.get(key_b)
            if cur is None:
                merged_b[key_b] = {**row, "_r": repository}
            else:
                for k in ("calls", "failures", "tokens", "cost_usd"):
                    cur[k] += row[k]
        for (day, full, kind, name), row in sorted(merged_b.items()):
            repository = row["_r"]
            await connection.execute(
                text(
                    """
                    INSERT INTO dev_agent_breakdown (day, repository, repository_external_id,
                        project_id, identity, tool, kind, name, calls, failures, cost_usd,
                        tokens, duration_ms)
                    VALUES (:day, :r, :e, :p, :i, 'claude_code', :kind, :name, :calls,
                        :failures, :cost_usd, :tokens, 0)
                    ON CONFLICT (day, repository, identity, tool, kind, name) DO UPDATE SET
                        repository_external_id = EXCLUDED.repository_external_id,
                        project_id = EXCLUDED.project_id, calls = EXCLUDED.calls,
                        failures = EXCLUDED.failures, cost_usd = EXCLUDED.cost_usd,
                        tokens = EXCLUDED.tokens
                    """
                ),
                {
                    "day": day,
                    "r": full,
                    "e": repository.external_id,
                    "p": repository.project_id,
                    "i": plan.identity,
                    "kind": kind,
                    "name": name,
                    "calls": row["calls"],
                    "failures": row["failures"],
                    "cost_usd": row["cost_usd"],
                    "tokens": row["tokens"],
                },
            )
            outcome.written_breakdown += 1
        outcome.totals = dict(totals)
        if dry_run:
            raise _DryRunError(outcome)
    return outcome


def _report(plan: Plan, outcome: Outcome, dry_run: bool) -> str:
    lines = [
        f"{'DRY RUN — nothing written. ' if dry_run else ''}{plan.login} "
        f"({plan.identity}): {outcome.written_days} day rows, "
        f"{outcome.written_breakdown} breakdown rows"
    ]
    for old, new in sorted(outcome.landed.items()):
        if old != new:
            lines.append(f"  {old} -> {new}")
    for full, t in sorted(outcome.totals.items()):
        lines.append(
            f"  {full}: {t['days']} days, ${t['cost_usd']:.2f}, "
            f"{t['api_requests']} requests, {t['prompts']} prompts"
        )
    for full, n in sorted(outcome.skipped.items()):
        lines.append(f"  skipped {full}: {n} days (not a catalog or watched repository)")
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> int:
    from drake_api.db import get_engine
    from drake_api.settings import get_settings

    settings = get_settings()
    with open(args.file, encoding="utf-8") as handle:
        raw = handle.read(32 * 1024 * 1024)
    try:
        plan = parse(raw, args.identity)
    except ImportRefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    renames = None
    if settings.github_app_enabled:
        try:
            from drake_api.github_app.auth import GitHubAppAuth
            from drake_api.github_app.client import GitHubClient

            renames = Renames(GitHubClient(settings, GitHubAppAuth(settings)))
        except Exception as error:  # a broken App only costs the rename lookups
            print(f"note: renamed repositories not looked up ({type(error).__name__})")
    outcome = await apply(
        get_engine(settings),
        plan,
        orgs=tuple(settings.dev_activity_orgs),
        renames=renames,
        dry_run=args.dry_run,
    )
    print(_report(plan, outcome, args.dry_run))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m drake_api.dev_activity.usage_import",
        description="Import a developer's exported Claude Code usage into the Development map.",
    )
    parser.add_argument("file", help="the JSON written by scripts/export-claude-code-usage.py")
    parser.add_argument(
        "--identity",
        help="the developer's Claude account uuid (default: the file's account_uuid)",
    )
    parser.add_argument("--dry-run", action="store_true", help="resolve and report; write nothing")
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
