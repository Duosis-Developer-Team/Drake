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

    # --- OIDC / sessions (values come from the environment; never secrets in code) ---
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    # Optional confidential-client secret, provided only via environment /
    # external secret store. Empty means public client + PKCE.
    oidc_client_secret: str = ""
    # Where the provider redirects back to (this API's callback endpoint).
    oidc_redirect_url: str = "http://127.0.0.1:8000/v1/auth/callback"
    session_cookie_name: str = "drake_session"
    session_ttl_seconds: int = 8 * 60 * 60
    login_state_ttl_seconds: int = 600
    # Origins allowed to perform cookie-authenticated mutations (CSRF layer 2).
    allowed_web_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # --- Telemetry (Query Broker) ---
    # Server-owned connector resolver: config_ref -> base URL. Values come
    # from the environment / external secret store (JSON object), never from
    # requests and never exposed through the API.
    telemetry_connectors: dict[str, str] = {}
    telemetry_max_timeout_seconds: float = 10.0
    # Local/test-only override so E2E can exercise stale/last-good flows
    # without waiting out production TTLs. Ignored outside local/test.
    telemetry_fresh_ttl_override_seconds: int | None = None
    internal_metrics_enabled: bool = True

    def validate_runtime_security(self) -> None:
        """Reject insecure identity configuration outside local/test.

        A plaintext (http) issuer — including any fake/test provider — must
        never be usable in a production-like environment.
        """
        if self.env in ("local", "test"):
            return
        if self.oidc_issuer.startswith("http://"):
            raise RuntimeError("plaintext OIDC issuer is not allowed outside local/test")
        if self.oidc_redirect_url.startswith("http://"):
            raise RuntimeError("plaintext OIDC redirect URL is not allowed outside local/test")


@lru_cache
def get_settings() -> Settings:
    return Settings()
