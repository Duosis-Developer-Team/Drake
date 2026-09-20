import { describe, expect, it } from "vitest";

import type { Project } from "@/lib/catalog";
import type { ServiceHealthRow, ServiceHealthStatus } from "@/lib/serviceHealth";
import { buildPortfolioRisk } from "@/lib/view-models/portfolio-risk";

function project(id: string, key: string): Project {
  return {
    id,
    project_key: key,
    display_name: key,
    lifecycle: "active",
    criticality: "medium",
    tenant_model: "single_tenant",
    repository: { provider: "github", owner: "acme", name: key, default_branch: "main" },
    version: 1,
    scope: { type: "project", ref: id },
    source: { kind: "repository", ref: "main", revision: "abc123", accepted_at: "2026-01-01T00:00:00Z" },
    counts: { environments: 1, services: 1 },
    as_of: "2026-01-01T00:00:00Z",
  };
}

let serviceSeq = 0;
function service(projectId: string, status: ServiceHealthStatus): ServiceHealthRow {
  serviceSeq += 1;
  return {
    environment_service_id: `es-${serviceSeq}`,
    project_id: projectId,
    project_key: projectId,
    environment_id: "env-1",
    environment_key: "prod",
    service_key: `svc-${serviceSeq}`,
    display_name: null,
    component: null,
    binding: null,
    health: {
      status,
      computed_at: "2026-01-01T00:00:00Z",
      newest_sample_at: null,
      freshness_age_seconds: null,
      partial: false,
      served_from_last_good: false,
      reasons: [],
      availability: {},
      stability: {},
      resources: {},
    },
  };
}

describe("buildPortfolioRisk", () => {
  it("marks a critical project with a critical service as complete evidence", () => {
    const model = buildPortfolioRisk(
      [{ ...project("p1", "alpha"), criticality: "critical" }],
      [service("p1", "healthy"), service("p1", "critical")],
      { projects: true, services: true, servicesTotal: 2 },
    );
    expect(model.items[0]).toMatchObject({
      projectKey: "alpha",
      criticality: "critical",
      tone: "critical",
      servicesObserved: 2,
      evidence: "complete",
    });
  });

  it("marks a project with zero matched services as unassessed when service evidence is complete", () => {
    const model = buildPortfolioRisk(
      [project("p1", "alpha")],
      [],
      { projects: true, services: true, servicesTotal: 0 },
    );
    expect(model.items[0]).toMatchObject({
      evidence: "unassessed",
      servicesObserved: 0,
      tone: "unknown",
      healthLabel: "Unassessed",
    });
  });

  it("marks a project with zero matched services as incomplete, not unassessed, when service evidence is incomplete", () => {
    const model = buildPortfolioRisk(
      [project("p1", "alpha")],
      [],
      { projects: true, services: false, servicesTotal: 50 },
    );
    expect(model.items[0]).toMatchObject({
      evidence: "incomplete",
      servicesObserved: 0,
      tone: "unknown",
      healthLabel: "Evidence incomplete",
    });
  });

  it("reports model.complete false when either projects or services are incomplete", () => {
    const incompleteProjects = buildPortfolioRisk([project("p1", "alpha")], [], {
      projects: false,
      services: true,
      servicesTotal: 0,
    });
    expect(incompleteProjects.complete).toBe(false);

    const incompleteServices = buildPortfolioRisk([project("p1", "alpha")], [], {
      projects: true,
      services: false,
      servicesTotal: 50,
    });
    expect(incompleteServices.complete).toBe(false);

    const bothComplete = buildPortfolioRisk([project("p1", "alpha")], [], {
      projects: true,
      services: true,
      servicesTotal: 0,
    });
    expect(bothComplete.complete).toBe(true);
  });

  it("does not let a healthy majority hide one critical service", () => {
    const model = buildPortfolioRisk(
      [project("p1", "alpha")],
      [service("p1", "healthy"), service("p1", "healthy"), service("p1", "critical")],
      { projects: true, services: true, servicesTotal: 3 },
    );
    expect(model.items[0].tone).toBe("critical");
    expect(model.items[0].evidence).toBe("complete");
  });

  it("never reports a success tone under incomplete evidence, even with a healthy service already loaded", () => {
    const model = buildPortfolioRisk(
      [project("p1", "alpha")],
      [service("p1", "healthy")],
      { projects: true, services: false, servicesTotal: 50 },
    );
    expect(model.items[0]).toMatchObject({
      evidence: "incomplete",
      servicesObserved: 1,
    });
    expect(model.items[0].tone).not.toBe("success");
  });

  it("orders items by criticality first, then by shared tone severity", () => {
    const model = buildPortfolioRisk(
      [
        { ...project("p1", "beta-high-critical"), criticality: "high" },
        { ...project("p2", "alpha-critical-healthy"), criticality: "critical" },
        { ...project("p3", "gamma-critical-critical"), criticality: "critical" },
      ],
      [
        service("p1", "critical"),
        service("p2", "healthy"),
        service("p3", "critical"),
      ],
      { projects: true, services: true, servicesTotal: 3 },
    );
    expect(model.items.map((item) => item.projectKey)).toEqual([
      "gamma-critical-critical",
      "alpha-critical-healthy",
      "beta-high-critical",
    ]);
  });
});
