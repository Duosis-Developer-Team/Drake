"""Redis dependency check.

Redis is a cache/queue dependency, never an authoritative store. The readiness
probe is bounded and reports availability without raising.
"""

import asyncio

import redis.asyncio as aioredis

from drake_api.settings import Settings


async def check_redis(settings: Settings) -> bool:
    """Bounded availability probe. Returns False instead of raising."""
    client: aioredis.Redis = aioredis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.ready_check_timeout_seconds,
        socket_timeout=settings.ready_check_timeout_seconds,
    )
    try:
        async with asyncio.timeout(settings.ready_check_timeout_seconds):
            await client.ping()
        return True
    except Exception:
        return False
    finally:
        await client.aclose()
