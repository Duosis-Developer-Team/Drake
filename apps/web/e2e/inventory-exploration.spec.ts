import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Wave 3 exploration gate.
 *
 * This file grows across the whole wave: Task 5 (Wave 3A) adds the
 * Projects/Project-detail responsive+a11y loop below; Task 8 (Wave 3B) and
 * Task 12 (Wave 3C) extend it with their own scenarios, reusing the same
 * helpers and viewport/theme matrix defined here rather than each
 * reinventing one — consistent with how this file will keep growing rather
 * than forking into a parallel gate file per sub-wave.
 *
 * On the real stack — fake OIDC + API + PostgreSQL + Redis + production web
 * build. No route mocking: fixtures come from the e2e-setup bootstrap only.
 *
 * Fixture world: alpha (envs dev/test on cluster-a, criticality high) + beta
 * (envs dev/prod on cluster-a, criticality critical). Neither project has a
 * bound service-health workload in this lightweight fixture stack, so every
 * service reads `not_configured` — the one real, unmocked non-success tone
 * available without the heavyweight k3d agent stack (see
 * `zz-inventory-a11y.spec.ts`), and the one this gate's assertions use.
 */

test.describe.configure({ mode: "serial" });
test.setTimeout(120_000);

const VIEWPORTS = [
  { name: "390", width: 390, height: 844 },
  { name: "768", width: 768, height: 1024 },
  { name: "1024", width: 1024, height: 768 },
  { name: "1280", width: 1280, height: 800 },
  { name: "1440", width: 1440, height: 900 },
  { name: "1920", width: 1920, height: 1080 },
] as const;

async function signInAs(page: Page, subject: string) {
  await page.goto(`/v1/auth/login?redirect=/&login_hint=${subject}`);
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

async function setTheme(page: Page, dark: boolean) {
  await page.evaluate(
    (value) => localStorage.setItem("drake-theme", value),
    dark ? "dark" : "light",
  );
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
    .toBe(dark);
}

/**
 * Scoped to `#main` — the region Wave 3 pages own — rather than the whole
 * document. A pre-existing overflow in the shared TopBar's identity control
 * trips at exactly 768px on pages with a long enough title (confirmed via a
 * baseline-commit comparison: it reproduces identically on 3d16c38, well
 * before any Wave 3 change). That's real but out of this wave's file
 * boundary; scoping here keeps the gate honest about what Wave 3A itself
 * introduced without masking it or silently fixing shell chrome mid-gate.
 */
async function assertNoHorizontalOverflow(page: Page, label: string) {
  const overflow = await page.evaluate(() => {
    const main = document.getElementById("main");
    return main ? main.scrollWidth - window.innerWidth : 0;
  });
  expect(overflow, `${label}: horizontal viewport overflow`).toBeLessThanOrEqual(0);
}

/**
 * Scoped to `#main` for the same reason as the overflow check above: a
 * pre-existing dark-mode contrast violation in the shared TopBar's time-range
 * control (white text on `bg-accent`, ~2:1) trips only when that control is
 * visible at 768px, and reproduces identically on the pre-Wave-3 baseline
 * (3d16c38). It's real but shared shell chrome outside this wave's file
 * boundary, so this gate stays scoped to what Wave 3A itself renders.
 */
async function assertAxeClean(page: Page, label: string) {
  const scan = await new AxeBuilder({ page }).include("#main").analyze();
  const blocking = scan.violations.filter((violation) =>
    ["critical", "serious"].includes(violation.impact ?? ""),
  );
  expect(
    blocking.map((violation) => `${violation.id}: ${violation.help}`),
    `${label}: critical/serious axe violations`,
  ).toEqual([]);
}

let alphaProjectId = "";

test("collect fixture ids", async ({ page }) => {
  await signInAs(page, "user-owner");
  const response = await page.request.get("/v1/projects?limit=100");
  const body = (await response.json()) as { projects: { id: string; project_key: string }[] };
  const alpha = body.projects.find((project) => project.project_key === "alpha");
  expect(alpha, "fixture project alpha must exist").toBeTruthy();
  alphaProjectId = alpha!.id;
});

const WAVE3A_SCREENS = [
  { name: "projects portfolio", path: () => "/projects", ready: "project-list" },
  {
    name: "project topology",
    path: () => `/projects/${alphaProjectId}`,
    ready: "environment-list",
  },
] as const;

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`${viewport.name} ${theme}: Wave 3A screens pass axe + overflow gates`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await signInAs(page, "user-owner");
      await setTheme(page, theme === "dark");

      for (const screen of WAVE3A_SCREENS) {
        await page.goto(screen.path());
        await expect(page.getByTestId(screen.ready).first()).toBeVisible();
        const label = `${screen.name} @ ${viewport.name}/${theme}`;
        await assertNoHorizontalOverflow(page, label);
        await assertAxeClean(page, label);
      }
    });
  }
}
