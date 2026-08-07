import { defineConfig } from "@playwright/test";

// Disposable-stack endpoints: local Compose defaults, overridable for CI
// service containers.
const databaseUrl =
  process.env.DRAKE_E2E_DATABASE_URL ??
  "postgresql+psycopg://drake:drake_local_only_dev@127.0.0.1:55432/drake";
const redisUrl = process.env.DRAKE_E2E_REDIS_URL ?? "redis://127.0.0.1:56379/0";

/**
 * E2E against the REAL stack: fake OIDC provider (test-only), the FastAPI
 * control plane on the disposable local database/Redis, and the Next.js app.
 * Prerequisite: `make up` + `bash scripts/e2e-setup.sh` from the repo root.
 *
 * The OIDC redirect URL goes through the WEB origin (/v1 rewrite), so the
 * session cookie is issued same-origin — exactly like a real deployment
 * behind one host.
 */
export default defineConfig({
  testDir: "./e2e",
  // Dev-mode servers cold-compile routes on first hit; timeouts are sized
  // for that, not for application latency.
  timeout: 90_000,
  expect: { timeout: 30_000 },
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3456",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run python apps/api/tests/fake_oidc.py --port 9556",
      cwd: "../..",
      url: "http://127.0.0.1:9556/.well-known/openid-configuration",
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      // E2E-only flaky proxy in front of the local fixture Prometheus:
      // lets the suite exercise honest stale/unavailable states.
      command: "uv run python scripts/e2e_flaky_prometheus.py",
      cwd: "../..",
      url: "http://127.0.0.1:59191/__health",
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      // Deterministic fake GitHub REST API for the integration scenario.
      command: "uv run python scripts/e2e_fake_github.py",
      cwd: "../..",
      url: "http://127.0.0.1:59097/__health",
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command:
        "uv run uvicorn drake_api.main:create_app --factory --host 127.0.0.1 --port 8123",
      cwd: "../..",
      url: "http://127.0.0.1:8123/health/live",
      reuseExistingServer: true,
      timeout: 30_000,
      env: {
        DRAKE_ENV: "local",
        DRAKE_OIDC_ISSUER: "http://127.0.0.1:9556",
        DRAKE_OIDC_CLIENT_ID: "drake-test-client",
        DRAKE_OIDC_REDIRECT_URL: "http://127.0.0.1:3456/v1/auth/callback",
        DRAKE_DATABASE_URL: databaseUrl,
        DRAKE_REDIS_URL: redisUrl,
        DRAKE_ALLOWED_WEB_ORIGINS: '["http://127.0.0.1:3456"]',
        // Server-owned telemetry connectors (E2E: flaky proxy over the
        // fixture Prometheus). Short fresh-TTL override (local/test only)
        // lets the stale/last-good scenario run in seconds.
        DRAKE_TELEMETRY_CONNECTORS:
          '{"e2e-prometheus":{"url":"http://127.0.0.1:59191"}}',
        DRAKE_TELEMETRY_FRESH_TTL_OVERRIDE_SECONDS: "2",
        // Agent observation windows shrunk so disconnect→stale transitions
        // are observable in seconds (local/E2E only; defaults are 90/900).
        DRAKE_AGENT_HEARTBEAT_STALE_SECONDS: "6",
        DRAKE_AGENT_INVENTORY_STALE_SECONDS: "60",
        // GitHub App against the local fake — throwaway material only.
        DRAKE_GITHUB_APP_ENABLED: "true",
        DRAKE_GITHUB_APP_CLIENT_ID: "Iv1.e2elocal",
        DRAKE_GITHUB_APP_PRIVATE_KEY_FILE: ".e2e-github/app-key.pem",
        DRAKE_GITHUB_WEBHOOK_SECRET_FILE: ".e2e-github/webhook-secret",
        DRAKE_GITHUB_API_BASE_URL: "http://127.0.0.1:59097",
      },
    },
    {
      // Production build: deterministic E2E without dev-mode cold-compile
      // stalls, and closer to real deployment behavior.
      command: "pnpm build && pnpm start --port 3456",
      url: "http://127.0.0.1:3456",
      reuseExistingServer: true,
      timeout: 180_000,
      env: {
        DRAKE_API_URL: "http://127.0.0.1:8123",
      },
    },
  ],
});
