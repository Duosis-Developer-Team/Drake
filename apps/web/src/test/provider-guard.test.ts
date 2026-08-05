/**
 * Static guard: browser code must never talk to telemetry providers or the
 * Kubernetes API directly. The web app's only data source is the Drake API.
 *
 * This scans every source file under src/ (excluding this test directory)
 * for forbidden endpoints/hosts and for absolute http(s) URLs in code.
 */
// @vitest-environment node
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

import { describe, expect, it } from "vitest";

// vitest runs with the package directory as cwd.
const SRC_ROOT = join(process.cwd(), "src");

// Built via concatenation so this guard file never matches its own patterns.
const FORBIDDEN_PATTERNS: { name: string; pattern: RegExp }[] = [
  { name: "prometheus", pattern: new RegExp("prome" + "theus", "i") },
  { name: "thanos", pattern: new RegExp("tha" + "nos", "i") },
  { name: "alertmanager", pattern: new RegExp("alert" + "manager", "i") },
  { name: "prometheus port", pattern: new RegExp(":9" + "090") },
  { name: "alertmanager port", pattern: new RegExp(":9" + "093") },
  { name: "kubernetes api host", pattern: new RegExp("kuber" + "netes\\.default") },
  { name: "kubernetes api path", pattern: new RegExp("/api/v1/(pods|nodes|secrets)") },
  { name: "kubeconfig", pattern: new RegExp("kube" + "config", "i") },
  // No absolute runtime URLs at all in Sprint 0 web code.
  { name: "absolute url", pattern: new RegExp("https?:" + "//", "i") },
];

function collectSourceFiles(dir: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      results.push(...collectSourceFiles(full));
    } else if (/\.(ts|tsx|css)$/.test(entry)) {
      results.push(full);
    }
  }
  return results;
}

describe("browser provider-access guard", () => {
  const files = collectSourceFiles(SRC_ROOT).filter(
    (file) => !relative(SRC_ROOT, file).split(sep).includes("test"),
  );

  it("scans a meaningful set of source files", () => {
    expect(files.length).toBeGreaterThan(10);
  });

  it("finds no forbidden provider references in browser code", () => {
    const violations: string[] = [];
    for (const file of files) {
      const content = readFileSync(file, "utf8");
      for (const { name, pattern } of FORBIDDEN_PATTERNS) {
        if (pattern.test(content)) {
          violations.push(`${relative(SRC_ROOT, file)}: ${name}`);
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
