import http from "node:http";
import net from "node:net";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Metrics E2E on the real stack: fake OIDC + FastAPI + PostgreSQL + Redis +
 * the local fixture Prometheus behind the E2E flaky proxy + the production
 * web build. No route mocking; the browser never talks to the provider.
 *
 * Fixture world: alpha (dev with core-api/auth-api traffic, test with NO
 * telemetry targets → honest empty) has a configured provider; beta's
 * provider integration is honestly not_configured.
 */

test.describe.configure({ mode: "serial" });

const FLAKY_CONTROL = "http://127.0.0.1:59191";
const REDIS_PORT = Number(process.env.DRAKE_E2E_REDIS_PORT ?? "56379");

/** Raw RESP EVAL against the disposable Redis (no client dependency). */
function redisEval(script: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: "127.0.0.1", port: REDIS_PORT }, () => {
      const payload =
        `*3\r\n$4\r\nEVAL\r\n$${Buffer.byteLength(script)}\r\n${script}\r\n$1\r\n0\r\n`;
      socket.write(payload);
    });
    socket.on("data", (data) => {
      socket.end();
      const text = data.toString();
      if (text.startsWith(":")) resolve(Number(text.slice(1).trim()));
      else reject(new Error(`unexpected redis reply: ${text.slice(0, 60)}`));
    });
    socket.on("error", reject);
  });
}

const TOTAL_LEASES =
  "local t=0 for _,k in ipairs(redis.call('KEYS','telemetry:lease:*')) do " +
  "t=t+redis.call('ZCARD',k) end return t";
const MAX_PRINCIPAL =
  "local m=0 for _,k in ipairs(redis.call('KEYS','telemetry:lease:principal:*')) do " +
  "local c=redis.call('ZCARD',k) if c>m then m=c end end return m";
const MAX_TARGET =
  "local m=0 for _,k in ipairs(redis.call('KEYS','telemetry:lease:target:*')) do " +
  "local c=redis.call('ZCARD',k) if c>m then m=c end end return m";

async function providerStats(page: Page): Promise<{
  started: number;
  completed: number;
  disconnected: number;
}> {
  const response = await page.request.get(`${FLAKY_CONTROL}/__stats`);
  return (await response.json()) as { started: number; completed: number; disconnected: number };
}

