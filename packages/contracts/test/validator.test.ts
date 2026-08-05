import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { validateContent } from "../src/validator.js";

const FIXTURES = join(__dirname, "..", "fixtures");

function fixtureFiles(kind: "valid" | "invalid"): string[] {
  return readdirSync(join(FIXTURES, kind))
    .filter((name) => name.endsWith(".yaml"))
    .map((name) => join(FIXTURES, kind, name));
}

describe("project manifest fixtures", () => {
  it("has fixtures on disk", () => {
    expect(fixtureFiles("valid").length).toBeGreaterThanOrEqual(4);
    expect(fixtureFiles("invalid").length).toBeGreaterThanOrEqual(8);
  });

  for (const file of fixtureFiles("valid")) {
    it(`accepts ${file.split("/").at(-1)}`, () => {
      const result = validateContent(readFileSync(file, "utf8"), "drake-project");
      expect(result.issues).toEqual([]);
      expect(result.valid).toBe(true);
    });
  }

  for (const file of fixtureFiles("invalid")) {
    it(`rejects ${file.split("/").at(-1)}`, () => {
      const result = validateContent(readFileSync(file, "utf8"), "drake-project");
      expect(result.valid).toBe(false);
      expect(result.issues.length).toBeGreaterThan(0);
    });
  }
});

describe("rejection reasons are specific", () => {
  function issuesFor(name: string) {
    const content = readFileSync(join(FIXTURES, "invalid", name), "utf8");
    return validateContent(content, "drake-project").issues;
  }

  it("flags unknown fields via additionalProperties", () => {
    const issues = issuesFor("unknown-field.yaml");
    expect(issues.some((i) => i.rule === "schema" && i.message.includes("autoRemediation"))).toBe(
      true,
    );
  });

  it("flags credential URLs with the credential-in-url rule", () => {
    expect(issuesFor("credential-url.yaml").some((i) => i.rule === "credential-in-url")).toBe(true);
  });

  it("flags credential assignments", () => {
    expect(
      issuesFor("credential-assignment.yaml").some((i) => i.rule === "credential-assignment"),
    ).toBe(true);
  });

  it("flags private key material", () => {
    expect(issuesFor("private-key.yaml").some((i) => i.rule === "private-key-material")).toBe(true);
  });

  it("flags inline SQL", () => {
    expect(issuesFor("inline-sql.yaml").some((i) => i.rule === "inline-sql")).toBe(true);
  });

  it("flags plaintext endpoints", () => {
    expect(issuesFor("insecure-config.yaml").some((i) => i.rule === "plaintext-endpoint")).toBe(
      true,
    );
  });

  it("never echoes the offending value in messages", () => {
    for (const name of [
      "credential-url.yaml",
      "credential-assignment.yaml",
      "private-key.yaml",
    ]) {
      for (const issue of issuesFor(name)) {
        expect(issue.message).not.toContain("fake-test");
        expect(issue.message).not.toContain("FAKEFIXTURE");
      }
    }
  });
});

describe("secret references are not false positives", () => {
  it("accepts connectionSecretRef names containing the word secret-adjacent terms", () => {
    const manifest = readFileSync(join(FIXTURES, "valid", "project-alpha.yaml"), "utf8");
    const result = validateContent(manifest, "drake-project");
    expect(result.valid).toBe(true);
  });
});

describe("tenant snapshot schema", () => {
  const validSnapshot = {
    schema_version: "1.0",
    snapshot_id: "3f1a35b8-3f6a-4a6e-9f60-0a1b2c3d4e5f",
    generated_at: "2026-08-06T00:00:00Z",
    complete: true,
    next_cursor: null,
    tenants: [
      {
        tenant_key: "t-001",
        display_name: null,
        status: "active",
        plan_key: "basic",
        entitlements: [{ dimension: "users.active", unit: "count", included: 10, extra: 0, enforcement: "warn" }],
        usage: [
          {
            dimension: "users.active",
            value: 7,
            unit: "count",
            window_start: "2026-08-05T00:00:00Z",
            window_end: "2026-08-06T00:00:00Z",
            source: "adapter",
          },
        ],
        storage: [
          {
            store_key: "postgres-main",
            bytes: 1024,
            rows: 42,
            method: "logical_rollup_exact",
            confidence: "exact",
            as_of: "2026-08-06T00:00:00Z",
          },
        ],
        quality: ["complete"],
      },
    ],
  };

  it("accepts a well-formed snapshot", () => {
    const result = validateContent(JSON.stringify(validSnapshot), "tenant-snapshot");
    expect(result.issues).toEqual([]);
  });

  it("rejects an unknown storage method", () => {
    const bad = structuredClone(validSnapshot);
    bad.tenants[0].storage[0].method = "guessed";
    const result = validateContent(JSON.stringify(bad), "tenant-snapshot");
    expect(result.valid).toBe(false);
  });

  it("rejects a snapshot without quality flags", () => {
    const bad = structuredClone(validSnapshot) as Record<string, unknown>;
    delete (bad.tenants as Record<string, unknown>[])[0].quality;
    const result = validateContent(JSON.stringify(bad), "tenant-snapshot");
    expect(result.valid).toBe(false);
  });
});

describe("backup event schema", () => {
  const validEvent = {
    specversion: "1.0",
    type: "drake.backup.run.completed.v1",
    source: "project/beta/prod",
    id: "6c9e2a44-0b7e-4b7a-8a54-2f6d7e8a9b0c",
    time: "2026-08-06T01:00:00Z",
    subject: "datastore/postgres-main",
    data: {
      policy_key: "nightly-postgres",
      status: "succeeded",
      started_at: "2026-08-06T00:00:00Z",
      finished_at: "2026-08-06T00:10:00Z",
      artifact_key: "backups/2026-08-06/postgres-main",
      size_bytes: 1048576,
      checksum_algorithm: "sha256",
      checksum_verified: true,
      storage_site: "site-b",
      offsite: true,
      error_code: null,
      restore_validation: null,
    },
  };

  it("accepts a well-formed completed event", () => {
    const result = validateContent(JSON.stringify(validEvent), "backup-event");
    expect(result.issues).toEqual([]);
  });

  it("rejects an unknown event type", () => {
    const bad = { ...validEvent, type: "drake.backup.run.maybe.v1" };
    const result = validateContent(JSON.stringify(bad), "backup-event");
    expect(result.valid).toBe(false);
  });

  it("rejects extra fields in data", () => {
    const bad = structuredClone(validEvent) as { data: Record<string, unknown> };
    bad.data.connection_string = "ref-name-only";
    const result = validateContent(JSON.stringify(bad), "backup-event");
    expect(result.valid).toBe(false);
  });
});
