import { execFileSync } from "node:child_process";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const CLI = join(__dirname, "..", "dist", "cli.js");
const FIXTURES = join(__dirname, "..", "fixtures");

interface CliRun {
  status: number;
  stdout: string;
}

function runCli(args: string[]): CliRun {
  try {
    const stdout = execFileSync(process.execPath, [CLI, ...args], { encoding: "utf8" });
    return { status: 0, stdout };
  } catch (error) {
    const failure = error as { status: number | null; stdout: string };
    return { status: failure.status ?? -1, stdout: failure.stdout ?? "" };
  }
}

describe("drake-validate CLI", () => {
  it("exits 0 for all valid fixtures", () => {
    const files = ["project-alpha", "project-beta", "project-gamma", "project-delta"].map((n) =>
      join(FIXTURES, "valid", `${n}.yaml`),
    );
    const run = runCli(files);
    expect(run.status).toBe(0);
    expect(run.stdout.match(/^OK /gm)).toHaveLength(4);
  });

  it("exits 1 for an invalid fixture and prints readable reasons", () => {
    const run = runCli([join(FIXTURES, "invalid", "unknown-field.yaml")]);
    expect(run.status).toBe(1);
    expect(run.stdout).toContain("INVALID");
    expect(run.stdout).toContain("autoRemediation");
  });

  it("never prints the fake credential value from fixtures", () => {
    const run = runCli([
      join(FIXTURES, "invalid", "credential-url.yaml"),
      join(FIXTURES, "invalid", "credential-assignment.yaml"),
    ]);
    expect(run.status).toBe(1);
    expect(run.stdout).not.toContain("fake-test-password-not-real");
    expect(run.stdout).not.toContain("fake-test-value-not-real");
  });

  it("exits 2 without arguments", () => {
    expect(runCli([]).status).toBe(2);
  });

  it("exits 2 for an unreadable file", () => {
    expect(runCli([join(FIXTURES, "does-not-exist.yaml")]).status).toBe(2);
  });

  it("validates tenant snapshots with --schema", () => {
    const run = runCli(["--schema", "tenant-snapshot", join(FIXTURES, "valid", "project-alpha.yaml")]);
    expect(run.status).toBe(1); // a project manifest is NOT a valid snapshot
  });
});
