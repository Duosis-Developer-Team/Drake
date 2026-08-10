import { describe, expect, it } from "vitest";

import { validateContent } from "../src/validator.js";

/**
 * Contract behaviour for external runtimes, including the one place where
 * this schema change is NOT purely additive.
 */

const base = (spec: string) => `
apiVersion: drake.duosis.com/v1alpha1
kind: ProjectObservability
metadata: {name: t, displayName: T}
spec:
  repository: {provider: github, owner: o, name: r, defaultBranch: main}
  owners: [{team: platform, role: primary}]
${spec}
  tenantModel: {mode: none}
`;

const valid = (doc: string) => validateContent(doc, "drake-project").valid;

describe("external runtime", () => {
  it("validates with no cluster, no namespace and no metrics profile", () => {
    expect(
      valid(
        base(`  environments: [{name: prod, runtime: external, branch: main, criticality: medium, hostingProvider: vercel}]
  services: [{name: web, component: web, runtime: nextjs}]`),
      ),
    ).toBe(true);
  });

  it("REFUSES a namespace on an external environment", () => {
    // This is the validation TIGHTENING. `namespace` was previously merely
    // optional here, so a document of this exact shape used to validate and
    // no longer does. It is recorded as a test rather than described in
    // prose so the incompatibility cannot be forgotten.
    expect(
      valid(
        base(`  environments: [{name: prod, runtime: external, branch: main, criticality: medium, namespace: invented}]
  services: [{name: web, component: web, runtime: nextjs}]`),
      ),
    ).toBe(false);
  });

  it("REFUSES a clusterRef on an external environment", () => {
    expect(
      valid(
        base(`  environments: [{name: prod, runtime: external, branch: main, criticality: medium, clusterRef: cluster-a}]
  services: [{name: web, component: web, runtime: nextjs}]`),
      ),
    ).toBe(false);
  });

  it("rejects an unknown hosting provider rather than accepting free text", () => {
    expect(
      valid(
        base(`  environments: [{name: prod, runtime: external, branch: main, criticality: medium, hostingProvider: some-startup}]
  services: [{name: web, component: web, runtime: nextjs}]`),
      ),
    ).toBe(false);
  });
});

describe("metricsProfile requiredness", () => {
  const k8sEnv =
    "{name: dev, runtime: kubernetes, branch: main, criticality: medium, clusterRef: cluster-a, namespace: t-dev}";

  it("is required for a Kubernetes project", () => {
    expect(
      valid(base(`  environments: [${k8sEnv}]
  services: [{name: api, component: api, runtime: fastapi}]`)),
    ).toBe(false);
  });

  it("is not required when the project has no Kubernetes environment", () => {
    expect(
      valid(
        base(`  environments: [{name: prod, runtime: external, branch: main, criticality: medium}]
  services: [{name: web, component: web, runtime: nextjs}]`),
      ),
    ).toBe(true);
  });

  it("is required for EVERY service in a mixed-runtime project", () => {
    // Deliberate, and a consequence of the operative domain invariant:
    // services are project-level and every service is expected in every
    // Kubernetes environment (Hermes: 5 services x 2 environments = 10
    // bindings). So in a mixed project every service does have a Kubernetes
    // deployment, and a profile is not a false claim for any of them.
    //
    // Scoping a service to a subset of environments would need a schema
    // field that does not exist; see ADR-0027.
    expect(
      valid(base(`  environments:
    - ${k8sEnv}
    - {name: prod, runtime: external, branch: main, criticality: medium}
  services:
    - {name: api, component: api, runtime: fastapi, metricsProfile: fastapi-v1}
    - {name: site, component: web, runtime: nextjs}`)),
    ).toBe(false);
  });

  it("accepts a mixed-runtime project when every service declares a profile", () => {
    expect(
      valid(base(`  environments:
    - ${k8sEnv}
    - {name: prod, runtime: external, branch: main, criticality: medium}
  services:
    - {name: api, component: api, runtime: fastapi, metricsProfile: fastapi-v1}
    - {name: site, component: web, runtime: nextjs, metricsProfile: nextjs-v1}`)),
    ).toBe(true);
  });
});

describe("managed dependencies", () => {
  const env =
    "  environments: [{name: prod, runtime: external, branch: main, criticality: medium}]\n  services: [{name: web, component: web, runtime: nextjs}]";

  it("does not require a measurement profile for a provider-managed store", () => {
    expect(
      valid(
        base(`${env}
  dataStores: [{name: db, engine: postgresql, scope: project, dependencyClass: managed_data_platform, provider: supabase, verification: repository_intent}]`),
      ),
    ).toBe(true);
  });

  it("still requires one for an in-cluster store, including by default", () => {
    expect(
      valid(`${base(env)}  dataStores: [{name: db, engine: postgresql, scope: project}]`),
    ).toBe(false);
  });

  it("rejects a free-text provider", () => {
    expect(
      valid(
        base(`${env}
  dataStores: [{name: db, engine: postgresql, scope: project, dependencyClass: managed_data_platform, provider: my-own-db}]`),
      ),
    ).toBe(false);
  });
});
