import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HealthMatrix } from "@/components/data-viz/HealthMatrix";
import type { HealthMatrixCell } from "@/lib/view-models/health-matrix";

const CELLS: HealthMatrixCell[] = [
  {
    projectKey: "alpha",
    environmentKey: "production",
    projectId: "p-alpha",
    environmentId: "e-production",
    services: [
      { serviceKey: "checkout", tone: "success" },
      { serviceKey: "billing", tone: "critical" },
    ],
  },
  {
    projectKey: "alpha",
    environmentKey: "staging",
    projectId: "p-alpha",
    environmentId: "e-staging",
    services: [{ serviceKey: "checkout", tone: "success" }],
  },
];

describe("HealthMatrix", () => {
  it("renders one cell per environment with the worst tone among its services", () => {
    render(<HealthMatrix cells={CELLS} status="ready" compact />);
    const disclosure = within(screen.getByTestId("health-matrix-disclosure"));
    const production = disclosure.getByText("production").closest("li");
    expect(production).not.toBeNull();
    // The critical billing service should surface a "(2)" count, not hide
    // behind the healthy checkout row it's grouped with.
    expect(within(production as HTMLElement).getByText("(2)")).toBeInTheDocument();
  });

  it("names the worst status as visible text, not color alone", () => {
    render(<HealthMatrix cells={CELLS} status="ready" compact />);
    const disclosure = within(screen.getByTestId("health-matrix-disclosure"));
    const production = disclosure.getByText("production").closest("li");
    expect(within(production as HTMLElement).getByText("Critical")).toBeInTheDocument();
  });

  it("a populated cell drills down into service health scoped to its project and environment", () => {
    render(<HealthMatrix cells={CELLS} status="ready" compact />);
    const link = screen.getByRole("link", {
      name: /alpha \/ production: worst status critical, 2 services — open service health/i,
    });
    expect(link).toHaveAttribute("href", "/service-health?project_id=p-alpha&environment_id=e-production");
  });

  it("the compact prop renders the disclosure list", () => {
    render(<HealthMatrix cells={CELLS} status="ready" compact />);
    expect(screen.getByTestId("health-matrix-disclosure")).toBeInTheDocument();
    expect(screen.queryByTestId("health-matrix-grid")).not.toBeInTheDocument();
  });

  it("renders both the grid and the disclosure list when not compact", () => {
    render(<HealthMatrix cells={CELLS} status="ready" />);
    expect(screen.getByTestId("health-matrix-grid")).toBeInTheDocument();
    expect(screen.getByTestId("health-matrix-disclosure")).toBeInTheDocument();
  });

  it("empty cells render NotConfiguredState, never an empty grid", () => {
    render(<HealthMatrix cells={[]} status="ready" />);
    expect(screen.getByTestId("state-not-configured")).toBeInTheDocument();
    expect(screen.queryByTestId("health-matrix-grid")).not.toBeInTheDocument();
    expect(screen.queryByText(/all systems|everything healthy/i)).not.toBeInTheDocument();
  });

  it("a denied source renders DeniedState, not an empty grid", () => {
    render(<HealthMatrix cells={[]} status="denied" />);
    expect(screen.getByTestId("state-permission-denied")).toBeInTheDocument();
  });
});
