"""A project logo is never a GitHub round trip on a screen's first open:
past its fresh window it is served at once and read again behind it."""

import asyncio
import json
import time
from typing import Any

from drake_api.catalog import logos
from drake_api.catalog.logos import (
    CACHE_SECONDS,
    KEEP_SECONDS,
    Logo,
    LogoRepository,
    cache_key,
    project_logo,
)
from fakeredis import aioredis as fake_aioredis

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
NEW_PNG = b"\x89PNG\r\n\x1a\n" + b"1" * 16
REPO = LogoRepository(1, 2, "Duosis-Developer-Team", "Hermes", "main")


class _Reader:
    def __init__(self, content: bytes = PNG, fail: bool = False) -> None:
        self.calls = 0
        self.content = content
        self.fail = fail

    async def __call__(self, client: Any, repository: Any, theme: str) -> Logo | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("github down")
        return Logo(self.content, "image/png")


async def _age(redis: Any, seconds: float) -> None:
    key = cache_key("p1", "dark")
    entry = json.loads(await redis.get(key))
    entry["stored_at"] = time.time() - seconds
    await redis.set(key, json.dumps(entry))


async def test_a_logo_is_kept_well_past_its_fresh_window(monkeypatch: Any) -> None:
    redis, reader = fake_aioredis.FakeRedis(), _Reader()
    monkeypatch.setattr(logos, "read_logo", reader)
    await project_logo(None, redis, "p1", REPO, "dark")
    ttl = await redis.ttl(cache_key("p1", "dark"))
    assert CACHE_SECONDS < ttl <= KEEP_SECONDS


async def test_a_fresh_logo_costs_github_nothing(monkeypatch: Any) -> None:
    redis, reader = fake_aioredis.FakeRedis(), _Reader()
    monkeypatch.setattr(logos, "read_logo", reader)
    await project_logo(None, redis, "p1", REPO, "dark")
    logo = await project_logo(None, redis, "p1", REPO, "dark")
    assert logo is not None and logo.content == PNG
    assert reader.calls == 1


async def test_a_stale_logo_is_served_at_once_and_read_again_behind_it(monkeypatch: Any) -> None:
    redis, reader = fake_aioredis.FakeRedis(), _Reader()
    monkeypatch.setattr(logos, "read_logo", reader)
    await project_logo(None, redis, "p1", REPO, "dark")
    await _age(redis, CACHE_SECONDS + 1)
    reader.content = NEW_PNG

    served = await project_logo(None, redis, "p1", REPO, "dark")
    assert served is not None and served.content == PNG  # the kept one, at once
    await asyncio.gather(*logos._REFRESHING)
    assert reader.calls == 2
    again = await project_logo(None, redis, "p1", REPO, "dark")
    assert again is not None and again.content == NEW_PNG


async def test_a_github_outage_keeps_serving_the_kept_logo(monkeypatch: Any) -> None:
    redis, reader = fake_aioredis.FakeRedis(), _Reader()
    monkeypatch.setattr(logos, "read_logo", reader)
    await project_logo(None, redis, "p1", REPO, "dark")
    await _age(redis, CACHE_SECONDS + 1)
    reader.fail = True

    served = await project_logo(None, redis, "p1", REPO, "dark")
    await asyncio.gather(*logos._REFRESHING)
    assert served is not None and served.content == PNG
    still = await project_logo(None, redis, "p1", REPO, "dark")
    assert still is not None and still.content == PNG


async def test_one_reader_at_a_time_refreshes(monkeypatch: Any) -> None:
    redis, reader = fake_aioredis.FakeRedis(), _Reader()
    monkeypatch.setattr(logos, "read_logo", reader)
    await project_logo(None, redis, "p1", REPO, "dark")
    await _age(redis, CACHE_SECONDS + 1)
    await asyncio.gather(*(project_logo(None, redis, "p1", REPO, "dark") for _ in range(5)))
    await asyncio.gather(*logos._REFRESHING)
    assert reader.calls == 2


async def test_an_entry_from_before_this_change_is_still_served(monkeypatch: Any) -> None:
    redis, reader = fake_aioredis.FakeRedis(), _Reader()
    monkeypatch.setattr(logos, "read_logo", reader)
    old = {"media_type": "image/png", "b64": "iVBORw0KGgo="}
    await redis.set(cache_key("p1", "dark"), json.dumps(old), ex=60)
    logo = await project_logo(None, redis, "p1", REPO, "dark")
    assert logo is not None and logo.media_type == "image/png"
    assert reader.calls == 0
