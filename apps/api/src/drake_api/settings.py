"""Typed application settings.

All values come from the environment (``DRAKE_`` prefix) or an optional local
``.env`` file. Defaults are safe for local development only: the API binds to
localhost and no credential values are embedded in code.
"""

from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelemetryConnector(BaseModel):
    """Server-owned provider connector configuration.

    ``allow_private`` is the EXPLICIT opt-in required before a connector may
    resolve to private-network targets in a production-like environment
    (ADR-0015: "private networks only via explicitly allowed server-owned
    connectors"). Being present in the map is not enough on its own.
    """

    url: str
    allow_private: bool = False


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
    # Server-owned connector resolver: config_ref -> typed connector
    # configuration. Values come from the environment / external secret
    # store (JSON object), never from requests and never exposed through
    # the API.
    telemetry_connectors: dict[str, TelemetryConnector] = {}
    telemetry_max_timeout_seconds: float = 10.0
    # Local/test-only override so E2E can exercise stale/last-good flows
    # without waiting out production TTLs. Ignored outside local/test.
    telemetry_fresh_ttl_override_seconds: int | None = None
    # Drake's own broker metrics exposition. OFF by default; may only be
    # enabled in local/test — the public API listener never serves it in a
    # production-like environment (a future real scrape needs a separate
    # internal listener/service/network policy, not this flag).
    internal_metrics_enabled: bool = False

    # --- Cluster agent internal API (ADR-0016) ---
    # File/external-secret REFERENCES only; key material never lives in the
    # repository, database, logs, or responses.
    agent_ca_cert_file: str = ""
    agent_ca_key_file: str = ""
    agent_cert_ttl_days: int = 14
    # Inventory retention/cleanup bounds (ADR-0017 bounded completion
    # window). Safe production defaults; local/test may shrink them, and
    # validate_runtime_security refuses unsafe values elsewhere.
    agent_snapshot_ttl_seconds: int = 900
    agent_max_pending_snapshots: int = 4
    agent_snapshot_history_limit: int = 50
    agent_change_event_retention_days: int = 30
    agent_change_event_row_limit: int = 20_000
    agent_cleanup_batch_rows: int = 5_000
    # The internal agent listener's own TLS identity (server cert) and the
    # client-CA used for CERT_REQUIRED verification are configured on the
    # listener (scripts/run_internal_agent_api.py); these settings gate the
    # fail-closed production boot below.
    internal_agent_api_enabled: bool = False

    # --- GitHub App (Sprint 5A) -------------------------------------
    # Feature flag: everything below is inert until an operator turns it
    # on AND supplies the secret references.
    github_app_enabled: bool = False
    # GitHub recommends the CLIENT ID as the JWT issuer; the numeric app
    # id stays accepted for operators who configured it earlier.
    github_app_client_id: str = ""
    github_app_id: str = ""
    # Secret REFERENCES only — file paths into the operator's secret
    # store, exactly like agent_ca_key_file. The material never becomes a
    # settings value, a column, a log line, or an API response.
    github_app_private_key_file: str = ""
    github_webhook_secret_file: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_http_timeout_seconds: float = 10.0
    # JWT lifetime stays under GitHub's hard 10-minute ceiling.
    github_jwt_ttl_seconds: int = 540
    # Refresh an installation token this long before it expires, so a slow
    # request can never ride an already-dead token.
    github_token_refresh_buffer_seconds: int = 300
    github_webhook_max_body_bytes: int = 1_048_576

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
        if self.internal_metrics_enabled:
            raise RuntimeError(
                "internal metrics cannot be enabled on the public API outside local/test"
            )
        if self.internal_agent_api_enabled and not (
            self.agent_ca_cert_file and self.agent_ca_key_file
        ):
            raise RuntimeError("internal agent API requires Agent CA material outside local/test")
        if self.agent_snapshot_ttl_seconds < 300:
            raise RuntimeError("snapshot completion TTL below 300s is local/test only")
        if self.agent_change_event_retention_days < 7:
            raise RuntimeError("change-event retention below 7 days is local/test only")
        if self.agent_snapshot_history_limit < 10:
            raise RuntimeError("snapshot history limit below 10 is local/test only")
        if self.github_app_enabled:
            if not (self.github_app_private_key_file and self.github_webhook_secret_file):
                raise RuntimeError(
                    "GitHub App requires private key and webhook secret references "
                    "outside local/test"
                )
            if not (self.github_app_client_id or self.github_app_id):
                raise RuntimeError("GitHub App requires a client id (or app id) outside local/test")
            if not self.github_api_base_url.startswith("https://"):
                raise RuntimeError("GitHub API base URL must be https outside local/test")
        if self.github_jwt_ttl_seconds > 600:
            raise RuntimeError("GitHub App JWT lifetime cannot exceed GitHub's 10-minute ceiling")


@lru_cache
def get_settings() -> Settings:
    return Settings()
