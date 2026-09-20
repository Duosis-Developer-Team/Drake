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
  // Operational capabilities: telemetry is configured since Sprint 3 (fixture
  // Prometheus), the other three stay honestly not configured. They render as
  // status chips now rather than four stacked state blocks, but the claim
  // under test is unchanged — three of the four say "not configured", and
  // none of them says anything healthier than what was observed.
  const grid = page.getByTestId("operational-grid");
  // At least three of the four are honestly not configured. Telemetry is the
  // one that may be either — it depends on whether the connector has been
  // observed yet — so the assertion that matters is the one below it: nothing
  // in this grid claims a state healthier than what was observed.
  expect(await grid.getByText("Not configured").count()).toBeGreaterThanOrEqual(3);
  await expect(grid.getByText("Healthy")).toHaveCount(0);

  await page.getByTestId("environment-list").getByText("dev", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "dev", level: 1 })).toBeVisible();
  await expect(page.getByText("cluster-a / alpha-dev")).toBeVisible();

  await page.getByTestId("service-list").getByText("core-api").click();
  await expect(page.getByRole("heading", { name: "core-api" })).toBeVisible();
  await expect(page.getByText(/livePath: \/health\/live/)).toBeVisible();
  // All four service capabilities are honestly not configured for this
  // fixture, rendered as status chips rather than four stacked state blocks.
  await expect(page.getByTestId("operational-grid").getByText("Not configured")).toHaveCount(4);

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
  // Counts are their own tabular columns now, so they can be compared down
  // the list rather than read as prose.
  // Scoped to the list: the portfolio risk map above it is a real table too,
  // and its "medium criticality" row also carries Alpha's name and a count.
  const row = page.getByTestId("project-list").getByRole("row", { name: /Alpha/ });
  await expect(row.getByText("1", { exact: true })).toBeVisible();
  await expect(row.getByText("2", { exact: true })).toBeVisible();

  await page.getByTestId("project-list").getByText("Alpha", { exact: true }).click();
  const environments = page.getByTestId("environment-list");
  await expect(environments).toBeVisible();
  await expect(environments).toContainText("dev");
  await expect(environments).not.toContainText("test");

  // Sibling environment via forged URL → honest not-found state.
  const projectUrl = page.url();
  await environments.getByText("dev", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "dev", level: 1 })).toBeVisible();
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
    page.getByTestId("agent-card").getByText("not configured"),
  ).toBeVisible();
  await expect(
    page.getByTestId("freshness-card").getByText("not configured"),
  ).toBeVisible();
  // The empty state is asserted by its test id, not its wording: the claim
  // is that the page says "nothing here" rather than inventing environments,
  // and that claim should survive a copy edit.
  await expect(page.getByTestId("environments-empty")).toBeVisible();
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
  // The kind is presented as a label now ("Prometheus"), not the raw enum.
  // What matters here is that the integration is listed at all, so the match
  // is on the name rather than on how the page chooses to case it.
  await expect(page.getByTestId("integration-table")).toContainText(/prometheus/i);
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

test("owner: the portfolio risk map's health filter round-trips through the URL, survives Back, and never overwrites criticality", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await expect(page.getByTestId("project-risk-map")).toBeVisible();

  // Neither fixture project has a bound workload in this lightweight stack,
  // so "Not applicable" is the one real, unmocked non-success tone group
  // available here (see inventory-exploration.spec.ts's header comment).
  await page.getByRole("button", { name: "Not applicable" }).click();
  await expect(page).toHaveURL(/risk=not-applicable/);

  const list = page.getByTestId("project-list");
  const alphaRow = list.getByRole("row", { name: /Alpha/ });
  await expect(alphaRow).toBeVisible();
  await expect(alphaRow.getByText("High criticality")).toBeVisible();

  await list.getByText("Alpha", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Alpha" })).toBeVisible();
  // The health filter that got us here must not have rewritten the
  // project's own recorded criticality.
  await expect(page.getByText("High criticality")).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(/risk=not-applicable/);
  await expect(page.getByTestId("project-risk-map")).toBeVisible();
});

test("narrow environment user: project topology shows only the authorized environment", async ({
  page,
}) => {
  await signInAs(page, "user-env");
  await page.getByRole("link", { name: "Projects", exact: true }).click();
  await page.getByTestId("project-list").getByText("Alpha", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "Alpha" })).toBeVisible();

  const topology = page.getByTestId("environment-list");
  await expect(topology).toBeVisible();
  await expect(topology.getByRole("heading", { name: "dev" })).toBeVisible();
  await expect(topology.getByRole("heading", { name: "test" })).toHaveCount(0);
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
