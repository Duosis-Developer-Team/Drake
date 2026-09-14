import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProjectRiskMap } from "@/components/data-viz/ProjectRiskMap";
import type { PortfolioRiskModel } from "@/lib/view-models/portfolio-risk";

const COMPLETE_MODEL: PortfolioRiskModel = {
  items: [
    {
      projectId: "p1",
      projectKey: "alpha",
      displayName: "Alpha",
      criticality: "critical",
      tone: "critical",
      healthLabel: "Critical",
      servicesObserved: 2,
      evidence: "complete",
      href: "/projects/p1",
    },
    {
      projectId: "p2",
      projectKey: "beta",
      displayName: "Beta",
      criticality: "high",
      tone: "success",
      healthLabel: "Healthy",
      servicesObserved: 3,
      evidence: "complete",
      href: "/projects/p2",
    },
  ],
  complete: true,
  projectsLoaded: 2,
  servicesLoaded: 5,
  servicesTotal: 5,
};

const INCOMPLETE_MODEL: PortfolioRiskModel = {
  items: [
    {
      projectId: "p3",
      projectKey: "gamma",
      displayName: "Gamma",
      criticality: "medium",
      tone: "unknown",
      healthLabel: "Evidence incomplete",
      servicesObserved: 0,
      evidence: "incomplete",
      href: "/projects/p3",
    },
  ],
  complete: false,
  projectsLoaded: 1,
  servicesLoaded: 40,
  servicesTotal: 90,
};

function noop() {}

describe("ProjectRiskMap", () => {
  it("names row and column headers, and links each project by name and status", () => {
    render(<ProjectRiskMap model={COMPLETE_MODEL} activeTone={null} onToneChange={noop} />);
    const grid = within(screen.getByTestId("project-risk-map-grid"));
    expect(grid.getByRole("rowheader", { name: "Critical" })).toBeInTheDocument();
    expect(grid.getByRole("rowheader", { name: "High" })).toBeInTheDocument();
    expect(grid.getByRole("columnheader", { name: "Critical" })).toBeInTheDocument();
    expect(grid.getByRole("columnheader", { name: "Healthy" })).toBeInTheDocument();
    expect(grid.getByRole("link", { name: /Alpha.*Critical/ })).toHaveAttribute(
      "href",
      "/projects/p1",
    );
    expect(grid.getByRole("link", { name: /Beta.*Healthy/ })).toHaveAttribute(
      "href",
      "/projects/p2",
    );
  });

  it("gives every tone control a visible text label, never color alone", () => {
    render(<ProjectRiskMap model={COMPLETE_MODEL} activeTone={null} onToneChange={noop} />);
    const grid = within(screen.getByTestId("project-risk-map-grid"));
    expect(grid.getByRole("button", { name: "Critical" })).toBeInTheDocument();
    expect(grid.getByRole("button", { name: "Healthy" })).toBeInTheDocument();
  });

  it("shows the completeness note only when every collection loaded is complete", () => {
    render(<ProjectRiskMap model={COMPLETE_MODEL} compact activeTone={null} onToneChange={noop} />);
    expect(screen.getByText("All projects assessed")).toBeInTheDocument();
  });

  it("surfaces evidence incomplete instead of a completeness claim when a collection is still partial", () => {
    render(<ProjectRiskMap model={INCOMPLETE_MODEL} compact activeTone={null} onToneChange={noop} />);
    expect(screen.getByText(/Evidence incomplete/)).toBeInTheDocument();
    expect(screen.queryByText("All projects assessed")).not.toBeInTheDocument();
  });

  it("compact renders only the disclosure list, never the desktop matrix", () => {
    render(<ProjectRiskMap model={COMPLETE_MODEL} compact activeTone={null} onToneChange={noop} />);
    expect(screen.getByTestId("project-risk-map-disclosure")).toBeInTheDocument();
    expect(screen.queryByTestId("project-risk-map-grid")).not.toBeInTheDocument();
  });

  it("toggles a tone filter on and off when its column control is clicked twice", () => {
    const onToneChange = vi.fn();
    const { rerender } = render(
      <ProjectRiskMap model={COMPLETE_MODEL} activeTone={null} onToneChange={onToneChange} />,
    );
    screen.getByRole("button", { name: "Critical" }).click();
    expect(onToneChange).toHaveBeenCalledWith("critical");

    rerender(
      <ProjectRiskMap model={COMPLETE_MODEL} activeTone="critical" onToneChange={onToneChange} />,
    );
    screen.getByRole("button", { name: "Critical" }).click();
    expect(onToneChange).toHaveBeenCalledWith(null);
  });
});
