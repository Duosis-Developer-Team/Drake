/**
 * Brand asset transfer budget (Sprint 2 closure).
 *
 * The official masters live in apps/web/assets/brand and must never ship
 * from the public runtime path — only small 2× derivatives may. A future
 * full-res drop into public/brand fails this test before it fails users.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// Vitest's root is apps/web; fall back to the monorepo-root cwd just in case.
const WEB_ROOT = existsSync(join(process.cwd(), "public", "brand"))
  ? process.cwd()
  : join(process.cwd(), "apps", "web");
const RUNTIME_BRAND_DIR = join(WEB_ROOT, "public", "brand");
const PER_FILE_BUDGET_BYTES = 16 * 1024;
const TOTAL_BUDGET_BYTES = 32 * 1024;

describe("brand asset budget", () => {
  it("ships only optimized derivatives from the runtime path", () => {
    const files = readdirSync(RUNTIME_BRAND_DIR).filter((f) => f.endsWith(".png"));
    expect(files.length).toBeGreaterThan(0);
    let total = 0;
    for (const file of files) {
      const size = statSync(join(RUNTIME_BRAND_DIR, file)).size;
      expect(size, `${file} exceeds the per-file budget`).toBeLessThanOrEqual(
        PER_FILE_BUDGET_BYTES,
      );
      total += size;
    }
    expect(total, "total runtime brand payload").toBeLessThanOrEqual(TOTAL_BUDGET_BYTES);
  });

  it("sidebar references existing derivatives with explicit dimensions", () => {
    const sidebar = readFileSync(
      join(WEB_ROOT, "src", "components", "shell", "Sidebar.tsx"),
      "utf8",
    );
    const sources = [...sidebar.matchAll(/src="\/brand\/([^"]+)"/g)].map((m) => m[1]);
    expect(sources.length).toBeGreaterThan(0);
    for (const source of sources) {
      // Every referenced asset exists in the runtime path (and therefore
      // passed the budget above).
      expect(() => statSync(join(RUNTIME_BRAND_DIR, source))).not.toThrow();
    }
    // Explicit dimensions so the shell never layout-shifts on brand load.
    expect(sidebar).toMatch(/width=\{32\}/);
    expect(sidebar).toMatch(/height=\{32\}/);
  });
});
