import { describe, expect, it } from "vitest";

import { buildHealthMatrix, worstToneOf } from "@/lib/view-models/health-matrix";
import type { ServiceHealthRow } from "@/lib/serviceHealth";

function row(overrides: Partial<ServiceHealthRow> & { status: ServiceHealthRow["health"]["status"] }): ServiceHealthRow {
  const { status, ...rest } = overrides;
  return {
    environment_service_id: "es1",
    project_id: "p1",
    project_key: "alpha",
    environment_id: "e1",
    environment_key: "production",
    service_key: "checkout",
    display_name: null,
    component: null,
    binding: null,
    health: {
      status,
      computed_at: "2026-08-11T00:00:00Z",
      newest_sample_at: "2026-08-11T00:00:00Z",
      freshness_age_seconds: 0,
      partial: false,
      served_from_last_good: false,
      reasons: [],
      availability: {},
      stability: {},
      resources: {},
    },
    ...rest,
  };
}

describe("buildHealthMatrix", () => {
  it("groups services by project and environment", () => {
    const cells = buildHealthMatrix([
      row({ status: "healthy", project_key: "alpha", environment_key: "production", service_key: "checkout" }),
      row({ status: "healthy", project_key: "alpha", environment_key: "production", service_key: "billing" }),
      row({ status: "healthy", project_key: "alpha", environment_key: "staging", service_key: "checkout" }),
    ]);
    expect(cells).toHaveLength(2);
    const prod = cells.find((cell) => cell.environmentKey === "production");
    expect(prod?.services).toHaveLength(2);
  });

  it("a cell's tone is the worst service's tone, even when most are healthy", () => {
    const cells = buildHealthMatrix([
      row({ status: "healthy", service_key: "checkout" }),
      row({ status: "healthy", service_key: "billing" }),
      row({ status: "critical", service_key: "payments" }),
    ]);
    expect(cells).toHaveLength(1);
    expect(worstToneOf(cells[0])).toBe("critical");
  });

  it("returns an explicit empty list for no in-scope services, not a phantom cell", () => {
    expect(buildHealthMatrix([])).toEqual([]);
  });
});
