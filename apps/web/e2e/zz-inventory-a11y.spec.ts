import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Sprint 4 UI acceptance gates on the REAL production build with REAL
 * inventory data (this file sorts after inventory.spec.ts, which enrolled
 * the agent and filled the projection; by now the agent is stopped, so the
 * screens honestly show disconnected/stale — exactly the states that must
 * never look healthy).
 *
 * Gates: 390px mobile + ≥1280px desktop; light AND dark theme; keyboard-
 * only navigation; zero critical/serious axe violations; heading/table
 * semantics; focus visibility; zero horizontal viewport overflow; stale
 * never healthy-colored; unknown buckets visible; payload hygiene.
 */

test.describe.configure({ mode: "serial" });
test.setTimeout(120_000);

let clusterId = "";

async function signInAs(page: Page, subject: string) {
  await page.goto(`/v1/auth/login?redirect=/&login_hint=${subject}`);
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

async function findClusterA(page: Page): Promise<string> {
  const response = await page.request.get("/v1/clusters");
  const body = (await response.json()) as {
    clusters: { id: string; cluster_ref: string }[];
  };
  const clusterA = body.clusters.find((row) => row.cluster_ref === "cluster-a");
  expect(clusterA, "fixture cluster-a must exist").toBeTruthy();
  return clusterA!.id;
}

async function setTheme(page: Page, dark: boolean) {
  const isDark = () =>
    page.evaluate(() => document.documentElement.classList.contains("dark"));
  if ((await isDark()) === dark) return;
  await page
    .getByRole("button", {
      name: dark ? /switch to dark theme/i : /switch to light theme/i,
    })
    .click();
  await expect.poll(async () => await isDark()).toBe(dark);
}

async function assertNoHorizontalOverflow(page: Page, label: string) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow, `${label}: horizontal viewport overflow`).toBeLessThanOrEqual(0);
}

async function assertAxeClean(page: Page, label: string) {
  const scan = await new AxeBuilder({ page }).analyze();
  const blocking = scan.violations.filter((violation) =>
    ["critical", "serious"].includes(violation.impact ?? ""),
  );
  expect(
    blocking.map((violation) => `${violation.id}: ${violation.help}`),
    `${label}: critical/serious axe violations`,
  ).toEqual([]);
}

const INVENTORY_SCREENS = [
  { name: "cluster list", path: () => "/clusters", ready: "cluster-list" },
  { name: "cluster detail", path: () => `/clusters/${clusterId}`, ready: "agent-card" },
  {
    name: "inventory browser",
    path: () => `/clusters/${clusterId}/inventory`,
    ready: "resource-rows",
  },
] as const;

test("collect fixture ids", async ({ page }) => {
  await signInAs(page, "user-owner");
  clusterId = await findClusterA(page);
});

for (const viewport of [
  { name: "mobile-390", width: 390, height: 844 },
  { name: "desktop-1280", width: 1280, height: 900 },
] as const) {
  for (const theme of ["light", "dark"] as const) {
    test(`${viewport.name} ${theme}: inventory screens pass axe + overflow gates`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await signInAs(page, "user-owner");
      await setTheme(page, theme === "dark");

      for (const screen of INVENTORY_SCREENS) {
        await page.goto(screen.path());
        await expect(page.getByTestId(screen.ready).first()).toBeVisible();
        const label = `${screen.name} @ ${viewport.name}/${theme}`;
        await assertNoHorizontalOverflow(page, label);
        await assertAxeClean(page, label);
      }
    });
  }
}

test("semantics: headings and tables are real structure, not styling", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await page.goto(`/clusters/${clusterId}`);
  await expect(page.getByTestId("agent-card")).toBeVisible();

  // Exactly one h1; h2 section headings exist below it.
  expect(await page.locator("h1").count()).toBe(1);
  expect(await page.locator("h2").count()).toBeGreaterThan(0);

  await page.goto(`/clusters/${clusterId}/inventory`);
  await expect(page.getByTestId("resource-rows")).toBeVisible();
  const table = page.locator("table").first();
  const headers = table.locator("thead th[scope='col']");
  expect(await headers.count()).toBeGreaterThanOrEqual(5);
});

test("keyboard only: filter, row link, and drilldown are reachable", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await page.goto(`/clusters/${clusterId}/inventory`);
  await expect(page.getByTestId("resource-rows")).toBeVisible();

  // Reach the kind filter with Tab alone and drive it with the keyboard.
  const kindFilter = page.getByTestId("filter-kind");
  for (let presses = 0; presses < 40; presses++) {
    if (await kindFilter.evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press("Tab");
  }
  expect(
    await kindFilter.evaluate((el) => el === document.activeElement),
    "kind filter must be reachable by keyboard",
  ).toBe(true);
  await kindFilter.selectOption("Namespace");
  await expect(page.getByTestId("resource-rows")).toContainText("kube-system");

  // Tab to the first resource row link; Enter opens the drilldown.
  const firstRowLink = page.getByTestId("resource-rows").locator("a").first();
  for (let presses = 0; presses < 60; presses++) {
    if (await firstRowLink.evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press("Tab");
  }
  expect(
    await firstRowLink.evaluate((el) => el === document.activeElement),
    "resource link must be reachable by keyboard",
  ).toBe(true);
  // The focused element is visibly focused (outline or ring).
  const focusVisible = await firstRowLink.evaluate((el) => {
    const style = window.getComputedStyle(el);
    return el.matches(":focus-visible") || style.outlineStyle !== "none";
  });
  expect(focusVisible, "focus must be visible").toBe(true);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("health-card")).toBeVisible();
  await assertAxeClean(page, "resource drilldown");
});

test("honesty gates: stale is never healthy-colored, unknown stays visible", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await page.goto(`/clusters/${clusterId}`);
  const freshness = page.getByTestId("freshness-card");
  await expect(freshness).toBeVisible();
  // The agent from inventory.spec.ts is stopped: stale/disconnected land
  // in their OWN visual states, never the healthy badge.
  await expect(freshness.getByTestId("status-stale")).toBeVisible();
  await expect(freshness.getByTestId("status-healthy")).toHaveCount(0);
  // Unknown buckets are rendered as data, not hidden.
  await expect(page.getByTestId("rollup-counts").first()).toContainText("unknown");
});

test("denied and not-found stay uniform without data leakage", async ({ page }) => {
  await signInAs(page, "user-plain");
  await page.goto(`/clusters/${clusterId}`);
  await expect(page.getByTestId("state-not-configured").first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/clusters/${clusterId}/inventory`);
  await expect(page.getByTestId("state-not-configured").first()).toBeVisible();
  await assertNoHorizontalOverflow(page, "denied inventory @ 390px");
  await assertAxeClean(page, "denied inventory");
});

test("browser payloads stay free of certs, keys, and internal endpoints", async ({
  page,
}) => {
  const payloads: string[] = [];
  page.on("response", async (response) => {
    if (response.url().includes("/v1/")) {
      payloads.push(await response.text().catch(() => ""));
    }
  });
  await signInAs(page, "user-owner");
  await page.goto(`/clusters/${clusterId}/inventory`);
  await expect(page.getByTestId("resource-rows")).toBeVisible();
  const combined = payloads.join("\n");
  expect(combined).not.toContain("BEGIN CERTIFICATE");
  expect(combined).not.toContain("BEGIN PRIVATE KEY");
  expect(combined).not.toContain("/internal/v1/agent");
});
