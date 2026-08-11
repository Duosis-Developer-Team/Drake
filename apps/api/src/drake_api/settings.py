"""Typed application settings.

All values come from the environment (``DRAKE_`` prefix) or an optional local
``.env`` file. Defaults are safe for local development only: the API binds to
localhost and no credential values are embedded in code.
"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from drake_api.origin import PublicOrigin, oidc_redirect_url, parse_public_origin


class TelemetryConnector(BaseModel):
    """Server-owned provider connector configuration.

    ``allow_private`` is the EXPLICIT opt-in required before a connector may
    resolve to private-network targets in a production-like environment
    (ADR-0015: "private networks only via explicitly allowed server-owned
    connectors"). Being present in the map is not enough on its own.

    ``allow_plaintext`` is the same shape of decision for the scheme, and it
    exists because the rule it relaxes had no way to express the ordinary
    case. A Prometheus reached over a Kubernetes Service speaks HTTP; there
    is no hostname to put on a certificate that anything would trust, so
    "https only in production" did not mean "be careful", it meant "no
    in-cluster provider, ever".

    What it does NOT relax: plaintext to a PUBLIC address stays refused even
    with this set, so the flag cannot become a way to send queries across
    the internet in the clear. Like ``allow_private`` it lives only in
    server-owned settings and can never arrive from a caller.
    """

    url: str
    allow_private: bool = False
    allow_plaintext: bool = False


class WebhookDestination(BaseModel):
    """Operator-owned webhook target, resolved from an opaque key.

    Modelled on `TelemetryConnector` for the same reason: the endpoint must
    never come from a request. A policy references a KEY; this map is the
    only place a URL exists, and it lives in settings (environment or
    external-secret backed), never in the database or an API response.

    `signing_secret_file` is a REFERENCE, following the same `*_file`
    convention as the Agent CA and GitHub App material. The secret is read
    at send time and never becomes a settings value, a column, a log line,
    or part of any response.
    """

    url: str
    display_name: str = ""
    allow_private: bool = False
    signing_secret_file: str = ""
    timeout_seconds: float = 10.0
    payload_schema_version: int = 1


class ProtectionConnector(BaseModel):
    """A backup reporter Drake will accept evidence from.

    The connector key in this map decides which project the evidence may
    touch — a payload cannot name its own project, environment or store
    outside what its connector is registered for. That is the whole reason
    the registry exists rather than a `project_id` field in the event.

    `signing_secret_file` is a REFERENCE, following the same `*_file`
    convention as the Agent CA and webhook signing material. The secret is
    read at verification time and never becomes a settings value, a column,
    a log line, or part of any response.
    """

    project_key: str
    display_name: str = ""
    signing_secret_file: str = ""
    # How far out of clock a signed request may be before it is refused.
    # Bounded, because a wide window is a replay window.
    replay_window_seconds: int = 300
    # How long this reporter may go quiet before its evidence is treated as
    # stale rather than current.
    stale_after_seconds: int = 129_600


class AlertmanagerIntegration(BaseModel):
    """One Alertmanager Drake accepts alerts from and can silence at.

    The opaque webhook key in this map decides which project the alerts may
    resolve into — a payload cannot name its own project. That is the whole
    reason the registry exists rather than a `project` field being trusted.

    Both credentials are REFERENCES, following the same `*_file` convention
    as the Agent CA, the GitHub App key and the webhook signing material.
    Neither becomes a settings value, a column, a log line, or a response.

    `api_base_url` is where silences are created. It lives here and nowhere
    else: a browser never talks to Alertmanager, and no payload or request
    can point Drake at a different one.
    """

    project_key: str
    display_name: str = ""
    # Inbound: the bearer token Alertmanager presents on its webhook calls.
    # Native Alertmanager signs nothing, so this is the authentication —
    # pretending a body HMAC exists would be inventing a guarantee.
    webhook_token_file: str = ""
    # Outbound: Alertmanager API v2 base and its credential.
    api_base_url: str = ""
    api_token_file: str = ""
    allow_private: bool = False
    api_timeout_seconds: float = 10.0
    # How long this Alertmanager may go quiet before its alert projection is
    # treated as possibly out of date.
    stale_after_seconds: int = 900
    # Silence duration bounds. A silence is a pause, not an off switch.
    min_silence_seconds: int = 300
    max_silence_seconds: int = 86_400


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

    # --- production edge (ADR-0021) ---
    # The ONE public origin Drake is served from. `/` is the web app and
    # `/v1` is the API on this same origin, so cookies and CSRF work with
    # no CORS and no second hostname. Every externally visible URL derives
    # from this value rather than from the incoming request, because a
    # forged Host header must not be able to decide where Drake redirects.
    public_origin: str = "http://127.0.0.1:3000"
    # How many reverse proxies sit in front of the API. Forwarded headers
    # are honoured only when this is set AND the request arrived through
    # that many hops — a client cannot promote its own X-Forwarded-* by
    # simply sending them.
    trusted_proxy_count: int = 0

    # Local default carries no password on purpose; real local values come
    # from .env (see .env.example). Never point this at shared infrastructure.
    database_url: str = "postgresql+psycopg://drake@127.0.0.1:5432/drake"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # CORS is deny-by-default: no origins means the middleware is not added.
    cors_origins: list[str] = []

    ready_check_timeout_seconds: float = 1.5

    # --- Authentication mode ---------------------------------------------
    #
    # `oidc`  — the identity provider is the only way in.
    # `local` — Drake verifies an email and password itself.
    #
    # Exactly one is active. They are not additive: running both at once
    # would mean a second, quieter way to obtain a session, which is not
    # something an operator should be able to switch on by accident.
    auth_mode: Literal["oidc", "local"] = "oidc"

    # Local-mode login throttling, per (client address, normalized email).
    local_login_max_attempts: int = 10
    local_login_window_seconds: int = 300

    # --- OIDC / sessions (values come from the environment; never secrets in code) ---
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    # Optional confidential-client secret, provided only via environment /
    # external secret store. Empty means public client + PKCE.
    oidc_client_secret: str = ""
    # Where the provider redirects back to (this API's callback endpoint).
    oidc_redirect_url: str = "http://127.0.0.1:8000/v1/auth/callback"
    # The base name. Over HTTPS the effective name carries the `__Host-`
    # prefix (see `effective_session_cookie_name`), which browsers only
    # honour when the cookie is Secure, host-only and path `/`.
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
    # Recovery worker: how often to sweep for stranded deliveries and
    # outstanding reconciliation intents, and how many to take per sweep.
    github_recovery_poll_seconds: float = 30.0
    github_recovery_batch_size: int = 50
    # Which organizations and repositories this deployment will look at.
    # Empty means "whatever the installation grants", which is already
    # bounded by the App installation itself; a non-empty list narrows it
    # further and is checked server-side, never against a payload's claim.
    github_allowed_organizations: list[str] = []
    github_allowed_repositories: list[str] = []
    # GitOps pull requests are the only path through which Drake would ever
    # WRITE to a repository. Off by default: while it is off no token is
    # minted and no provider call is made.
    github_gitops_pr_enabled: bool = False
    gitops_worker_enabled: bool = False
    gitops_worker_interval_seconds: float = 60.0

    # --- Incident evaluation runner (Sprint 6) ----------------------
    # OFF by default, like every other background actor here: a feature
    # that queries a datasource on a timer must be turned on deliberately,
    # not inherited from a default. Nothing below runs until it is.
    incident_runner_enabled: bool = False
    # Server-controlled and bounded. There is no per-service schedule and
    # no user-supplied interval: the clamp below is the whole contract.
    incident_runner_interval_seconds: float = 60.0
    incident_runner_batch_size: int = 25
    incident_runner_concurrency: int = 4
    # How long one cycle may hold the distributed lease. Long enough for a
    # full batch, short enough that a crashed replica's lease expires
    # rather than blocking evaluation until someone notices.
    incident_runner_lease_seconds: int = 120

    # --- Notification routing and delivery (Sprint 7) ---------------
    # Both actors are OFF by default. A control plane that starts calling
    # external endpoints because a default said so is a control plane
    # nobody can safely upgrade.
    notification_planner_enabled: bool = False
    notification_planner_interval_seconds: float = 60.0
    notification_planner_batch_size: int = 50
    webhook_worker_enabled: bool = False
    webhook_worker_interval_seconds: float = 30.0
    webhook_worker_batch_size: int = 20
    # Two ceilings: how much Drake sends at once, and how much it sends to
    # any single receiver. The second one stops one slow endpoint from
    # consuming the whole worker.
    webhook_worker_concurrency: int = 4
    webhook_destination_concurrency: int = 2
    # Bounded retry. Six attempts over at most a day, then dead-letter —
    # retrying forever is how a queue becomes a permanent backlog.
    webhook_max_attempts: int = 6
    webhook_max_elapsed_seconds: int = 86_400
    webhook_claim_seconds: int = 120
    webhook_max_response_bytes: int = 64 * 1024
    # Server-owned registry: opaque key → operator-controlled endpoint.
    # Empty by default, so a deployment that configures nothing sends
    # nothing.
    notification_webhooks: dict[str, WebhookDestination] = {}
    # Where a notification's incident link points. Empty means links are
    # emitted as relative paths only.
    public_app_base_url: str = ""

    # --- Deployment intelligence (Sprint 8) -------------------------
    # Base for composing a workflow-run link from typed provenance parts.
    # Empty means no link is produced — Drake never stores or accepts a URL,
    # so without a configured base there is simply nothing to link to.
    workflow_run_base_url: str = "https://github.com"
    deployment_ingest_enabled: bool = False
    deployment_ingest_interval_seconds: float = 120.0
    deployment_ingest_batch_size: int = 200

    # --- Protection Center (Sprint 9) -------------------------------
    # Server-owned registry of backup reporters. Empty by default: a
    # deployment that registers no connector accepts no protection
    # evidence at all.
    protection_connectors: dict[str, ProtectionConnector] = {}
    protection_max_body_bytes: int = 256 * 1024
    protection_max_page_records: int = 500
    # Periodic re-evaluation. Off by default like every other background
    # actor here; evaluation also runs inline after ingest.
    protection_evaluation_enabled: bool = False
    protection_evaluation_interval_seconds: float = 300.0

    # --- Alerts, SLO and incident operations (Sprint 10) ------------
    # Server-owned registry of Alertmanagers. Empty by default: a
    # deployment that registers none accepts no alerts and can silence
    # nothing.
    alertmanager_integrations: dict[str, AlertmanagerIntegration] = {}
    alertmanager_max_body_bytes: int = 1_048_576
    # The silence worker performs the only outbound provider mutation in
    # Drake. Off by default, like every other background actor here.
    silence_worker_enabled: bool = False
    silence_worker_interval_seconds: float = 30.0
    silence_worker_batch_size: int = 20
    silence_max_attempts: int = 5
    # SLO evaluation runs through the Sprint 5 broker, so it is a query
    # load against someone's Prometheus. Off unless deliberately enabled.
    slo_evaluator_enabled: bool = False
    slo_evaluator_interval_seconds: float = 300.0
    slo_evaluator_batch_size: int = 25
    slo_evaluator_lease_seconds: int = 300

    @property
    def effective_session_cookie_name(self) -> str:
        """`__Host-` wherever the cookie is actually Secure.

        The prefix is a browser-enforced promise: host-only, path `/`, and
        Secure. Applying it over plaintext would simply make the cookie be
        rejected, so local and test keep the bare name.
        """
        if self.is_production_like:
            return f"__Host-{self.session_cookie_name}"
        return self.session_cookie_name

    @property
    def local_auth_enabled(self) -> bool:
        return self.auth_mode == "local"

    @property
    def is_production_like(self) -> bool:
        return self.env not in ("local", "test")

    def resolved_public_origin(self) -> "PublicOrigin":
        """The validated canonical origin. Raises if it is unusable."""
        return parse_public_origin(self.public_origin, require_https=self.is_production_like)

    def validate_runtime_security(self) -> None:
        """Reject insecure identity configuration outside local/test.

        A plaintext (http) issuer — including any fake/test provider — must
        never be usable in a production-like environment.
        """
        if self.env in ("local", "test"):
            return
        # The public origin is the root of every externally visible URL, so
        # it is validated before anything derived from it is used.
        origin = self.resolved_public_origin()
        if self.trusted_proxy_count < 1:
            raise RuntimeError(
                "production runs behind an ingress: trusted_proxy_count must be at least 1 "
                "so forwarded host/proto are honoured only from that boundary"
            )
        for allowed in self.allowed_web_origins:
            if allowed and allowed != str(origin):
                raise RuntimeError(
                    "allowed_web_origins must contain only the canonical public origin "
                    "outside local/test"
                )
        if self.cors_origins:
            raise RuntimeError(
                "single-origin deployments need no CORS; cors_origins must be empty "
                "outside local/test"
            )
        # The OIDC contract is enforced only when OIDC is the way in. In
        # local mode an unset issuer is the honest state, not a
        # misconfiguration, and demanding one would force an operator to
        # invent values for a provider that is deliberately not in use.
        # Plaintext is rejected first, whatever the mode: a http:// issuer
        # left in configuration is a mistake worth naming precisely, and
        # reporting "missing client id" instead would send the operator
        # looking in the wrong place.
        if self.oidc_issuer.startswith("http://"):
            raise RuntimeError("plaintext OIDC issuer is not allowed outside local/test")
        if self.auth_mode == "oidc":
            if self.oidc_redirect_url.startswith("http://"):
                raise RuntimeError("plaintext OIDC redirect URL is not allowed outside local/test")
            if self.oidc_redirect_url != oidc_redirect_url(origin):
                raise RuntimeError(
                    "oidc_redirect_url must be the canonical public origin's callback path"
                )
            if not self.oidc_issuer:
                raise RuntimeError("auth_mode=oidc requires an OIDC issuer outside local/test")
            if not self.oidc_client_id:
                raise RuntimeError("auth_mode=oidc requires an OIDC client id outside local/test")
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
        if self.github_gitops_pr_enabled and not self.github_app_enabled:
            raise RuntimeError(
                "GitOps pull requests require the GitHub App: a write path cannot be "
                "enabled while the integration that authenticates it is off"
            )
        if self.github_gitops_pr_enabled != self.gitops_worker_enabled:
            # The two flags are one decision. On its own,
            # `github_gitops_pr_enabled` accepts requests nothing will ever
            # deliver; `gitops_worker_enabled` alone runs a loop that can
            # never be given work. Both are half-enabled states that look
            # like they are working, which is the failure mode this whole
            # boundary exists to prevent.
            raise RuntimeError(
                "GitOps flags must be set together outside local/test: "
                "github_gitops_pr_enabled and gitops_worker_enabled are one decision, "
                "and enabling only one accepts requests nothing delivers or runs a "
                "worker nothing can reach"
            )
        if self.github_gitops_pr_enabled:
            # Repository writes need a real provider, and a real provider
            # needs a real App: identity, both credential references, and
            # GitHub's own API origin.
            #
            # The recording double is never constructed outside local/test
            # (see `create_app`), so without these the write path would have
            # nothing behind it at all.
            missing = [
                name
                for name, present in (
                    ("github_app_enabled", self.github_app_enabled),
                    ("github app identity", bool(self.github_app_client_id or self.github_app_id)),
                    ("github_app_private_key_file", bool(self.github_app_private_key_file)),
                    ("github_webhook_secret_file", bool(self.github_webhook_secret_file)),
                )
                if not present
            ]
            if missing:
                raise RuntimeError(
                    "GitOps repository writes require a fully configured GitHub App "
                    f"outside local/test; missing: {', '.join(missing)}"
                )
            if self.github_api_base_url.rstrip("/") != "https://api.github.com":
                # A configurable API origin plus a write credential is an
                # exfiltration primitive: point it elsewhere and Drake
                # pushes a manifest, and its installation token, to whoever
                # is listening.
                raise RuntimeError(
                    "GitOps repository writes may only target https://api.github.com "
                    "outside local/test"
                )
        if self.github_jwt_ttl_seconds > 600:
            raise RuntimeError("GitHub App JWT lifetime cannot exceed GitHub's 10-minute ceiling")
        for key, destination in self.notification_webhooks.items():
            # Plaintext to an external receiver would put a signed incident
            # payload on the wire in the clear.
            if not destination.url.startswith("https://"):
                raise RuntimeError(
                    f"notification webhook {key!r} must use https outside local/test"
                )
        if self.webhook_max_attempts > 10:
            raise RuntimeError("webhook retry budget above 10 attempts is not bounded delivery")
        for key, alertmanager in self.alertmanager_integrations.items():
            # Plaintext to Alertmanager would put the API credential on the
            # wire, and a silence request is a privileged operation.
            if alertmanager.api_base_url and not alertmanager.api_base_url.startswith("https://"):
                raise RuntimeError(
                    f"alertmanager integration {key!r} must use https outside local/test"
                )
            # An unauthenticated webhook endpoint would let anyone forge
            # alerts into a project, which is worse than having no alerts.
            if not alertmanager.webhook_token_file:
                raise RuntimeError(
                    f"alertmanager integration {key!r} requires a webhook token reference "
                    "outside local/test"
                )
            if alertmanager.max_silence_seconds > 604_800:
                raise RuntimeError(
                    f"alertmanager integration {key!r} allows a silence longer than a week; "
                    "that is an off switch, not a pause"
                )
        if self.slo_evaluator_enabled and self.slo_evaluator_interval_seconds < 60:
            raise RuntimeError("SLO evaluator interval below 60s is local/test only")
        if self.incident_runner_enabled and self.incident_runner_interval_seconds < 30:
            # A tighter loop than this is a load generator pointed at
            # someone's Prometheus, not monitoring.
            raise RuntimeError("incident runner interval below 30s is local/test only")


@lru_cache
def get_settings() -> Settings:
    return Settings()
