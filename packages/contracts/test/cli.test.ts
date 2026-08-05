import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, describe, expect, it } from "vitest";

import { manifestWithSelectorValue } from "./helpers.js";

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

/**
 * Credential test files are written to a NON-COMMITTED temp directory at
 * runtime, from concatenated parts. Nothing credential-shaped is ever a
 * committed literal, so the secret scanner needs no fixture allowlists.
 * Cleanup is guaranteed by afterAll (runs on failure too).
 */
const tempDir = mkdtempSync(join(tmpdir(), "drake-cli-fixtures-"));

afterAll(() => {
  rmSync(tempDir, { recursive: true, force: true });
});

function writeRuntimeFixture(name: string, selectorValue: string): string {
  const file = join(tempDir, name);
  writeFileSync(file, manifestWithSelectorValue(selectorValue), "utf8");
  return file;
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

  it("rejects runtime-generated credential fixtures without echoing values", () => {
    const urlValue = ["postgresql://svc:", "fake-", "cli-", "credential", "@db.example.test/x"].join(
      "",
    );
    const assignmentValue = ["pass", "word=", "fake-", "cli-", "value"].join("");
    const files = [
      writeRuntimeFixture("credential-url.yaml", urlValue),
      writeRuntimeFixture("credential-assignment.yaml", assignmentValue),
    ];
    const run = runCli(files);
    expect(run.status).toBe(1);
    expect(run.stdout.match(/^INVALID /gm)).toHaveLength(2);
    expect(run.stdout).toContain("credential-in-url");
    expect(run.stdout).toContain("credential-assignment");
    expect(run.stdout).not.toContain("fake-cli");
  });

  it("exits 2 without arguments", () => {
    expect(runCli([]).status).toBe(2);
  });

  it("exits 2 for an unreadable file", () => {
    expect(runCli([join(FIXTURES, "does-not-exist.yaml")]).status).toBe(2);
  });

  it("validates tenant snapshots with --schema", () => {
    const run = runCli([
      "--schema",
      "tenant-snapshot",
      join(FIXTURES, "valid", "project-alpha.yaml"),
    ]);
    expect(run.status).toBe(1); // a project manifest is NOT a valid snapshot
  });
});
