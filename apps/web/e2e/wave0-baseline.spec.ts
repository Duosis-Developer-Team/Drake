import { expect, test, type Page } from "@playwright/test";

/**
 * Wave 0 visual baseline — manual capture, not a CI gate.
 *
 * Deliberately not a `toHaveScreenshot` assertion: `experience.spec.ts`
 * already explains why pixel-diff snapshots are the wrong tool on a screen
 * full of live timestamps. This spec exists purely to put a "before" image
 * on disk next to whatever the remake produces, for the human visual QA the
 * design brief requires before and after each wave (brief §17.3, §22).
 *
 * Run manually against the real stack:
 *   make up && bash scripts/e2e-setup.sh
 *   pnpm --filter @drake/web exec playwright test e2e/wave0-baseline.spec.ts
 */

test.skip(!!process.env.CI, "baseline capture is a manual step, not a CI gate");
test.describe.configure({ mode: "serial" });
test.setTimeout(120_000);

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
    test(`baseline: ${theme} at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await signIn(page);
      await setTheme(page, theme);

      for (const [slug, path] of ROUTES) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        await page.screenshot({
          path: `.wave0-baseline/${slug}-${viewport.name}-${theme}.png`,
          fullPage: true,
        });
      }
    });
  }
}
