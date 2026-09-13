import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OperationalTimeline } from "@/components/data-viz/OperationalTimeline";
import type { TimelineLane } from "@/lib/view-models/timeline";

const LANES: TimelineLane[] = [
  {
    key: "incidents",
    label: "Incidents",
    historyAvailable: true,
    events: [
      {
        id: "i1",
        kind: "incident_opened",
        tone: "critical",
        at: "2026-08-10T08:00:00Z",
        label: "Incident opened: checkout-api",
        href: "/incidents/i1",
      },
      {
        id: "i2",
        kind: "incident_resolved",
        tone: "success",
        at: "2026-08-10T09:00:00Z",
        label: "Incident resolved: checkout-api",
        href: "/incidents/i1",
      },
    ],
  },
  {
    key: "deployments",
    label: "Deployments",
    historyAvailable: true,
    events: [
      {
        id: "d1",
        kind: "deployment",
        tone: "success",
        at: "2026-08-10T08:30:00Z",
        label: "Deployment: checkout (f00d)",
        href: "/deployments/d1",
      },
    ],
  },
  {
    key: "cluster-service-health",
    label: "Service & cluster health",
    historyAvailable: false,
    events: [],
  },
];

describe("OperationalTimeline", () => {
  it("gives every plotted event an accessible name naming what and when", () => {
    render(<OperationalTimeline lanes={LANES} />);
    expect(
      screen.getByRole("link", { name: /incident opened: checkout-api, \d{2}:\d{2} UTC/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /incident resolved: checkout-api, \d{2}:\d{2} UTC/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /deployment: checkout \(f00d\), \d{2}:\d{2} UTC/i }),
    ).toBeInTheDocument();
  });

  it("renders the unavailable-history lane's stated reason and zero events", () => {
    render(<OperationalTimeline lanes={LANES} />);
    const unavailable = screen.getByTestId("lane-unavailable-cluster-service-health");
    expect(unavailable).toHaveTextContent(/history unavailable/i);
    expect(within(unavailable).queryAllByRole("link")).toHaveLength(0);
  });

  it("never renders a track for the unavailable lane, so it cannot be mistaken for zero events happening", () => {
    render(<OperationalTimeline lanes={LANES} />);
    // The unavailable lane's row holds no dots at all — there is no track to
    // read as "flat", only the stated reason.
    const row = screen.getByText("Service & cluster health").closest("div");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).queryAllByRole("link")).toHaveLength(0);
  });

  it("the table disclosure lists exactly as many rows as events plotted on the track", async () => {
    render(<OperationalTimeline lanes={LANES} />);
    const summary = screen.getByText(/view as table \(3 events\)/i);
    fireEvent.click(summary);
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    // 1 header row + 3 event rows.
    expect(rows).toHaveLength(4);
  });

  it("keeps each lane's events in chronological DOM order, so Tab reaches them in time order", () => {
    render(<OperationalTimeline lanes={LANES} />);
    const incidentLinks = screen.getAllByRole("link", { name: /incident (opened|resolved)/i });
    expect(incidentLinks[0]).toHaveAccessibleName(/opened/i);
    expect(incidentLinks[1]).toHaveAccessibleName(/resolved/i);
  });

  it("states an honest empty window rather than an empty track when a real lane has no events", () => {
    render(
      <OperationalTimeline
        lanes={[{ key: "alerts", label: "Alerts", historyAvailable: true, events: [] }]}
      />,
    );
    expect(screen.getByText(/no events in the selected window/i)).toBeInTheDocument();
  });
});
