/**
 * Brand asset guardrails.
 *
 * The official masters live in apps/web/assets/brand and must never ship from
 * the public runtime path — only the derivatives built by
 * scripts/build_brand_assets.py may. A future full-res drop into public/brand
 * fails here before it fails users.
 *
 * The budget is expressed per THEME rather than as one total, because the
 * shell paints the logo as a CSS background: a browser fetches the light
 * wordmark or the dark one, never both. Summing all six files would measure a
 * transfer that never happens.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// Vitest's root is apps/web; fall back to the monorepo-root cwd just in case.
const WEB_ROOT = existsSync(join(process.cwd(), "public", "brand"))
  ? process.cwd()
  : join(process.cwd(), "apps", "web");
const RUNTIME_BRAND_DIR = join(WEB_ROOT, "public", "brand");

const PER_FILE_BUDGET_BYTES = 24 * 1024;
const PER_THEME_BUDGET_BYTES = 40 * 1024;

const EXPECTED = [
  "drake-wordmark-light.webp",
  "drake-wordmark-dark.webp",
  "drake-mark-light.webp",
  "drake-mark-dark.webp",
  "drake-favicon-light.png",
  "drake-favicon-dark.png",
];

function sizeOf(file: string): number {
  return statSync(join(RUNTIME_BRAND_DIR, file)).size;
}

describe("brand asset budget", () => {
  it("ships exactly the derivatives the shell references", () => {
    const files = readdirSync(RUNTIME_BRAND_DIR).filter(
      (file) => file.endsWith(".webp") || file.endsWith(".png"),
    );
    expect([...files].sort()).toEqual([...EXPECTED].sort());
  });

  it("keeps every derivative, and each theme's set, inside budget", () => {
    for (const file of EXPECTED) {
      expect(sizeOf(file), `${file} exceeds the per-file budget`).toBeLessThanOrEqual(
        PER_FILE_BUDGET_BYTES,
      );
    }
    for (const theme of ["light", "dark"]) {
      const total = EXPECTED.filter((file) => file.includes(theme)).reduce(
        (sum, file) => sum + sizeOf(file),
        0,
      );
      expect(total, `${theme} theme brand payload`).toBeLessThanOrEqual(
        PER_THEME_BUDGET_BYTES,
      );
    }
  });

  it("the shell paints the logo as a background, with a fixed box", () => {
    const source = readFileSync(
      join(WEB_ROOT, "src", "components", "shell", "Brand.tsx"),
      "utf8",
    );
    // Its own prose explains why there is no <img>, so match on code only.
    const brand = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

    // Background-image, not <img>: a hidden <img> is still fetched, so the
    // dark-theme wordmark would download on every light-theme page load.
    expect(brand).not.toMatch(/<img/);
    const referenced = [...brand.matchAll(/\/brand\/([\w.-]+)/g)].map((match) => match[1]);
    expect(referenced.length).toBeGreaterThan(0);
    for (const file of referenced) {
      expect(EXPECTED, `${file} is referenced but not built`).toContain(file);
    }

    // Explicit width and height, so the shell never reflows on brand load.
    expect(brand).toMatch(/style=\{\{ height, width:/);
  });

  it("the favicons are declared per colour scheme", () => {
    // One favicon cannot work on both tab strips: the light lockup is deep
    // green and disappears on a dark one, and the dark lockup is near-white.
    const layout = readFileSync(join(WEB_ROOT, "src", "app", "layout.tsx"), "utf8");
    expect(layout).toMatch(/drake-favicon-light\.png/);
    expect(layout).toMatch(/drake-favicon-dark\.png/);
    expect(layout).toMatch(/prefers-color-scheme: light/);
    expect(layout).toMatch(/prefers-color-scheme: dark/);
  });
});
