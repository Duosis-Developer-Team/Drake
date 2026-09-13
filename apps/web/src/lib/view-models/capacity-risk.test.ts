import { describe, expect, it } from "vitest";

import { certificateRiskItems, pvcRiskItems } from "@/lib/view-models/capacity-risk";
import type { Cluster } from "@/lib/catalog";
import type { InventorySummary } from "@/lib/inventory";

function cluster(id: string, name: string): Cluster {
  return {
    id,
    cluster_ref: `ref-${id}`,
    display_name: name,
    site: "site-1",
    lifecycle: "active",
    version: 1,
    scope: { type: "cluster", ref: `ref-${id}` },
    source: { kind: "operator", ref: "operator:-", revision: "v1", accepted_at: "2026-08-11T00:00:00Z" },
    operational: { agent: "connected", inventory: "fresh" },
    as_of: "2026-08-11T00:00:00Z",
  } as Cluster;
}

function summary(overrides: Partial<InventorySummary> = {}): InventorySummary {
  return {
    cluster_id: "c1",
    agent: { status: "connected", certificate_not_after: null, certificate_expiry_warning: false },
    inventory: { state: "fresh" },
    nodes: { total: 0, healthy: 0, degraded: 0, unhealthy: 0, unknown: 0 },
    namespaces: { total: 0, healthy: 0, degraded: 0, unhealthy: 0, unknown: 0 },
    pods: { total: 0, healthy: 0, degraded: 0, unhealthy: 0, unknown: 0, crashloop: 0, oom_killed: 0, restarts: 0 },
    workloads: { total: 0, healthy: 0, degraded: 0, unhealthy: 0, unknown: 0 },
    persistent_volume_claims: { total: 9, healthy: 9, degraded: 0, unhealthy: 0, unknown: 0 },
    by_kind: {},
    as_of: "2026-08-11T00:00:00Z",
    ...overrides,
  } as InventorySummary;
}

describe("certificateRiskItems", () => {
  it("surfaces a cluster only when certificate_expiry_warning is true", () => {
    const clusters = [cluster("c1", "prod-1"), cluster("c2", "prod-2")];
    const summaries = new Map([
      ["c1", summary({ agent: { status: "connected", certificate_not_after: "2026-09-01T00:00:00Z", certificate_expiry_warning: true } })],
      ["c2", summary()],
    ]);
    const items = certificateRiskItems(clusters, summaries);
    expect(items).toHaveLength(1);
    expect(items[0].clusterId).toBe("c1");
    expect(items[0].deadline).toBe("2026-09-01T00:00:00Z");
  });

  it("a cluster with no summary produces no certificate item", () => {
    const items = certificateRiskItems([cluster("c1", "prod-1")], new Map());
    expect(items).toEqual([]);
  });
});

describe("pvcRiskItems", () => {
  it("surfaces a cluster only when degraded + unhealthy > 0", () => {
    const clusters = [cluster("c1", "prod-1"), cluster("c2", "prod-2")];
    const summaries = new Map([
      ["c1", summary({ persistent_volume_claims: { total: 9, healthy: 7, degraded: 1, unhealthy: 1, unknown: 0 } })],
      ["c2", summary()],
    ]);
    const items = pvcRiskItems(clusters, summaries);
    expect(items).toHaveLength(1);
    expect(items[0].clusterId).toBe("c1");
    expect(items[0].tone).toBe("critical");
  });

  it("tone is warning, not critical, when only degraded (no unhealthy)", () => {
    const clusters = [cluster("c1", "prod-1")];
    const summaries = new Map([
      ["c1", summary({ persistent_volume_claims: { total: 9, healthy: 7, degraded: 2, unhealthy: 0, unknown: 0 } })],
    ]);
    const items = pvcRiskItems(clusters, summaries);
    expect(items[0].tone).toBe("warning");
  });

  it("a cluster with no summary produces no PVC item", () => {
    const items = pvcRiskItems([cluster("c1", "prod-1")], new Map());
    expect(items).toEqual([]);
  });
});
