import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvidenceCoverage } from "@/components/command-center/EvidenceCoverage";
import type { Resource } from "@/lib/useResource";

function resource(overrides: Partial<Resource<unknown>>): Resource<unknown> {
  return {
    data: null,
    loading: false,
    refreshing: false,
    error: null,
    denied: false,
    notFound: false,
    fetchedAt: null,
    reload: () => {},
    ...overrides,
  };
}

describe("EvidenceCoverage", () => {
  it("renders one row per state, correctly labelled", () => {
    const sources = [
      { key: "a", label: "Incidents", resource: resource({ data: { items: [] } }) },
      { key: "b", label: "Alerts", resource: resource({ data: {}, error: "stale refresh" }) },
      { key: "c", label: "Clusters", resource: resource({ denied: true }) },
      { key: "d", label: "Services", resource: resource({ notFound: true }) },
      { key: "e", label: "Integrations", resource: resource({ error: "network down" }) },
      { key: "f", label: "Deployments", resource: resource({ loading: true }) },
    ];
    render(<EvidenceCoverage sources={sources} />);
    const list = within(screen.getByTestId("evidence-coverage"));
    expect(list.getByText("Incidents").closest("li")).toHaveTextContent("Answered");
    expect(list.getByText("Alerts").closest("li")).toHaveTextContent("Last known good");
    expect(list.getByText("Clusters").closest("li")).toHaveTextContent("Permission required");
    expect(list.getByText("Services").closest("li")).toHaveTextContent("Not configured");
    expect(list.getByText("Integrations").closest("li")).toHaveTextContent("Unavailable");
    expect(list.getByText("Deployments").closest("li")).toHaveTextContent("Checking");
  });

  it("never claims all systems or all sources healthy when a source is denied or unavailable", () => {
    const sources = [
      { key: "a", label: "Incidents", resource: resource({ denied: true }) },
      { key: "b", label: "Alerts", resource: resource({ error: "network down" }) },
    ];
    render(<EvidenceCoverage sources={sources} />);
    expect(screen.queryByText(/all systems healthy/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/all sources healthy/i)).not.toBeInTheDocument();
  });
});
