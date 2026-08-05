import { describe, expect, it } from "vitest";

import { checkPolicy } from "../src/policy.js";

describe("policy engine", () => {
  it("returns no findings for a clean document", () => {
    expect(
      checkPolicy({
        metadata: { name: "alpha" },
        spec: { dataStores: [{ connectionSecretRef: "alpha-monitoring" }] },
      }),
    ).toEqual([]);
  });

  it("distinguishes credential references from credential values", () => {
    // Reference name — allowed even though the FIELD mentions "Secret".
    expect(checkPolicy({ connectionSecretRef: "team-a-secret-store-entry" })).toEqual([]);
    // Value — rejected.
    const findings = checkPolicy({ anything: "password=not-a-real-one" });
    expect(findings.map((f) => f.rule)).toContain("credential-assignment");
  });

  it("reports the JSON path, not the value", () => {
    const findings = checkPolicy({
      spec: { services: [{ workloadSelector: { conn: "https://u:fakepw@db.example.test/x" } }] },
    });
    expect(findings).toHaveLength(1);
    expect(findings[0].path).toBe("spec.services[0].workloadSelector.conn");
    expect(JSON.stringify(findings[0])).not.toContain("fakepw");
  });

  it("flags insecure transport flags by field name", () => {
    const findings = checkPolicy({ tls: { insecure_skip_verify: true } });
    expect(findings.map((f) => f.rule)).toContain("insecure-flag");
  });

  it("flags plaintext http endpoints but allows https", () => {
    expect(checkPolicy({ endpoint: "https://collector.example.test" })).toEqual([]);
    expect(checkPolicy({ endpoint: "http://collector.example.test" }).map((f) => f.rule)).toContain(
      "plaintext-endpoint",
    );
  });

  it("flags bearer tokens and cloud key ids", () => {
    expect(
      checkPolicy({ header: "Bearer abcdefghijklmnopqrstuvwxyz0123456789" }).map((f) => f.rule),
    ).toContain("bearer-token");
    expect(checkPolicy({ keyId: "AKIA" + "ABCDEFGHIJ" + "KLMNOP" }).map((f) => f.rule)).toContain(
      "cloud-access-key-id",
    );
  });

  it("does not flag prose that merely mentions the word secret", () => {
    expect(
      checkPolicy({ description: "Rotation of the monitoring secret is documented in the runbook" }),
    ).toEqual([]);
  });
});
