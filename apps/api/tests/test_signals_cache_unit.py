"""The error-signal read every overview, log and trace screen opens with is
answered from a short cache keyed by exactly what the caller may read."""

import asyncio
import json
import time
from typing import Any

from drake_api.observability import signals_cache
from drake_api.observability.signals_cache import (
    FRESH_SECONDS,
    STALE_SECONDS,
    cache_key,
    cached_section,
)
from fakeredis import aioredis as fake_aioredis

BINDINGS = [{"id": "b1", "namespace": "hermes-test", "workload_name": "web"}]


class _Store:
    def __init__(self, state: str = "ok") -> None:
        self.calls = 0
        self.state = state

    async def __call__(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        self.calls += 1
        return {"state": self.state}, {"b1": {"lines": self.calls}}


async def _age(redis: Any, seconds: float) -> None:
    key = cache_key("logs", "1h", BINDINGS)
    entry = json.loads(await redis.get(key))
    entry["stored_at"] = time.time() - seconds
    await redis.set(key, json.dumps(entry))


async def test_a_fresh_answer_is_served_without_the_store() -> None:
    redis, store = fake_aioredis.FakeRedis(), _Store()
    first = await cached_section(redis, store, kind="logs", range_key="1h", bindings=BINDINGS)
    second = await cached_section(redis, store, kind="logs", range_key="1h", bindings=BINDINGS)
    assert store.calls == 1
    assert first[1] == second[1] == {"b1": {"lines": 1}}


async def test_a_stale_answer_is_served_at_once_and_rebuilt_behind_it() -> None:
    redis, store = fake_aioredis.FakeRedis(), _Store()
    await cached_section(redis, store, kind="logs", range_key="1h", bindings=BINDINGS)
    await _age(redis, FRESH_SECONDS + 1)

    served = await cached_section(redis, store, kind="logs", range_key="1h", bindings=BINDINGS)
    assert served[1] == {"b1": {"lines": 1}}  # the last answer, at once
    await asyncio.gather(*signals_cache._REFRESHING)
    assert store.calls == 2  # rebuilt behind it


async def test_an_old_answer_is_rebuilt_inline() -> None:
    redis, store = fake_aioredis.FakeRedis(), _Store()
    await cached_section(redis, store, kind="logs", range_key="1h", bindings=BINDINGS)
    await _age(redis, STALE_SECONDS + 1)
    served = await cached_section(redis, store, kind="logs", range_key="1h", bindings=BINDINGS)
    assert served[1] == {"b1": {"lines": 2}}


async def test_a_store_outage_is_never_served_from_cache_as_data() -> None:
    redis, store = fake_aioredis.FakeRedis(), _Store(state="unavailable")
    await cached_section(redis, store, kind="logs", range_key="1h", bindings=BINDINGS)
    await cached_section(redis, store, kind="logs", range_key="1h", bindings=BINDINGS)
    assert store.calls == 2


def test_callers_who_may_read_different_bindings_never_share_an_entry() -> None:
    wider = [*BINDINGS, {"id": "b2", "namespace": "logislot-prod", "workload_name": "api"}]
    assert cache_key("logs", "1h", BINDINGS) != cache_key("logs", "1h", wider)
    assert cache_key("logs", "1h", BINDINGS) != cache_key("traces", "1h", BINDINGS)
    assert cache_key("logs", "1h", BINDINGS) != cache_key("logs", "6h", BINDINGS)
    # The order a caller asks in does not matter.
    assert cache_key("logs", "1h", wider) == cache_key("logs", "1h", list(reversed(wider)))