async function signInAs(page: Page, subject: string) {
  await page.goto(`/v1/auth/login?redirect=/&login_hint=${subject}`);
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

async function signOutIfNeeded(page: Page) {
  await page.goto("/");
  const menu = page.getByRole("button", { name: /account menu/i });
  if (await menu.isVisible().catch(() => false)) {
    await menu.click();
    await page.getByRole("menuitem", { name: /sign out/i }).click();
    await expect(page.getByTestId("screen-signed-out")).toBeVisible();
  }
}

async function setProviderMode(page: Page, mode: "ok" | "fail" | "slow") {
  const response = await page.request.post(`${FLAKY_CONTROL}/__mode/${mode}`);
  expect(response.ok()).toBeTruthy();
}

async function openAlphaOverview(page: Page) {
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByTestId("project-list").getByText("Alpha", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Alpha" })).toBeVisible();
}

test.afterEach(async ({ page }) => {
  // Never leave the provider in failure mode for the next scenario.
  await setProviderMode(page, "ok");
});

test.beforeEach(async ({ page }) => {
  await signOutIfNeeded(page);
});

test("owner: project overview metrics with live data and URL time range", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await openAlphaOverview(page);

  const metrics = page.getByTestId("project-metrics");
  await expect(metrics).toBeVisible();
  await expect(metrics.getByTestId("dashboard-project-environment-overview-v1")).toBeVisible();

  // URL-backed time range: select 1h (also keeps the window tight enough
  // for a freshly started fixture Prometheus in CI).
  await page.getByRole("group", { name: "Time range" }).getByText("Last 1h").click();
  await expect(page).toHaveURL(/range=1h/);

  // Live golden signals from the fixture Prometheus (never fabricated):
  const requestRate = metrics.getByTestId("widget-request-rate-kpi");
  await expect(requestRate).toContainText("req/s");
  await expect(metrics.getByTestId("widget-scrape-state")).toContainText("Being scraped");
  await page.reload();
  await expect(
    page.getByRole("group", { name: "Time range" }).getByRole("button", { name: "Last 1h" }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(metrics.getByTestId("widget-request-rate-kpi")).toContainText("req/s");
});

test("owner: environment selector drives URL state and honest empty states", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await openAlphaOverview(page);

  const metrics = page.getByTestId("project-metrics");
  await page.getByRole("group", { name: "Time range" }).getByText("Last 1h").click();
  await expect(metrics.getByTestId("widget-request-rate-kpi")).toContainText("req/s");

  // alpha/test exists in the catalog but has NO telemetry targets:
  await metrics.getByRole("combobox").selectOption({ label: "test" });
  await expect(page).toHaveURL(/env=/);
  await expect(
    metrics.getByText("No data in the selected range.").first(),
  ).toBeVisible();
});

test("owner: service golden signals with chart, table fallback, profile gating", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await openAlphaOverview(page);
  await page.getByTestId("environment-list").getByText("dev", { exact: true }).click();
  await page.getByTestId("service-list").getByText("core-api", { exact: false }).click();
  await expect(page.getByRole("heading", { name: /core-api/i })).toBeVisible();
  await page.getByRole("group", { name: "Time range" }).getByText("Last 1h").click();

  const dashboard = page.getByTestId("dashboard-service-golden-signals-v1");
  await expect(dashboard).toBeVisible();
  const trend = dashboard.getByTestId("widget-request-rate-trend");
  await expect(trend.getByRole("img")).toBeVisible(); // accessible SVG chart
  await trend.getByText("Data table").click();
  await expect(trend.getByRole("table")).toBeVisible(); // tabular fallback

  // fastapi-v1 profile: kubernetes-only workload widgets never render.
  await expect(dashboard.getByTestId("widget-container-restarts")).toHaveCount(0);
  await expect(dashboard.getByTestId("widget-cpu-utilization")).toHaveCount(0);
});

test("provider outage: stale last-good, then honest unavailable with correlation id", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await openAlphaOverview(page);
  await page.getByTestId("environment-list").getByText("dev", { exact: true }).click();
  await page.getByTestId("service-list").getByText("core-api", { exact: false }).click();
  await page.getByRole("group", { name: "Time range" }).getByText("Last 1h").click();
  const dashboard = page.getByTestId("dashboard-service-golden-signals-v1");
  await expect(dashboard.getByTestId("widget-request-rate-trend")).toContainText("req/s");

  // Kill the provider; wait out the (test-shortened) fresh TTL.
  await setProviderMode(page, "fail");
  await page.waitForTimeout(2500);
  await page.reload();
  // Last-good serves — explicitly STALE, never presented as healthy.
  await expect(
    dashboard.getByTestId("widget-request-rate-trend").getByText("stale", { exact: true }),
  ).toBeVisible();

  // A different range has no last-good: honest unavailability + reference.
  await page.getByRole("group", { name: "Time range" }).getByText("Last 7d").click();
  const unavailable = dashboard.getByTestId("widget-request-rate-trend");
  await expect(unavailable.getByText("Telemetry source unavailable.")).toBeVisible();
  await expect(unavailable.getByText(/^ref: /)).toBeVisible();

  await setProviderMode(page, "ok");
});

test("beta project: telemetry honestly not configured", async ({ page }) => {
  await signInAs(page, "user-beta"); // Developer @ project beta (dedicated)
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByTestId("project-list").getByText("Beta", { exact: true }).click();
  const metrics = page.getByTestId("project-metrics");
  await expect(
    metrics.getByText("Telemetry source not configured.").first(),
  ).toBeVisible();
  // Never a fabricated zero or an "ok" state:
  await expect(metrics.getByText("req/s")).toHaveCount(0);
});

test("narrow environment grant: selector offers only the authorized environment", async ({
  page,
}) => {
  await signInAs(page, "user-env"); // Developer @ alpha/dev only
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByTestId("project-list").getByText("Alpha", { exact: true }).click();
  const metrics = page.getByTestId("project-metrics");
  await expect(metrics).toBeVisible();
  // Single authorized environment → no selector, just the fixed label:
  await expect(metrics.getByRole("combobox")).toHaveCount(0);
  await expect(metrics.getByText("dev", { exact: true })).toBeVisible();
  await page.getByRole("group", { name: "Time range" }).getByText("Last 1h").click();
  await expect(metrics.getByTestId("widget-request-rate-kpi")).toContainText("req/s");
});

