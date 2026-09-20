import { describe, expect, it } from "vitest";

import type { Environment } from "@/lib/catalog";
import type { ServiceHealthRow, ServiceHealthStatus } from "@/lib/serviceHealth";
import { buildProjectTopology, buildServiceLane } from "@/lib/view-models/scope-health";

function environment(id: string, key: string, overrides: Partial<Environment> = {}): Environment {
  return {
    id,
    environment_key: key,
    runtime: "kubernetes",
    branch: "main",
    criticality: "medium",
    namespace: "default",
    lifecycle: "active",
    cluster: { ref: "cluster-a", display_name: "Cluster A" },
    hosting_provider: null,
    version: 1,
    scope: { type: "environment", ref: id },
    source: {
      kind: "manifest",
      ref: "github:acme/repo",
      revision: "abc123",
      accepted_at: "2026-01-01T00:00:00Z",
    },
    as_of: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

let seq = 0;
function serviceRow(
  environmentId: string,
  status: ServiceHealthStatus,
  opts: {
    partial?: boolean;
    binding?: ServiceHealthRow["binding"];
    readyReplicas?: number | null;
    desiredReplicas?: number | null;
  } = {},
): ServiceHealthRow {
  seq += 1;
  return {
    environment_service_id: `es-${seq}`,
    project_id: "p1",
    project_key: "alpha",
    environment_id: environmentId,
    environment_key: "prod",
    service_key: `svc-${seq}`,
    display_name: null,
    component: null,
    binding: opts.binding ?? null,
    health: {
      status,
      computed_at: "2026-01-01T00:00:00Z",
      newest_sample_at: null,
      freshness_age_seconds: 30,
      partial: opts.partial ?? false,
      served_from_last_good: false,
      reasons: [],
      availability: {
        ready_replicas: opts.readyReplicas ?? null,
        desired_replicas: opts.desiredReplicas ?? null,
      },
      stability: {},
      resources: {},
    },
  };
}

describe("buildServiceLane", () => {
  it("carries the row's own project and environment id into the href, independent of any caller context", () => {
    const row = serviceRow("e1", "healthy");
    const lane = buildServiceLane(row);
    expect(lane.href).toBe(`/projects/p1/environments/e1/services/${row.environment_service_id}`);
    expect(lane.tone).toBe("success");
    expect(lane.statusLabel).toBe("Healthy");
  });

  it("retains an unbound service rather than dropping it", () => {
    const row = serviceRow("e1", "not_configured", { binding: null });
    const lane = buildServiceLane(row);
    expect(lane.binding).toBeNull();
    expect(lane.tone).toBe("not-applicable");
  });

  it("preserves a partial, stale reading rather than upgrading it", () => {
    const row = serviceRow("e1", "stale", { partial: true });
    const lane = buildServiceLane(row);
    expect(lane.tone).toBe("stale");
    expect(lane.statusLabel).toBe("Stale");
    expect(lane.partial).toBe(true);
  });
});

describe("buildProjectTopology", () => {
  it("orders services worst-first regardless of input order", () => {
    const kube = environment("e1", "prod");
    const rows = [
      serviceRow("e1", "healthy"),
      serviceRow("e1", "critical"),
      serviceRow("e1", "stale"),
    ];
    const topology = buildProjectTopology("p1", [kube], rows, true);
    expect(topology[0].services.map((service) => service.tone)).toEqual([
      "critical",
      "stale",
      "success",
    ]);
  });

  it("keeps an unbound service in the lane instead of hiding it", () => {
    const kube = environment("e1", "prod");
    const rows = [serviceRow("e1", "not_configured", { binding: null })];
    const topology = buildProjectTopology("p1", [kube], rows, true);
    expect(topology[0].services).toHaveLength(1);
    expect(topology[0].services[0].binding).toBeNull();
  });

  it("marks an external runtime environment not-applicable, never a health tone", () => {
    const external = environment("e2", "stage", { runtime: "external", cluster: null });
    const topology = buildProjectTopology("p1", [external], [], true);
    expect(topology[0]).toMatchObject({
      runtime: "external",
      placement: "Not applicable",
      tone: "not-applicable",
      evidence: "not-applicable",
    });
  });

  it("reads a missing Kubernetes cluster placement as unknown, not blank", () => {
    const kube = environment("e3", "qa", { cluster: null });
    const topology = buildProjectTopology("p1", [kube], [], true);
    expect(topology[0].placement).toBe("Unknown placement");
  });

  it("marks a zero-service lane unassessed only when the service collection is complete", () => {
    const kube = environment("e3", "qa");
    const complete = buildProjectTopology("p1", [kube], [], true);
    expect(complete[0].evidence).toBe("unassessed");

    const incomplete = buildProjectTopology("p1", [kube], [], false);
    expect(incomplete[0].evidence).toBe("incomplete");
  });

  it("never reports a success tone for an environment lane under incomplete evidence", () => {
    const kube = environment("e1", "prod");
    const rows = [serviceRow("e1", "healthy")];
    const topology = buildProjectTopology("p1", [kube], rows, false);
    expect(topology[0].evidence).toBe("incomplete");
    expect(topology[0].tone).not.toBe("success");
  });
});
