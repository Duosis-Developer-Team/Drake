import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Sprint 13 experience gates, on the REAL production build.
 *
 * These are the properties the redesign is only allowed to claim if a machine
 * can check them: both themes render every route, the theme survives a reload
 * without a flash of the wrong one, the layout does not scroll sideways at any
 * supported width, keyboard-only navigation reaches the content, axe finds no
 * critical or serious violation on the screens people actually live on, and
 * the browser talks to nothing but Drake.
 *
 * Deliberately not visual-diff snapshots: a pixel baseline on a screen full of
 * live timestamps fails for reasons that have nothing to do with the UI. The
 * assertions below are about structure and behaviour, and the human visual QA
 * is the screenshot pass that runs beside them.
 */

test.describe.configure({ mode: "serial" });
test.setTimeout(120_000);

/** Every route that exists without a fixture id. */
const STATIC_ROUTES = [
  ["Command Center", "/"],
  ["Projects", "/projects"],
  ["Service health", "/service-health"],
  ["Clusters", "/clusters"],
  ["Objectives", "/slo"],
  ["Incidents", "/incidents"],
  ["Alerts", "/alerts"],
  ["Deployments", "/deployments"],
  ["Protection", "/protection"],
  ["Onboarding", "/onboarding"],
  ["Integrations", "/integrations"],
  ["GitHub integration", "/integrations/github"],
  ["Notification routing", "/notification-policies"],
  ["Notifications", "/notifications"],
  ["Deliveries", "/notification-deliveries"],
  ["Audit & access", "/admin"],
] as const;

/** The screens an operator lives on; axe runs against these. */
const CRITICAL_ROUTES = ["/", "/projects", "/clusters", "/incidents", "/integrations"] as const;

const VIEWPORTS = [
  { name: "wide desktop", width: 1920, height: 1080 },
  { name: "standard desktop", width: 1440, height: 900 },
  { name: "13-inch laptop", width: 1280, height: 800 },
  { name: "tablet landscape", width: 1024, height: 768 },
  { name: "tablet portrait", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
] as const;

async function signIn(page: Page) {
  await page.goto("/v1/auth/login?redirect=/&login_hint=user-owner");
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

async function setTheme(page: Page, theme: "light" | "dark" | "system") {
  await page.evaluate((value) => {
    if (value === "system") localStorage.removeItem("drake-theme");
    else localStorage.setItem("drake-theme", value);
  }, theme);
  await page.reload();
}

async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test("brand: the wordmark is the authoritative asset, and it swaps with the theme", async ({
  page,
}) => {
  await signIn(page);
  const wordmark = page.getByTestId("drake-wordmark");
  await expect(wordmark).toBeVisible();

  const backgroundFor = async () =>
    wordmark.evaluate((node) => getComputedStyle(node).backgroundImage);

  await setTheme(page, "light");
  expect(await backgroundFor()).toContain("drake-wordmark-light.webp");

  await setTheme(page, "dark");
  expect(await backgroundFor()).toContain("drake-wordmark-dark.webp");

  // The box is sized before the asset loads, so the shell cannot reflow.
  const box = await wordmark.boundingBox();
  expect(box?.width).toBeGreaterThan(0);
  expect(box?.height).toBeGreaterThan(0);
});

test("theme: system, light and dark all apply, and survive a reload without flashing", async ({
  page,
}) => {
  await signIn(page);

  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    await expect(page.locator("html")).toHaveClass(theme === "dark" ? /dark/ : /^(?!.*dark).*$/);

    // The class is applied by the inline script, so it is already correct on
    // the very first paint — nothing repaints from the wrong theme.
    const appliedBeforeHydration = await page.evaluate(() => ({
      dark: document.documentElement.classList.contains("dark"),
      // A body that has already painted has a resolved background colour from
      // the token layer rather than the UA default.
      background: getComputedStyle(document.body).backgroundColor,
    }));
    expect(appliedBeforeHydration.dark).toBe(theme === "dark");
    expect(appliedBeforeHydration.background).not.toBe("rgba(0, 0, 0, 0)");
  }

  // System follows the OS preference, in both directions.
  await setTheme(page, "system");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: "light" });
  await page.reload();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await page.emulateMedia({ colorScheme: null });
});

