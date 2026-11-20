/**
 * Brand guardrails.
 *
 * The Tracery logo is inline SVG drawn from theme tokens (Brand.tsx), so the
 * runtime ships only the app icon in three forms. A stray master or an old
 * derivative dropped into public/brand fails here before it ships.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const WEB_ROOT = existsSync(join(process.cwd(), "public", "brand"))
  ? process.cwd()
  : join(process.cwd(), "apps", "web");
const RUNTIME_BRAND_DIR = join(WEB_ROOT, "public", "brand");

const EXPECTED = ["tracery-icon.svg", "tracery-favicon-32.png", "tracery-apple-touch.png"];
const PER_FILE_BUDGET_BYTES = 8 * 1024;

describe("brand assets", () => {
  it("ships exactly the app icons the layout references", () => {
    expect([...readdirSync(RUNTIME_BRAND_DIR)].sort()).toEqual([...EXPECTED].sort());
    const layout = readFileSync(join(WEB_ROOT, "src", "app", "layout.tsx"), "utf8");
    for (const file of EXPECTED) expect(layout, `${file} is not referenced`).toContain(file);
  });

  it("keeps every icon inside budget", () => {
    for (const file of EXPECTED) {
      expect(statSync(join(RUNTIME_BRAND_DIR, file)).size, file).toBeLessThanOrEqual(
        PER_FILE_BUDGET_BYTES,
      );
    }
  });

  it("draws the logo from theme tokens, not from fixed colours or images", () => {
    const source = readFileSync(join(WEB_ROOT, "src", "components", "shell", "Brand.tsx"), "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code).not.toMatch(/<img/);
    expect(code).not.toMatch(/#[0-9a-fA-F]{6}/);
    expect(code).toContain("var(--brand)");
    expect(code).toContain("var(--gold)");
  });
});
