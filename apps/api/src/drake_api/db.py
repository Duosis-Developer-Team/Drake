"""PostgreSQL connection foundation.

The engine is created lazily and never logs its URL. Readiness checks run a
bounded ``SELECT 1`` and report availability without raising.
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from drake_api.settings import Settings


class Base(DeclarativeBase):
    """Declarative base for all Drake API models."""


_engines: dict[str, AsyncEngine] = {}


def get_engine(settings: Settings) -> AsyncEngine:
    engine = _engines.get(settings.database_url)
    if engine is None:
        engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            hide_parameters=True,
        )
        _engines[settings.database_url] = engine
    return engine


async def check_database(settings: Settings) -> bool:
    """Bounded availability probe. Returns False instead of raising."""
    engine = get_engine(settings)
    try:
        async with asyncio.timeout(settings.ready_check_timeout_seconds):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engines() -> None:
    for engine in _engines.values():
        await engine.dispose()
    _engines.clear()
