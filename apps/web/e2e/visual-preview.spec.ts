import { expect, test, type Page } from "@playwright/test";

/**
 * Reusable "current state" visual snapshot — manual capture, not a CI gate.
 *
 * Unlike `wave0-baseline.spec.ts` (a frozen record of the pre-remake UI),
 * this one captures whatever is checked out right now. Every implementation
 * wave's own quality gate (brief §17.3, §22) re-runs this against its
 * before/after commits rather than growing a new one-off script each time.
 *
 * Run manually against the real stack:
 *   make up && bash scripts/e2e-setup.sh
 *   DRAKE_VISUAL_OUT_DIR=.visual-preview/my-label \
 *     pnpm --filter @drake/web exec playwright test e2e/visual-preview.spec.ts
 */

test.skip(!!process.env.CI, "visual capture is a manual step, not a CI gate");
test.describe.configure({ mode: "serial" });
test.setTimeout(120_000);

const OUT_DIR = process.env.DRAKE_VISUAL_OUT_DIR ?? ".visual-preview/current";

/** Same critical-route set `experience.spec.ts` runs axe against. */
const ROUTES = [
  ["command-center", "/"],
  ["projects", "/projects"],
  ["clusters", "/clusters"],
  ["incidents", "/incidents"],
  ["integrations", "/integrations"],
] as const;

const VIEWPORTS = [
  { name: "desktop", width: 1920, height: 1080 },
  { name: "1280", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
] as const;

async function signIn(page: Page) {
  await page.goto("/v1/auth/login?redirect=/&login_hint=user-owner");
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

async function setTheme(page: Page, theme: "light" | "dark") {
  await page.evaluate((value) => localStorage.setItem("drake-theme", value), theme);
  await page.reload();
}

for (const theme of ["light", "dark"] as const) {
  for (const viewport of VIEWPORTS) {
    test(`preview: ${theme} at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await signIn(page);
      await setTheme(page, theme);

      for (const [slug, path] of ROUTES) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await page.screenshot({
          path: `${OUT_DIR}/${slug}-${viewport.name}-${theme}.png`,
          fullPage: true,
        });
      }
    });
  }
}
