"""Shared fixtures for API tests."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from drake_api.db import dispose_engines
from drake_api.main import create_app
from drake_api.testing import make_settings
from fastapi import FastAPI
from harness_s1 import require_it_settings
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from test_catalog_persistence_integration import reset_catalog

API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app() -> FastAPI:
    return create_app(make_settings())


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


# --- shared integration fixtures ---------------------------------------
# Declared here so the GitHub suites (integration, durability, boundary,
# contract) share one migrated database and one clean-catalog engine
# without importing each other's fixtures.


@pytest.fixture(scope="module")
def migrated_db() -> None:
    settings = require_it_settings()
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


@pytest.fixture
async def engine(migrated_db: None) -> Any:
    settings = require_it_settings()
    eng = create_async_engine(settings.database_url)
    await reset_catalog(eng)
    yield eng
    await eng.dispose()
    await dispose_engines()
