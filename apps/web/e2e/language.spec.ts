import { expect, test, type Page } from "@playwright/test";

/**
 * Language: English and Turkish.
 *
 * The preference is stored like the theme and applied to `<html lang>`
 * before first paint, and every screen reads through the same catalogue, so
 * this checks the three things a unit test cannot: the control is reachable
 * at every width, the choice survives a reload with the document language
 * already right, and a real route reads in Turkish end to end.
 */

async function signIn(page: Page) {
  await page.goto("/v1/auth/login?redirect=/&login_hint=user-owner");
  await expect(page.getByRole("heading", { name: /Command Center|Komuta Merkezi/ })).toBeVisible();
}

async function resetLanguage(page: Page) {
  await page.evaluate(() => localStorage.removeItem("drake-locale"));
}

test.afterEach(async ({ page }) => {
  await resetLanguage(page);
});

test("language: switching to Turkish translates the shell and survives a reload", async ({
  page,
}) => {
  await signIn(page);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  const nav = page.getByRole("navigation", { name: "Primary" });
  await expect(nav.getByRole("link", { name: "Incidents" })).toBeVisible();

  // The control lives in the top bar on a desktop viewport; its option
  // labels are the languages' own names and never translate.
  await page.getByRole("radiogroup", { name: "Language" }).getByRole("radio", { name: "TR" }).click();

  await expect(page.locator("html")).toHaveAttribute("lang", "tr");
  const navTr = page.getByRole("navigation", { name: "Ana gezinme" });
  await expect(navTr.getByRole("link", { name: "Olaylar" })).toBeVisible();
  await expect(navTr.getByRole("link", { name: "Komuta Merkezi" })).toBeVisible();
  await expect(page.getByRole("radiogroup", { name: "Dil" })).toBeVisible();

  // Reload: the inline script sets the document language before hydration,
  // and the catalogue renders Turkish from the first frame the shell paints.
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "tr");
  await expect(
    page.getByRole("navigation", { name: "Ana gezinme" }).getByRole("link", { name: "Olaylar" }),
  ).toBeVisible();

  // A real route, not just the rail: the incident list's own copy reads in
  // Turkish and its breadcrumb uses the catalogue's segment label.
  await page.goto("/incidents");
  await expect(page.getByRole("navigation", { name: "Sayfa yolu" })).toContainText("Olaylar");

  // Back to English from the same control.
  await page.getByRole("radiogroup", { name: "Dil" }).getByRole("radio", { name: "EN" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(
    page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Incidents" }),
  ).toBeVisible();
});

test("language: the sign-in screen and the search palette are translated too", async ({
  page,
}) => {
  // Signed-out copy is rendered outside the authenticated shell, so it has
  // its own path to the catalogue. The stored preference must reach it.
  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("drake-locale", "tr"));
  await page.reload();
  await expect(page.getByRole("heading", { name: "Drake'e giriş yapın" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Giriş yap" })).toBeVisible();

  await signIn(page);
  await page.keyboard.press("ControlOrMeta+K");
  const dialog = page.getByRole("dialog", { name: "Drake'te ara" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("textbox", { name: "Arama sorgusu" }).fill("olay");
  await expect(dialog.getByRole("option", { name: /Olaylar/ })).toBeVisible();
  await page.keyboard.press("Escape");
});

test("mobile: the language control is reachable, in the drawer with the rest of settings", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await expect(page.getByRole("radiogroup", { name: "Language" })).toBeHidden();

  await page.getByRole("button", { name: /open navigation/i }).click();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await drawer.getByRole("radiogroup", { name: "Language" }).getByRole("radio", { name: "Türkçe" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "tr");
  await expect(page.getByRole("dialog", { name: "Gezinme" })).toBeVisible();

  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1440, height: 900 });
});
