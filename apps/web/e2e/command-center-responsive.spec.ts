import { expect, test, type Page } from "@playwright/test";

/**
 * Command Center's responsive reflow (Wave 2, Task 2.7).
 *
 * Below 1024px the reading order becomes verdict → attention queue →
 * timeline summary (brief §9.4) — a real DOM/tab-order change, not a visual
 * one, since a CSS `order` utility moves what a reader sees without moving
 * what Tab reaches. The health matrix collapses to its own disclosure list
 * at the same breakpoint (Task 2.4), independently of this reorder.
 */

test.describe.configure({ mode: "serial" });
test.setTimeout(120_000);

async function signIn(page: Page) {
  await page.goto("/v1/auth/login?redirect=/&login_hint=user-owner");
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

test("at 390px, the attention queue precedes the full timeline in DOM order", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);

  const order = await page.evaluate(() => {
    const attention = document.querySelector('[data-testid="needs-attention"]');
    const timeline = document.querySelector('[data-testid="correlation-timeline"]');
    if (!attention || !timeline) return null;
    // DOCUMENT_POSITION_FOLLOWING means `timeline` comes after `attention`.
    return Boolean(
      attention.compareDocumentPosition(timeline) & Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
  expect(order, "attention queue must precede the timeline in DOM order at 390px").toBe(true);

  // The full multi-lane track is replaced by a one-line summary at this
  // width — the "View full timeline" trigger is how the real track is
  // still reachable.
  await expect(page.getByTestId("view-full-timeline")).toBeVisible();
  await expect(page.getByTestId("operational-timeline")).toHaveCount(0);
});

test("at 1280px, the attention queue follows the full timeline, and no mobile trigger renders", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await signIn(page);

  const order = await page.evaluate(() => {
    const attention = document.querySelector('[data-testid="needs-attention"]');
    const timeline = document.querySelector('[data-testid="correlation-timeline"]');
    if (!attention || !timeline) return null;
    return Boolean(
      timeline.compareDocumentPosition(attention) & Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });
  expect(order, "timeline must precede the attention queue in DOM order at 1280px").toBe(true);

  await expect(page.getByTestId("operational-timeline")).toBeVisible();
  await expect(page.getByTestId("view-full-timeline")).toHaveCount(0);
});

test("the full-timeline dialog trap-focuses and returns focus to its trigger on Escape", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);

  const trigger = page.getByTestId("view-full-timeline");
  await trigger.focus();
  await trigger.click();

  const dialog = page.getByRole("dialog", { name: "Correlation timeline" });
  await expect(dialog).toBeVisible();
  // Focus starts inside the dialog, not left behind on the trigger.
  await expect(dialog).toContainText("Incidents");
  const focusInsideDialog = await page.evaluate(() => {
    const dialogEl = document.querySelector('[role="dialog"]');
    return Boolean(dialogEl && dialogEl.contains(document.activeElement));
  });
  expect(focusInsideDialog).toBe(true);

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

for (const viewport of [
  { name: "1024px", width: 1024, height: 900, expectGrid: false },
  { name: "1280px", width: 1280, height: 900, expectGrid: true },
] as const) {
  test(`health matrix renders as ${viewport.expectGrid ? "a grid" : "a disclosure list"} at ${viewport.name}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await signIn(page);

    const panel = page.getByTestId("health-matrix-panel");
    await expect(panel).toBeVisible();
    // Wait past the panel's own loading skeleton before reading which
    // variant rendered.
    await expect(panel.getByText("Loading service health")).toHaveCount(0);

    // This local stack's service-health fixtures are whatever the
    // environment happened to seed — if no service has reported health yet,
    // the panel is honestly a NotConfiguredState rather than an empty grid
    // (Task 2.4), and there is no grid/disclosure breakpoint to assert on.
    const hasCells = await panel.getByTestId("state-not-configured").count().then((n) => n === 0);
    test.skip(!hasCells, "no service-health fixtures seeded in this environment");

    const gridVisible = await panel.getByTestId("health-matrix-grid").isVisible().catch(() => false);
    const disclosureVisible = await panel
      .getByTestId("health-matrix-disclosure")
      .isVisible()
      .catch(() => false);

    expect(gridVisible).toBe(viewport.expectGrid);
    expect(disclosureVisible).toBe(!viewport.expectGrid);
  });
}
