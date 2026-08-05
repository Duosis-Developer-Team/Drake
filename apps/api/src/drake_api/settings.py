"""Typed application settings.

All values come from the environment (``DRAKE_`` prefix) or an optional local
``.env`` file. Defaults are safe for local development only: the API binds to
localhost and no credential values are embedded in code.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DRAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Local default carries no password on purpose; real local values come
    # from .env (see .env.example). Never point this at shared infrastructure.
    database_url: str = "postgresql+psycopg://drake@127.0.0.1:5432/drake"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # CORS is deny-by-default: no origins means the middleware is not added.
    cors_origins: list[str] = []

    ready_check_timeout_seconds: float = 1.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
