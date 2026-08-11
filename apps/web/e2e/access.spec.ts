import net from "node:net";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Real-flow E2E: fake OIDC provider + FastAPI + PostgreSQL + Redis +
 * production Next.js build, one browser. No mocked network routes anywhere.
 */

test.describe.configure({ mode: "serial" });

const REDIS_PORT = Number(process.env.DRAKE_E2E_REDIS_PORT ?? "56379");

/** Server-side session wipe = hard expiry, via raw Redis protocol. */
function flushRedis(port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: "127.0.0.1", port }, () => {
      socket.write("FLUSHALL\r\n");
    });
    socket.on("data", (data) => {
      socket.end();
      data.toString().startsWith("+OK")
        ? resolve()
        : reject(new Error("redis flush failed"));
    });
    socket.on("error", reject);
  });
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

test.beforeEach(async ({ page }) => {
  await signOutIfNeeded(page);
});

test("signed-out → fake OIDC login → signed-in shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("screen-signed-out")).toBeVisible();

  await page.getByRole("link", { name: /sign in/i }).click();
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
  await page.getByRole("button", { name: /account menu/i }).click();
  await expect(page.getByRole("menu").getByText("Owner One")).toBeVisible();
  await expect(page.getByRole("menu").getByText("owner@example.test")).toBeVisible();
});

test("unauthorized user cannot reach Access Control", async ({ page }) => {
  await signInAs(page, "user-plain");
  // Permission-gated nav entry is absent for a zero-permission identity.
  await expect(
    page.getByRole("link", { name: /audit & access/i }),
  ).not.toBeVisible();
  // Direct navigation hits the server-verified permission-denied state.
  await page.goto("/admin");
  await expect(page.getByTestId("state-permission-denied")).toBeVisible();
});

test("permission-aware navigation and role editing", async ({ page }) => {
  await signInAs(page, "user-owner");

  await expect(
    page.getByRole("link", { name: /audit & access/i }),
  ).toBeVisible();
  await page.goto("/admin");

  await expect(page.getByRole("tab", { name: "Roles" })).toBeVisible();
  await expect(page.getByTestId("role-list")).toBeVisible();
  await expect(page.getByTestId("role-list").getByText("Platform Owner")).toBeVisible();

  const roleName = `E2E Role ${Date.now()}`;
  await page.getByLabel("New role name").fill(roleName);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByTestId("role-list").getByText(roleName)).toBeVisible();

  await page.getByTestId("role-list").getByText(roleName).click();
  await expect(page.getByTestId("permission-matrix")).toBeVisible();
  await page.getByTestId("permission-matrix").getByRole("checkbox").first().check();
  await page.getByRole("button", { name: /save permissions/i }).click();
  await expect(page.getByTestId("permission-matrix")).not.toBeVisible();

  await page.getByRole("tab", { name: "Audit" }).click();
  await expect(page.getByTestId("audit-table")).toBeVisible();
  await expect(
    page.getByTestId("audit-table").getByText("rbac.role.create").first(),
  ).toBeVisible();
});

