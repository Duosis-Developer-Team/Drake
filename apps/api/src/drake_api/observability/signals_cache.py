"""A short cache for the error-signal read that every overview, log and
trace screen opens with (`GET /v1/service-health/signals`).

The read counts log lines and requests for every bound service in
ClickHouse; with a busy store it took up to 2.5 s on each open. Its answer
depends only on which bindings the caller may read and on the range, so it
is cached under exactly that: two callers who may read the same bindings
share an entry, and a caller who may read fewer never reaches a wider one.

- younger than FRESH_SECONDS: served as is;
- younger than STALE_SECONDS: served at once, rebuilt behind it;
- older, missing, or the store unreachable: built inline, as before.

Only a store answer of `ok` is kept; "store down" is never served from
cache as if it were data.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("drake_api.observability.signals_cache")

FRESH_SECONDS = 30
STALE_SECONDS = 600
_REFRESHING: set[asyncio.Task[None]] = set()

Section = tuple[dict[str, Any], dict[str, dict[str, Any]]]


def cache_key(kind: str, range_key: str, bindings: list[dict[str, Any]]) -> str:
    """The bindings a caller may read, exactly as the statement sees them."""
    shape = sorted(json.dumps(b, sort_keys=True, default=str) for b in bindings)
    digest = hashlib.sha256(json.dumps([kind, range_key, shape]).encode("utf-8")).hexdigest()
    return f"signals:v1:{kind}:{range_key}:{digest}"


async def _store(redis: aioredis.Redis, key: str, section: Section, stored_at: float) -> None:
    if section[0].get("state") != "ok":
        return
    with contextlib.suppress(Exception):
        await redis.set(
            key,
            json.dumps({"stored_at": stored_at, "section": section[0], "per": section[1]}),
            ex=STALE_SECONDS,
        )


async def cached_section(
    redis: aioredis.Redis,
    compute: Callable[[], Awaitable[Section]],
    *,
    kind: str,
    range_key: str,
    bindings: list[dict[str, Any]],
    refresh: bool = False,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], float]:
    """(section, per-binding numbers, when they were read)."""
    key = cache_key(kind, range_key, bindings)
    if not refresh:
        cached = None
        with contextlib.suppress(Exception):
            raw = await redis.get(key)
            cached = json.loads(raw) if raw else None
        if cached is not None:
            age = time.time() - float(cached["stored_at"])
            if age < FRESH_SECONDS:
                return cached["section"], cached["per"], float(cached["stored_at"])
            if age < STALE_SECONDS:
                await _refresh_behind(redis, key, compute)
                return cached["section"], cached["per"], float(cached["stored_at"])
    stored_at = time.time()
    section = await compute()
    await _store(redis, key, section, stored_at)
    return section[0], section[1], stored_at


async def _refresh_behind(
    redis: aioredis.Redis, key: str, compute: Callable[[], Awaitable[Section]]
) -> None:
    try:
        acquired = bool(await redis.set(f"{key}:refresh", "1", nx=True, ex=30))
    except Exception:
        return
    if not acquired:
        return

    async def run() -> None:
        try:
            stored_at = time.time()
            await _store(redis, key, await compute(), stored_at)
        except Exception:
            logger.warning("signals: background refresh failed")
        finally:
            with contextlib.suppress(Exception):
                await redis.delete(f"{key}:refresh")

    task = asyncio.create_task(run(), name="signals-refresh")
    _REFRESHING.add(task)
    task.add_done_callback(_REFRESHING.discard)
