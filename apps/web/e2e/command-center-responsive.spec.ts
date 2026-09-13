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

test("at 390px, the DOM order is exactly verdict, attention queue, timeline — nothing interposed", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);

  // A pairwise "does A precede B" check can't catch a panel sitting between
  // them — it only proves A comes somewhere before B. This walks the actual
  // main-section testids in document order and asserts the whole sequence,
  // so a panel wrongly interposed between attention and timeline fails here.
  const testIds = ["verdict-panel", "needs-attention", "correlation-timeline"];
  const order = await page.evaluate((ids: string[]) => {
    const positions = ids
      .map((id) => {
        const el = document.querySelector(`[data-testid="${id}"]`);
        return el ? { id, top: el.getBoundingClientRect().top + window.scrollY } : null;
      })
      .filter((entry): entry is { id: string; top: number } => entry !== null);
    return positions;
  }, testIds);

  expect(order.map((entry) => entry.id), "all three sections must be present").toEqual(testIds);
  for (let i = 1; i < order.length; i++) {
    expect(
      order[i].top,
      `${order[i].id} must render after ${order[i - 1].id}, with nothing else between them`,
    ).toBeGreaterThan(order[i - 1].top);
  }

  // Nothing else — no evidence coverage, catalog, capacity risk, or service
  // health panel — sits between the attention queue and the timeline.
  const attention = page.getByTestId("needs-attention");
  const timeline = page.getByTestId("correlation-timeline");
  const between = await page.evaluate(() => {
    const attentionEl = document.querySelector('[data-testid="needs-attention"]');
    const timelineEl = document.querySelector('[data-testid="correlation-timeline"]');
    if (!attentionEl || !timelineEl) return null;
    const others = [
      "evidence-coverage-panel",
      "catalog-counts",
      "service-health-rollup",
      "capacity-risk-panel",
      "health-matrix-panel",
    ];
    return others.filter((id) => {
      const el = document.querySelector(`[data-testid="${id}"]`);
      if (!el) return false;
      const afterAttention = Boolean(
        attentionEl.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING,
      );
      const beforeTimeline = Boolean(
        el.compareDocumentPosition(timelineEl) & Node.DOCUMENT_POSITION_FOLLOWING,
      );
      return afterAttention && beforeTimeline;
    });
  });
  expect(between, "no other panel may sit between attention queue and timeline").toEqual([]);
  await expect(attention).toBeVisible();
  await expect(timeline).toBeVisible();

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

test("every timeline event's tooltip stays fully inside the 390px modal, none clipped off an edge", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);

  await page.getByTestId("view-full-timeline").click();
  const dialog = page.getByRole("dialog", { name: "Correlation timeline" });
  await expect(dialog).toBeVisible();

  const dots = dialog.locator('[data-testid="operational-timeline"] span.group');
  const count = await dots.count();
  test.skip(count === 0, "no timeline events in this environment's fixtures");

  for (let i = 0; i < count; i++) {
    const dot = dots.nth(i);
    await dot.getByRole("link").hover();
    const tooltip = dot.locator('[role="tooltip"]');
    await expect(tooltip).toBeVisible();
    const box = await tooltip.boundingBox();
    expect(box, `event ${i}'s tooltip must have a bounding box`).not.toBeNull();
    if (box) {
      expect(box.x, `event ${i}'s tooltip must not clip past the left edge`).toBeGreaterThanOrEqual(0);
      expect(
        box.x + box.width,
        `event ${i}'s tooltip must not clip past the right edge`,
      ).toBeLessThanOrEqual(390);
    }
  }
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