test("grant lifecycle: create, visible in list, double-submit safe, revoke", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  await page.goto("/admin");
  await page.getByRole("tab", { name: "Grants" }).click();
  await expect(page.getByTestId("grant-create-form")).toBeVisible();

  const table = page.getByTestId("grant-table");
  const activeRows = table
    .locator("tr", { hasText: "Plain User" })
    .filter({ hasText: "Developer" })
    .filter({ hasText: "active" });

  // Determinism across runs: revoke any leftover matching grants first
  // (the disposable DB persists between local E2E invocations).
  while ((await activeRows.count()) > 0) {
    const before = await activeRows.count();
    await activeRows.first().getByRole("button", { name: /revoke/i }).click();
    await expect(activeRows).toHaveCount(before - 1);
  }

  // Create a real grant for Plain User (identity exists from the earlier test).
  await page.locator("#grant-principal").selectOption({ label: "Plain User" });
  await page.locator("#grant-scope").selectOption({ index: 0 });
  await page.locator("#grant-role").selectOption({ label: "Developer" });

  const createButton = page.getByRole("button", { name: /create grant/i });
  await createButton.click();
  await expect(page.getByRole("status").getByText(/grant created/i)).toBeVisible();
  await expect(activeRows).toHaveCount(1);

  // Repeat-submit safety at the stack level: submitting again (the form
  // reset after success) cannot mint a duplicate — client validation stops
  // it and the row count stays at one. (The in-flight double-click collapse
  // is separately unit-tested against the fetch layer.)
  await createButton.click();
  await expect(
    page.getByTestId("grant-create-form").getByRole("alert"),
  ).toHaveText(/required/i);
  await expect(activeRows).toHaveCount(1);

  // Revoke it and observe the lifecycle state change.
  await activeRows.first().getByRole("button", { name: /revoke/i }).click();
  await expect(activeRows).toHaveCount(0);
  await expect(
    table
      .locator("tr", { hasText: "Plain User" })
      .filter({ hasText: "Developer" })
      .filter({ hasText: "revoked" })
      .first(),
  ).toBeVisible();
});

test("logout invalidates the session and protects routes", async ({ page }) => {
  await signInAs(page, "user-owner");
  await page.getByRole("button", { name: /account menu/i }).click();
  await page.getByRole("menuitem", { name: /sign out/i }).click();
  await expect(page.getByTestId("screen-signed-out")).toBeVisible();

  await page.goto("/admin");
  await expect(page.getByTestId("screen-signed-out")).toBeVisible();
});

test("server-side session expiry signs the browser out", async ({ page }) => {
  await signInAs(page, "user-owner");
  await flushRedis(REDIS_PORT);
  await page.reload();
  await expect(
    page.getByTestId("screen-signed-out").or(page.getByTestId("screen-expired")),
  ).toBeVisible();
});

test("responsive shell: mobile drawer works", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signInAs(page, "user-owner");
  await expect(page.getByRole("navigation", { name: "Primary" })).not.toBeVisible();
  await page.getByRole("button", { name: /open navigation/i }).click();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
});

test("theme control: system, light and dark", async ({ page }) => {
  // Three states now, not a toggle: "system" is a real persisted choice that
  // keeps following the OS, which a two-state toggle stops doing.
  await signInAs(page, "user-owner");
  const hasDark = () =>
    page.evaluate(() => document.documentElement.classList.contains("dark"));

  await page.getByRole("radio", { name: /dark/i }).first().click();
  await expect.poll(hasDark).toBe(true);

  await page.getByRole("radio", { name: /light/i }).first().click();
  await expect.poll(hasDark).toBe(false);

  await page.getByRole("radio", { name: /system/i }).first().click();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("drake-theme")))
    .toBeNull();
});

test("keyboard: sign-in is reachable and actionable", async ({ page }) => {
  await page.goto("/");
  const signInLink = page.getByRole("link", { name: /sign in/i });
  await signInLink.focus();
  await expect(signInLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
});

test("accessibility smoke: no critical violations incl. grant form", async ({ page }) => {
  await page.goto("/");
  const signedOutScan = await new AxeBuilder({ page }).analyze();
  expect(
    signedOutScan.violations.filter((violation) => violation.impact === "critical"),
  ).toEqual([]);

  await signInAs(page, "user-owner");
  const shellScan = await new AxeBuilder({ page }).analyze();
  expect(
    shellScan.violations.filter((violation) => violation.impact === "critical"),
  ).toEqual([]);

  await page.goto("/admin");
  await page.getByRole("tab", { name: "Grants" }).click();
  await expect(page.getByTestId("grant-create-form")).toBeVisible();
  const grantFormScan = await new AxeBuilder({ page }).analyze();
  expect(
    grantFormScan.violations.filter((violation) => violation.impact === "critical"),
  ).toEqual([]);
});
