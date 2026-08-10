import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { validateContent } from "../src/validator.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ONBOARDING = join(__dirname, "..", "onboarding");

/**
 * The manifests under `onboarding/` describe REAL projects, unlike the
 * fictional ones under `fixtures/valid`. Nothing validated them, so a real
 * manifest could drift from the schema and only be caught during an
 * onboarding run against a live catalog — which is the worst place to find
 * out. The directory is scanned rather than listed, so adding a project
 * without validating it is not possible.
 */
function manifestFiles(): string[] {
  return readdirSync(ONBOARDING)
    .filter((name) => name.endsWith(".yaml") || name.endsWith(".yml"))
    .map((name) => join(ONBOARDING, name));
}

describe("real onboarding manifests", () => {
  it("has manifests on disk", () => {
    expect(manifestFiles().length).toBeGreaterThanOrEqual(2);
  });

  for (const file of manifestFiles()) {
    it(`${file.split("/").pop()} validates against drake-project`, () => {
      const result = validateContent(readFileSync(file, "utf8"), "drake-project");
      // Assert on `issues` before `valid`: a failure should print WHAT is
      // wrong with the manifest, not just that something is.
      expect(result.issues).toEqual([]);
      expect(result.valid).toBe(true);
    });
  }
});
