import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { parse } from "yaml";
import { describe, expect, it } from "vitest";

import { validateContent } from "../src/validator.js";

/**
 * The Fikir Sepeti manifest, as a contract.
 *
 * `onboarding-manifests.test.ts` already proves every real manifest
 * validates. This proves the things that make THIS one different — the
 * absences. A manifest that validates is not the same as a manifest that
 * tells the truth, and every assertion below is a fact somebody could
 * "helpfully" add later: a namespace, a metrics profile, a health path, a
 * connection reference.
 */

const __dirname = dirname(fileURLToPath(import.meta.url));
const PATH = join(__dirname, "..", "onboarding", "fikir-sepeti.project.yaml");
const RAW = readFileSync(PATH, "utf8");
const DOC = parse(RAW);

/** Only as much shape as the mutations below need. */
interface ManifestDoc {
  spec: {
    environments: Record<string, unknown>[];
    services: Record<string, unknown>[];
    dataStores: Record<string, unknown>[];
    tenantModel: { mode: string };
  };
}

const doc = DOC as ManifestDoc;

const mutated = (edit: (copy: ManifestDoc) => void) => {
  const copy = JSON.parse(JSON.stringify(DOC)) as ManifestDoc;
  edit(copy);
  return validateContent(JSON.stringify(copy), "drake-project").valid;
};

describe("fikir-sepeti manifest", () => {
  it("validates", () => {
    const result = validateContent(RAW, "drake-project");
    expect(result.issues).toEqual([]);
    expect(result.valid).toBe(true);
  });

  it("describes one external environment hosted on Vercel", () => {
    expect(doc.spec.environments).toHaveLength(1);
    expect(doc.spec.environments[0]).toMatchObject({
      name: "prod",
      runtime: "external",
      branch: "main",
      hostingProvider: "vercel",
    });
  });

  it("declares one managed Supabase dependency at repository intent", () => {
    expect(doc.spec.dataStores).toHaveLength(1);
    expect(doc.spec.dataStores[0]).toMatchObject({
      engine: "postgresql",
      dependencyClass: "managed_data_platform",
      provider: "supabase",
      verification: "repository_intent",
    });
  });

  it("records the tenant model the migrations establish", () => {
    expect(doc.spec.tenantModel.mode).toBe("shared_table");
  });

  it("would be REFUSED if a cluster or namespace were added to it", () => {
    // Not a property of this document — a property of the schema, asserted
    // against this document, because this is the manifest somebody would
    // edit while wondering where the cluster field went.
    expect(mutated((d) => (d.spec.environments[0].namespace = "fikir-sepeti-prod"))).toBe(
      false,
    );
    expect(mutated((d) => (d.spec.environments[0].clusterRef = "duosis-prod-1"))).toBe(false);
  });

  it("names no metrics profile, no health path and no workload selector", () => {
    const [service] = doc.spec.services;
    expect(service.metricsProfile).toBeUndefined();
    expect(service.health).toBeUndefined();
    expect(service.workloadSelector).toBeUndefined();
  });

  it("stays valid without a metrics profile because it has no Kubernetes environment", () => {
    // The document-level conditional requires `metricsProfile` only when a
    // Kubernetes environment exists. Adding one must therefore make this
    // manifest INVALID rather than silently accepting a service nothing
    // scrapes into a cluster.
    expect(
      mutated((d) =>
        d.spec.environments.push({
          name: "dev",
          runtime: "kubernetes",
          branch: "main",
          criticality: "low",
          clusterRef: "duosis-prod-1",
          namespace: "fikir-sepeti-dev",
        }),
      ),
    ).toBe(false);
  });

  it("carries no connection reference and nothing secret-shaped", () => {
    expect(doc.spec.dataStores[0].connectionSecretRef).toBeUndefined();
    const lowered = RAW.toLowerCase();
    for (const token of ["://", "supabase.co", "service_role", "anon_key", "eyj", "sb_secret"]) {
      expect(lowered).not.toContain(token);
    }
  });
});
