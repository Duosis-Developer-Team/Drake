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

async function setProviderMode(page: Page, mode: "ok" | "fail") {
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