test("keyboard access and axe on the metrics screens", async ({ page }) => {
  await signInAs(page, "user-owner");
  await openAlphaOverview(page);
  await expect(page.getByTestId("project-metrics")).toBeVisible();

  // Keyboard: the range group is reachable and operable with Enter.
  await page.getByRole("group", { name: "Time range" }).getByText("Last 7d").focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/range=7d/);

  const scan = await new AxeBuilder({ page }).analyze();
  const critical = scan.violations.filter((violation) => violation.impact === "critical");
  expect(critical).toEqual([]);
});

test("mobile 390px: dashboards render without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signInAs(page, "user-owner");
  await page.goto("/projects");
  await page.getByTestId("project-list").getByText("Alpha", { exact: true }).click();
  await expect(page.getByTestId("project-metrics")).toBeVisible();
  // The range control is hidden below sm — URL state still works (and keeps
  // the window tight for a freshly started fixture Prometheus in CI).
  const mobileUrl = new URL(page.url());
  mobileUrl.searchParams.set("range", "1h");
  await page.goto(mobileUrl.toString());
  await expect(
    page.getByTestId("project-metrics").getByTestId("widget-scrape-state"),
  ).toContainText("Being scraped");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("real end-to-end cancellation: rapid changes stay bounded and disconnects free server work", async ({
  page,
  context,
}) => {
  // Part 1 exercises the FULL chain (browser → production Next rewrite →
  // FastAPI → broker → real Redis → slow provider): bounded budgets, a
  // complete final load, and no self-inflicted 429s while generations
  // churn. Part 2 proves server-side disconnect cancellation over a REAL
  // HTTP socket against the same uvicorn the browser uses — the Next hop
  // cannot carry aborts upstream (it drains pooled responses; verified and
  // documented in next.config.ts), which is exactly why the deployment
  // plan routes /v1 directly to the API at the ingress.
  await page.request.post(`${FLAKY_CONTROL}/__stats/reset`);

  await signInAs(page, "user-owner");
  await openAlphaOverview(page);
  await page.getByTestId("environment-list").getByText("dev", { exact: true }).click();
  await page.getByTestId("service-list").getByText("core-api", { exact: false }).click();
  await expect(page.getByRole("heading", { name: /core-api/i })).toBeVisible();
  await expect(
    page.getByTestId("dashboard-service-golden-signals-v1").getByTestId("widget-scrape-state"),
  ).toContainText(/scraped|Unknown/i);

  // NOW slow the provider down and churn generations: 24h → 1h → 7d.
  await setProviderMode(page, "slow");
  const range = page.getByRole("group", { name: "Time range" });
  await range.getByText("Last 1h").click();
  await page.waitForTimeout(300);
  await range.getByText("Last 7d").click();

  // Budgets hold END TO END on the REAL Redis lease sets while churning:
  for (let sample = 0; sample < 5; sample += 1) {
    expect(await redisEval(MAX_PRINCIPAL)).toBeLessThanOrEqual(4);
    expect(await redisEval(MAX_TARGET)).toBeLessThanOrEqual(8);
    await page.waitForTimeout(400);
  }

  // The final (7d) generation loads COMPLETELY — no self-inflicted 429:
  const dashboard = page.getByTestId("dashboard-service-golden-signals-v1");
  await expect(dashboard.getByTestId("widget-request-rate-trend")).toContainText("req/s", {
    timeout: 30_000,
  });
  await expect(dashboard.getByTestId("widget-error-ratio-trend")).toContainText("%");
  await expect(dashboard.getByTestId("widget-scrape-state")).toContainText("Being scraped");
  await expect(page.getByText(/query limit reached/i)).toHaveCount(0);

  // All lease tokens drain promptly once the dashboard settles — active
  // release, far below the 30s TTL backstop.
  await expect.poll(() => redisEval(TOTAL_LEASES), { timeout: 10_000 }).toBe(0);

  // ---- Part 2: real-HTTP disconnect against the API the browser uses ----
  // Let the browser's world go QUIET first (any bounded throttle-retry from
  // the churn phase finishes), so the provider counters below observe only
  // this part's traffic.
  // Quiet = counters balanced AND unchanged for ≥4.5s (outlasting any
  // pending bounded throttle-retry timer plus its slow provider call).
  let previous = "";
  let stableSamples = 0;
  await expect
    .poll(
      async () => {
        const stats = await providerStats(page);
        const snapshot = JSON.stringify(stats);
        const balanced = stats.started === stats.completed + stats.disconnected;
        stableSamples = balanced && snapshot === previous ? stableSamples + 1 : 0;
        previous = snapshot;
        return stableSamples >= 6 && (await redisEval(TOTAL_LEASES)) === 0;
      },
      { timeout: 25_000, intervals: [750] },
    )
    .toBe(true);

  const cookies = await context.cookies();
  const session = cookies.find((cookie) => cookie.name === "drake_session");
  expect(session).toBeTruthy();
  const me = (await (await page.request.get("/v1/me")).json()) as { csrf_token: string };
  const pathParts = new URL(page.url()).pathname.split("/");
  // /projects/{pid}/environments/{eid}/services/{sid} → the API collection:
  const services = await page.request.get(
    `/v1/projects/${pathParts[2]}/environments/${pathParts[4]}/services`,
  );
  const serviceId = ((await services.json()) as { services: { id: string }[] }).services[0].id;

  await page.request.post(`${FLAKY_CONTROL}/__stats/reset`);
  const statsBefore = await providerStats(page);
  const sockets = 3;
  const now = Date.now();
  for (let index = 0; index < sockets; index += 1) {
    const body = JSON.stringify({
      template_key: "service.request-rate.v1",
      scope: { type: "service", id: serviceId },
      // Distinct historical-ish ranges → three cache misses → three
      // concurrent slow provider calls.
      range: {
        from: new Date(now - (index + 3) * 3600_000).toISOString(),
        to: new Date(now - (index + 2) * 3600_000).toISOString(),
        step_seconds: 60,
      },
      parameters: {},
    });
    const requestOptions = {
      host: "127.0.0.1",
      port: 8123,
      path: "/v1/telemetry/query",
      method: "POST",
      headers: {
        cookie: `drake_session=${session?.value}`,
        "content-type": "application/json",
        "x-csrf-token": me.csrf_token,
        origin: "http://127.0.0.1:3456",
        "content-length": Buffer.byteLength(body),
      },
    };
    const inflight = http.request(requestOptions);
    inflight.on("error", () => {});
    inflight.write(body);
    inflight.end();
    // Destroy the raw socket well before the 3s slow response (and far
    // before the 5s provider timeout): a REAL client disconnect.
    setTimeout(() => inflight.destroy(), 800);
  }

  // Leases were held, then vanish promptly after the disconnects — their
  // own tokens, not the 30s TTL:
  await expect.poll(() => redisEval(TOTAL_LEASES), { timeout: 3_000 }).toBeGreaterThan(0);
  await expect.poll(() => redisEval(TOTAL_LEASES), { timeout: 5_000 }).toBe(0);

  // Provider-boundary proof over REAL HTTP: at least one slow provider
  // call ended as a client disconnect — its delayed response hit a closed
  // connection at ~3s, well before the 5s provider timeout, so cancelled
  // work did not run out its clock. The accounting must also CLOSE
  // (started == completed + disconnected): nothing hangs. Exhaustive
  // task-level cancellation semantics (transport CancelledError, immediate
  // own-token lease release, untouched observation, no orphan tasks) are
  // separately proven by the API disconnect integration test.
  await expect
    .poll(async () => {
      const stats = await providerStats(page);
      const started = stats.started - statsBefore.started;
      const disconnected = stats.disconnected - statsBefore.disconnected;
      const completed = stats.completed - statsBefore.completed;
      return started >= 1 && disconnected >= 1 && completed + disconnected === started;
    }, { timeout: 10_000 })
    .toBe(true);

  await setProviderMode(page, "ok");

  // Cancellation never faked a provider failure: alpha's telemetry
  // integration is not shown as degraded (cancelled calls record nothing).
  await page.getByRole("link", { name: "Integrations", exact: true }).click();
  const integrations = page.getByTestId("integration-table");
  await expect(integrations).toBeVisible();
  await expect(integrations.getByText("degraded", { exact: true })).toHaveCount(0);
});
