import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CapacityRiskBoard } from "@/components/data-viz/CapacityRiskBoard";
import type { CapacityRiskItem } from "@/lib/view-models/capacity-risk";

describe("CapacityRiskBoard", () => {
  it("ranks a critical PVC item above a warning certificate item", () => {
    const items: CapacityRiskItem[] = [
      {
        key: "certificate:c1",
        clusterId: "c1",
        clusterName: "prod-1",
        kind: "certificate",
        label: "Agent certificate expiring",
        tone: "warning",
        detail: "warning detail",
        deadline: "2026-09-01T00:00:00Z",
        href: "/clusters/c1",
      },
      {
        key: "pvc:c2",
        clusterId: "c2",
        clusterName: "prod-2",
        kind: "pvc",
        label: "Persistent volume claims at risk",
        tone: "critical",
        detail: "1 unhealthy, 0 degraded of 9 total",
        href: "/clusters/c2/inventory",
      },
    ];
    render(<CapacityRiskBoard items={items} unassessedClusters={[]} />);
    const rows = screen.getAllByRole("link");
    expect(rows[0]).toHaveTextContent("prod-2");
    expect(rows[1]).toHaveTextContent("prod-1");
  });

  it("says exactly 'no risk reported', never 'all capacity healthy', when empty", () => {
    render(<CapacityRiskBoard items={[]} unassessedClusters={[]} />);
    expect(screen.getByTestId("capacity-risk-empty")).toHaveTextContent(
      /no certificate or pvc risk reported by the sources checked/i,
    );
    expect(screen.queryByText(/all capacity healthy/i)).not.toBeInTheDocument();
  });

  it("lists a cluster with no inventory summary as forecast unavailable, separately from zero-risk", () => {
    render(<CapacityRiskBoard items={[]} unassessedClusters={["prod-3"]} />);
    expect(screen.getByTestId("capacity-risk-unassessed")).toHaveTextContent(/forecast unavailable/i);
    expect(screen.getByTestId("capacity-risk-unassessed")).toHaveTextContent("prod-3");
  });
});
