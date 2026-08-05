"""Test support helpers.

Unit tests use deliberately unreachable localhost ports so no external service
is ever required. Integration tests read disposable local stack URLs from
``DRAKE_IT_DATABASE_URL`` / ``DRAKE_IT_REDIS_URL`` and skip when unset.
"""

import os

from drake_api.settings import Settings

# Closed-port endpoints: connection refused, immediately and deterministically.
UNREACHABLE_DB_URL = "postgresql+psycopg://drake@127.0.0.1:59432/drake"
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:59379/0"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "local",
        "database_url": UNREACHABLE_DB_URL,
        "redis_url": UNREACHABLE_REDIS_URL,
        "ready_check_timeout_seconds": 0.5,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def integration_settings() -> Settings | None:
    """Settings pointing at the disposable local stack, or None when absent."""
    database_url = os.environ.get("DRAKE_IT_DATABASE_URL")
    redis_url = os.environ.get("DRAKE_IT_REDIS_URL")
    if not database_url or not redis_url:
        return None
    return make_settings(database_url=database_url, redis_url=redis_url)
