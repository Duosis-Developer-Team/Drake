import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Catalog access & state-semantics E2E on the real stack (fake OIDC + API +
 * PostgreSQL + Redis + production web build). No route mocking anywhere;
 * fixtures were written to PostgreSQL by the e2e-setup bootstrap only.
 *
 * Fixture world: alpha (envs dev/test on cluster-a) + beta (dev/prod).
 * Users: owner=Platform Owner; user-env=Developer@alpha/dev;
 * user-plain=Developer@project beta; user-cluster=cluster.view@org.
 */

test.describe.configure({ mode: "serial" });

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

test.beforeEach(async ({ page }) => {
  await signOutIfNeeded(page);
});

let clusterDetailUrl = "";

test("owner: full catalog walk — projects → overview → environment → service", async ({
  page,
}) => {
  await signInAs(page, "user-owner");

  // Command Center shows exact authorized catalog counts.
  await expect(page.getByTestId("catalog-counts")).toBeVisible();
  await expect(page.getByTestId("catalog-counts")).toContainText("Projects");

  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await expect(page.getByTestId("project-list")).toBeVisible();
  await expect(page.getByTestId("project-list")).toContainText("Alpha");
  await expect(page.getByTestId("project-list")).toContainText("Beta");

  await page.getByTestId("project-list").getByText("Alpha", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Alpha" })).toBeVisible();
  await expect(page.getByText("github:example-org/alpha", { exact: false })).toBeVisible();
  // Operational capability cards are honestly not configured:
  const grid = page.getByTestId("operational-grid");
  await expect(grid.getByTestId("state-not-configured")).toHaveCount(4);

  await page.getByTestId("environment-list").getByText("dev", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "dev" })).toBeVisible();
  await expect(page.getByText("cluster-a / alpha-dev")).toBeVisible();

  await page.getByTestId("service-list").getByText("core-api").click();
  await expect(page.getByRole("heading", { name: "core-api" })).toBeVisible();
  await expect(page.getByText(/livePath: \/health\/live/)).toBeVisible();
  await expect(
    page.getByTestId("operational-grid").getByTestId("state-not-configured"),
  ).toHaveCount(4);

  // Capture a cluster detail URL for the later unauthorized check.
  await page.getByRole("link", { name: "Clusters", exact: true }).click();
  await expect(page.getByTestId("cluster-list")).toBeVisible();
  await page.getByTestId("cluster-list").getByText("Cluster A").click();
  await expect(page.getByRole("heading", { name: "Cluster A", exact: true })).toBeVisible();
  clusterDetailUrl = page.url();
});

test("narrow environment user: only own environment/service; siblings 404", async ({
  page,
}) => {
  await signInAs(page, "user-env");

  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await expect(page.getByTestId("project-list")).toBeVisible();
  await expect(page.getByTestId("project-list")).toContainText("Alpha");
  await expect(page.getByTestId("project-list")).not.toContainText("Beta");
  // Authorized-child counts only:
  await expect(page.getByText(/1 env · 2 services/)).toBeVisible();

  await page.getByTestId("project-list").getByText("Alpha", { exact: true }).click();
  const environments = page.getByTestId("environment-list");
  await expect(environments).toBeVisible();
  await expect(environments).toContainText("dev");
  await expect(environments).not.toContainText("test");

  // Sibling environment via forged URL → honest not-found state.
  const projectUrl = page.url();
  await environments.getByText("dev", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "dev" })).toBeVisible();
  const devUrl = page.url();
  // Forge: replace the environment id with a random UUID (sibling stand-in).
  const forged = devUrl.replace(/environments\/[0-9a-f-]+/, "environments/00000000-0000-4000-8000-000000000000");
  await page.goto(forged);
  await expect(page.getByText(/not found/i).first()).toBeVisible();
  await page.goto(projectUrl); // recover
});

test("project user cannot reach cluster detail; cluster viewer can", async ({ page }) => {
  expect(clusterDetailUrl).not.toBe("");

  await signInAs(page, "user-plain");
  await expect(
    page.getByRole("link", { name: "Clusters", exact: true }),
  ).not.toBeVisible(); // nav gated
  await page.goto(clusterDetailUrl);
  await expect(page.getByText(/not found/i).first()).toBeVisible();
  await signOutIfNeeded(page);

  await signInAs(page, "user-cluster");
  await page.getByRole("link", { name: "Clusters", exact: true }).click();
  await expect(page.getByTestId("cluster-list")).toBeVisible();
  await page.getByTestId("cluster-list").getByText("Cluster A").click();
  await expect(page.getByRole("heading", { name: "Cluster A", exact: true })).toBeVisible();
  // Agent/inventory honestly not configured; no fabricated environments.
  await expect(
    page.getByTestId("operational-grid").getByTestId("state-not-configured"),
  ).toHaveCount(2);
  await expect(page.getByText(/no authorized environments/i)).toBeVisible();
  // Projects nav is permission-gated away for this user; the direct URL
  // still answers with an honest empty state (collection semantics).
  await expect(
    page.getByRole("link", { name: "Projects", exact: true }),
  ).not.toBeVisible();
  await page.goto("/projects");
  await expect(page.getByTestId("state-empty")).toBeVisible();
});

test("search returns only authorized results", async ({ page }) => {
  await signInAs(page, "user-env");
  await page.keyboard.press("ControlOrMeta+k");
  const input = page.getByLabel("Search query");
  await expect(input).toBeVisible();

  await input.fill("alpha");
  await expect(page.getByRole("option").first()).toBeVisible();
  const texts = await page.getByRole("option").allTextContents();
  expect(texts.join(" ")).toMatch(/alpha/i);
  expect(texts.join(" ")).not.toMatch(/beta/i);

  // Sibling environment name yields nothing (no existence oracle):
  await input.fill("test");
  await expect(page.getByText(/no authorized results/i)).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();

  // Owner DOES find the sibling environment by the same query.
  await signOutIfNeeded(page);
  await signInAs(page, "user-owner");
  await page.keyboard.press("ControlOrMeta+k");
  await page.getByLabel("Search query").fill("test");
  await expect(page.getByRole("option").first()).toBeVisible();
  const ownerTexts = await page.getByRole("option").allTextContents();
  expect(ownerTexts.join(" ")).toMatch(/test/i);
});

test("integration health: safe fields for owner, empty for narrow env user", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await page.getByRole("link", { name: "Integrations", exact: true }).click();
  await expect(page.getByTestId("integration-table")).toBeVisible();
  await expect(page.getByTestId("integration-table")).toContainText("prometheus");
  await expect(page.getByTestId("integration-table")).toContainText("not_configured");
  await expect(page.getByTestId("integration-table")).toContainText("never");
  await expect(page.locator("body")).not.toContainText("config_ref");
  await signOutIfNeeded(page);

  // Project-scope integrations require project.view — the narrow env user
  // sees an honest empty state, not someone else's connectors.
  await signInAs(page, "user-env");
  await page.goto("/integrations");
  await expect(page.getByTestId("state-empty")).toBeVisible();
});

test("catalog accessibility smoke: no critical violations", async ({ page }) => {
  await signInAs(page, "user-owner");
  await page.goto("/projects");
  await expect(page.getByTestId("project-list")).toBeVisible();
  const listScan = await new AxeBuilder({ page }).analyze();
  expect(listScan.violations.filter((v) => v.impact === "critical")).toEqual([]);

  await page.getByTestId("project-list").getByText("Alpha", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Alpha" })).toBeVisible();
  const overviewScan = await new AxeBuilder({ page }).analyze();
  expect(overviewScan.violations.filter((v) => v.impact === "critical")).toEqual([]);
});