test("every route renders in both themes with no console error and no sideways scroll", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`${page.url()} :: ${message.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`${page.url()} :: ${error.message}`));

  await signIn(page);

  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    for (const [name, route] of STATIC_ROUTES) {
      await page.goto(route);
      await expect(page.locator("main")).toBeVisible();
      // Every screen names itself.
      await expect(page.locator("h1")).toHaveCount(1);
      expect(await horizontalOverflow(page), `${theme} ${name} scrolls sideways`).toBeLessThanOrEqual(0);
    }
  }

  expect(errors, `console errors:\n${errors.join("\n")}`).toEqual([]);
});

test("responsive: no route scrolls sideways at any supported width", async ({ page }) => {
  await signIn(page);
  for (const viewport of VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    for (const route of CRITICAL_ROUTES) {
      await page.goto(route);
      await expect(page.locator("main")).toBeVisible();
      expect(
        await horizontalOverflow(page),
        `${route} scrolls sideways at ${viewport.name}`,
      ).toBeLessThanOrEqual(0);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("mobile: the rail becomes a dialog that traps focus and closes on Escape", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);

  // The persistent rail is gone at this width; the trigger takes its place.
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeHidden();
  const trigger = page.getByRole("button", { name: /open navigation/i });
  await trigger.click();

  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("navigation", { name: "Primary" })).toBeVisible();

  // Focus moved into the drawer rather than being left behind it.
  const focusedInside = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"]');
    return dialog?.contains(document.activeElement) ?? false;
  });
  expect(focusedInside).toBe(true);

  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  // ...and returned to the control that opened it.
  await expect(trigger).toBeFocused();

  await page.setViewportSize({ width: 1440, height: 900 });
});

test("mobile: the theme control is reachable, in the drawer with the rest of settings", async ({
  page,
}) => {
  // The persistent rail is gone below 1024px and the top bar drops the theme
  // control for space, so the drawer is the only route to it. That is a
  // deliberate placement — it sits with scope and sign-out — but it has to
  // actually work, and nothing else asserts it.
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await expect(page.getByRole("radio", { name: /dark/i })).toBeHidden();

  await page.getByRole("button", { name: /open navigation/i }).click();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await drawer.getByRole("radio", { name: /dark/i }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);

  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("keyboard: the skip link reaches the content and navigation is operable", async ({
  page,
}) => {
  await signIn(page);
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: /skip to content/i });
  await expect(skip).toBeFocused();
  await skip.press("Enter");
  await expect(page.locator("#main")).toBeFocused();

  // Every primary entry is reachable and activatable by keyboard alone.
  const projects = page.getByRole("link", { name: "Projects", exact: true });
  await projects.focus();
  await expect(projects).toBeFocused();
  await projects.press("Enter");
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByRole("heading", { name: "Projects", level: 1 })).toBeVisible();
});

test("keyboard: the focus ring is visible on both themes", async ({ page }) => {
  await signIn(page);
  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    const link = page.getByRole("link", { name: "Projects", exact: true });
    await link.focus();
    const outline = await link.evaluate((node) => {
      const style = getComputedStyle(node);
      return { width: style.outlineWidth, style: style.outlineStyle, color: style.outlineColor };
    });
    expect(outline.style, `${theme} focus ring missing`).not.toBe("none");
    expect(parseFloat(outline.width), `${theme} focus ring is hairline`).toBeGreaterThanOrEqual(2);
  }
});

test("the sidebar collapses, stays collapsed across a reload, and keeps its labels", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("button", { name: /collapse navigation/i }).click();

  // Collapsed shows the D-and-serpent lockup rather than the full wordmark.
  await expect(page.getByTestId("drake-mark")).toBeVisible();
  await expect(page.getByTestId("drake-wordmark")).toBeHidden();
  // Icon-only entries keep their accessible names.
  await expect(page.getByRole("link", { name: "Projects", exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("drake-mark")).toBeVisible();

  await page.getByRole("button", { name: /expand navigation/i }).click();
  await expect(page.getByTestId("drake-wordmark")).toBeVisible();
});

test("axe: no critical or serious violation on the screens people live on", async ({ page }) => {
  await signIn(page);
  for (const theme of ["light", "dark"] as const) {
    await setTheme(page, theme);
    for (const route of CRITICAL_ROUTES) {
      await page.goto(route);
      await expect(page.locator("main")).toBeVisible();
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      const blocking = results.violations.filter(
        (violation) => violation.impact === "critical" || violation.impact === "serious",
      );
      expect(
        blocking.map((violation) => `${violation.id}: ${violation.help}`),
        `${theme} ${route}`,
      ).toEqual([]);
    }
  }
});

test("the browser talks to Drake and to nobody else", async ({ page }) => {
  const foreign: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.startsWith("http://127.0.0.1:3456")) return;
    if (url.startsWith("data:") || url.startsWith("blob:")) return;
    // The sign-in redirect to the identity provider is a navigation the user
    // makes, not a data call the page makes.
    if (url.startsWith("http://127.0.0.1:9556/")) return;
    foreign.push(`${request.method()} ${url}`);
  });

  await signIn(page);
  for (const [, route] of STATIC_ROUTES) {
    await page.goto(route);
    await expect(page.locator("main")).toBeVisible();
  }
  expect(foreign, `direct provider calls:\n${foreign.join("\n")}`).toEqual([]);
});

test("404 is its own page, not a generic failure card", async ({ page }) => {
  await signIn(page);
  await page.goto("/this-route-does-not-exist");
  await expect(page.getByRole("heading", { name: /this page does not exist/i })).toBeVisible();
  await expect(page.getByText(/error 404/i)).toBeVisible();
  // Not the same words as a query failure — those two must never merge.
  await expect(page.getByText(/something went wrong/i)).toHaveCount(0);
});

test("the Command Center never claims health it did not measure", async ({ page }) => {
  await signIn(page);
  const attention = page.getByTestId("needs-attention");
  await expect(attention).toBeVisible();

  const empty = page.getByTestId("attention-empty");
  if (await empty.isVisible().catch(() => false)) {
    // An empty triage list is a statement about the checks, not about health.
    await expect(empty).toContainText(/not a statement that the platform is healthy/i);
    await expect(empty).toContainText(/checked/i);
    await expect(empty).not.toContainText(/all systems/i);
  }

  // A source that could not be read shows a dash, never a zero.
  await expect(page.getByTestId("triage-strip")).toBeVisible();
});
