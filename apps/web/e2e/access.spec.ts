import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Real-flow E2E: fake OIDC provider + FastAPI + Next.js, one browser.
 * No mocked network anywhere — every assertion rides the actual stack.
 */

test.describe.configure({ mode: "serial" });

async function signIn(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("link", { name: /sign in/i }).click();
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

async function signOutIfNeeded(page: import("@playwright/test").Page) {
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

test("signed-out → fake OIDC login → signed-in shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("screen-signed-out")).toBeVisible();

  await signIn(page);
  await expect(page.getByRole("button", { name: /account menu/i })).toBeVisible();
  await page.getByRole("button", { name: /account menu/i }).click();
  await expect(page.getByRole("menu").getByText("Owner One")).toBeVisible();
  await expect(page.getByRole("menu").getByText("owner@example.test")).toBeVisible();
});

test("permission-aware navigation and role editing", async ({ page }) => {
  await signIn(page);

  // Permission-gated nav entry is present (UI convenience; API is authority).
  await expect(
    page.getByRole("link", { name: /audit & administration/i }),
  ).toBeVisible();
  // Navigate directly: avoids the dev-server first-compile/hydration race.
  await page.goto("/admin");

  await expect(page.getByRole("tab", { name: "Roles" })).toBeVisible();
  await expect(page.getByTestId("role-list")).toBeVisible();
  await expect(page.getByTestId("role-list").getByText("Platform Owner")).toBeVisible();

  // Create a custom role, grant it one permission, save.
  const roleName = `E2E Role ${Date.now()}`;
  await page.getByLabel("New role name").fill(roleName);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByTestId("role-list").getByText(roleName)).toBeVisible();

  await page.getByTestId("role-list").getByText(roleName).click();
  await expect(page.getByTestId("permission-matrix")).toBeVisible();
  await page
    .getByTestId("permission-matrix")
    .getByRole("checkbox")
    .first()
    .check();
  await page.getByRole("button", { name: /save permissions/i }).click();
  await expect(page.getByTestId("permission-matrix")).not.toBeVisible();

  // Audit tab shows real events from this very session.
  await page.getByRole("tab", { name: "Audit" }).click();
  await expect(page.getByTestId("audit-table")).toBeVisible();
  await expect(page.getByTestId("audit-table").getByText("rbac.role.create").first()).toBeVisible();
});

test("logout invalidates the session and protects routes", async ({ page }) => {
  await signIn(page);
  await page.getByRole("button", { name: /account menu/i }).click();
  await page.getByRole("menuitem", { name: /sign out/i }).click();
  await expect(page.getByTestId("screen-signed-out")).toBeVisible();

  // Direct navigation to a protected route stays locked out.
  await page.goto("/admin");
  await expect(page.getByTestId("screen-signed-out")).toBeVisible();
});

test("responsive shell: mobile drawer works", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await expect(page.getByRole("navigation", { name: "Primary" })).not.toBeVisible();
  await page.getByRole("button", { name: /open navigation/i }).click();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
});

test("dark/light theme toggle", async ({ page }) => {
  await signIn(page);
  const hasDark = () => page.evaluate(() => document.documentElement.classList.contains("dark"));
  const before = await hasDark();
  await page
    .getByRole("button", { name: before ? /switch to light theme/i : /switch to dark theme/i })
    .click();
  await expect
    .poll(async () => await hasDark())
    .toBe(!before);
});

test("keyboard: sign-in is reachable and actionable", async ({ page }) => {
  await page.goto("/");
  const signInLink = page.getByRole("link", { name: /sign in/i });
  await signInLink.focus();
  await expect(signInLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
});

test("accessibility smoke: no critical violations", async ({ page }) => {
  await page.goto("/");
  const signedOutScan = await new AxeBuilder({ page }).analyze();
  expect(
    signedOutScan.violations.filter((violation) => violation.impact === "critical"),
  ).toEqual([]);

  await signIn(page);
  const shellScan = await new AxeBuilder({ page }).analyze();
  expect(
    shellScan.violations.filter((violation) => violation.impact === "critical"),
  ).toEqual([]);
});
