import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProjectTopology } from "@/components/features/catalog/ProjectTopology";
import type { EnvironmentLaneModel, ServiceLaneModel } from "@/lib/view-models/scope-health";

function service(id: string, tone: ServiceLaneModel["tone"], statusLabel: string): ServiceLaneModel {
  return {
    id,
    serviceKey: id,
    displayName: id,
    component: null,
    tone,
    statusLabel,
    freshnessAgeSeconds: 30,
    partial: false,
    binding: null,
    measurements: { ready: null, desired: null, restarts: null, cpu: null, memory: null },
    href: `/projects/p1/environments/e-prod/services/${id}`,
  };
}

const BOUND_SERVICE: ServiceLaneModel = {
  ...service("checkout", "success", "Healthy"),
  binding: {
    id: "b1",
    lifecycle: "active",
    namespace: "prod",
    workload_kind: "Deployment",
    workload_name: "checkout",
    resolved: true,
    preset_key: "default",
    health_policy_key: "default",
    revision: 1,
    cluster: { cluster_ref: "cluster-a", id: "cluster-opaque-123" },
  },
};

const LANES: EnvironmentLaneModel[] = [
  {
    id: "e-empty",
    key: "empty-env",
    runtime: "kubernetes",
    placement: "cluster-a/default",
    tone: "unknown",
    evidence: "unassessed",
    services: [],
    href: "/projects/p1/environments/e-empty",
  },
  {
    id: "e-prod",
    key: "prod",
    runtime: "kubernetes",
    placement: "cluster-a/prod",
    tone: "critical",
    evidence: "complete",
    services: [service("billing", "critical", "Critical"), service("cart", "stale", "Stale"), BOUND_SERVICE],
    href: "/projects/p1/environments/e-prod",
  },
  {
    id: "e-ext",
    key: "external-env",
    runtime: "external",
    placement: "Not applicable",
    tone: "not-applicable",
    evidence: "not-applicable",
    services: [],
    href: "/projects/p1/environments/e-ext",
  },
];

describe("ProjectTopology", () => {
  it("names every environment lane with a heading", () => {
    render(<ProjectTopology lanes={LANES} />);
    expect(screen.getByRole("heading", { name: /empty-env/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /prod/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /external-env/i })).toBeInTheDocument();
  });

  it("orders service links worst-first, exactly as the model produced them", () => {
    render(<ProjectTopology lanes={LANES} />);
    const list = screen.getByTestId("service-lane-list");
    const links = within(list).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/projects/p1/environments/e-prod/services/billing",
      "/projects/p1/environments/e-prod/services/cart",
      "/projects/p1/environments/e-prod/services/checkout",
    ]);
  });

  it("gives every service status visible text, not only an icon or colour", () => {
    render(<ProjectTopology lanes={LANES} />);
    const lane = screen.getByTestId("environment-lane-e-prod");
    expect(within(lane).getByText("Critical")).toBeInTheDocument();
    expect(within(lane).getByText("Stale")).toBeInTheDocument();
    expect(within(lane).getByText("Healthy")).toBeInTheDocument();
  });

  it("shows a bound service's opaque cluster reference without turning it into a broken link", () => {
    render(<ProjectTopology lanes={LANES} />);
    const list = screen.getByTestId("service-lane-list");
    expect(within(list).getByText(/cluster-a/)).toBeInTheDocument();
  });

  it("renders an empty environment with no service rows, never inventing one", () => {
    render(<ProjectTopology lanes={LANES} />);
    const lane = screen.getByTestId("environment-lane-e-empty");
    expect(within(lane).queryByTestId("service-lane-list")).not.toBeInTheDocument();
  });

  it("reads an external runtime environment as Not applicable, never a health claim", () => {
    render(<ProjectTopology lanes={LANES} />);
    const lane = screen.getByTestId("environment-lane-e-ext");
    expect(within(lane).getByText("Not applicable")).toBeInTheDocument();
  });
});
