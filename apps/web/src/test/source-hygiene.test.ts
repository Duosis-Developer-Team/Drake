/**
 * Static guard: no TypeScript source file may contain a literal NUL byte.
 *
 * `HealthMatrix.tsx` and `health-matrix.ts` once had a raw `\x00` sitting
 * inside a template literal (`${a}\x00${b}` typed as what looked like a
 * plain space) — invisible in an editor and in this project's own Read
 * tooling, but enough to make Git treat both files as binary, hiding every
 * future diff behind a "Bin X -> Y bytes" summary instead of a reviewable
 * change. A NUL that is actually wanted as a value belongs in source as the
 * `\u0000` escape, never as a raw byte.
 */
// @vitest-environment node
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

// vitest runs with the package directory as cwd.
const SRC_ROOT = join(process.cwd(), "src");

function collectSourceFiles(dir: string): string[] {
  const results: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const stats = statSync(full);
    if (stats.isDirectory()) {
      results.push(...collectSourceFiles(full));
    } else if (/\.(ts|tsx)$/.test(entry)) {
      results.push(full);
    }
  }
  return results;
}

describe("source hygiene: no literal NUL bytes", () => {
  const files = collectSourceFiles(SRC_ROOT);

  it("scans a meaningful set of source files", () => {
    expect(files.length).toBeGreaterThan(10);
  });

  it("finds no raw NUL byte in any .ts/.tsx source file", () => {
    const violations: string[] = [];
    for (const file of files) {
      const bytes = readFileSync(file);
      if (bytes.includes(0)) violations.push(relative(SRC_ROOT, file));
    }
    expect(violations).toEqual([]);
  });
});
