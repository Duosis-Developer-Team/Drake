import { describe, expect, it } from "vitest";

import { validateContent } from "../src/validator.js";

/**
 * Two `owners` entries that mean the same association.
 *
 * Identity is (team, role), and an omitted role means `primary`. JSON
 * Schema `uniqueItems` cannot express that: `{team: t}` and
 * `{team: t, role: primary}` are structurally different objects with one
 * meaning, so it sees two unique items and lets them through.
 *
 * Downstream they became two plan items with one identity — and
 * `onboarding_plan_items` is `UNIQUE(plan_id, item_key)`, so one was
 * dropped and the plan stopped matching the digest it was approved under.
 */

const manifest = (owners: string) => `
apiVersion: drake.duosis.com/v1alpha1
kind: ProjectObservability
metadata: {name: t, displayName: T}
spec:
  repository: {provider: github, owner: o, name: r, defaultBranch: main}
  owners:
${owners}
  environments: [{name: prod, runtime: external, branch: main, criticality: medium}]
  services: [{name: web, component: web, runtime: nextjs}]
  tenantModel: {mode: none}
`;

const result = (owners: string) => validateContent(manifest(owners), "drake-project");

describe("owner consistency", () => {
  it("allows the same team in two different roles", () => {
    const outcome = result(
      "    - {team: alpha-team, role: primary}\n    - {team: alpha-team, role: secondary}\n",
    );
    expect(outcome.issues).toEqual([]);
    expect(outcome.valid).toBe(true);
  });

  it("REFUSES an omitted role beside an explicit primary", () => {
    // The case uniqueItems cannot catch.
    const outcome = result("    - {team: alpha-team}\n    - {team: alpha-team, role: primary}\n");
    expect(outcome.valid).toBe(false);
    expect(outcome.issues.map((i) => i.rule)).toContain("owner-duplicate");
    expect(outcome.issues.find((i) => i.rule === "owner-duplicate")?.path).toBe("spec.owners[1]");
  });

  it("REFUSES an exact duplicate", () => {
    const outcome = result(
      "    - {team: alpha-team, role: primary}\n    - {team: alpha-team, role: primary}\n",
    );
    expect(outcome.valid).toBe(false);
    expect(outcome.issues.map((i) => i.rule)).toContain("owner-duplicate");
  });

  it("REFUSES two entries that both omit the role", () => {
    const outcome = result("    - {team: alpha-team}\n    - {team: alpha-team}\n");
    expect(outcome.valid).toBe(false);
    expect(outcome.issues.map((i) => i.rule)).toContain("owner-duplicate");
  });

  it("allows different teams", () => {
    const outcome = result("    - {team: alpha-team}\n    - {team: beta-team}\n");
    expect(outcome.issues).toEqual([]);
  });

  it("never echoes the team name in the finding", () => {
    // Findings are rendered in the UI and written to audit metadata.
    const outcome = result("    - {team: alpha-team}\n    - {team: alpha-team, role: primary}\n");
    const finding = outcome.issues.find((i) => i.rule === "owner-duplicate");
    expect(finding?.message).not.toContain("alpha-team");
  });
});
