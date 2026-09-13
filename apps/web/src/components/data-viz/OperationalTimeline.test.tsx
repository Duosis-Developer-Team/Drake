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

  it("states the window's real start and end, not just dot position", () => {
    render(<OperationalTimeline lanes={LANES} />);
    const axis = screen.getByTestId("timeline-axis");
    expect(axis).toHaveTextContent("08:00 UTC");
    expect(axis).toHaveTextContent("09:00 UTC");
  });

  it("a same-day window states bare clock times, with no date to read", () => {
    render(<OperationalTimeline lanes={LANES} />);
    const axis = screen.getByTestId("timeline-axis");
    // LANES' events are all on 2026-08-10 — nothing here should print a
    // month or day-of-month, since same-day clock times are unambiguous.
    expect(axis).not.toHaveTextContent(/Aug/);
  });

  it("a window crossing midnight UTC dates both ends, so it cannot read as going backward", () => {
    const overnightLanes: TimelineLane[] = [
      {
        key: "deployments",
        label: "Deployments",
        historyAvailable: true,
        events: [
          {
            id: "d1",
            kind: "deployment",
            tone: "success",
            at: "2026-08-10T22:52:00Z",
            label: "Deployment: core-api",
            href: "/deployments/d1",
          },
          {
            id: "i1",
            kind: "incident_opened",
            tone: "critical",
            at: "2026-08-11T09:00:00Z",
            label: "Incident opened: core-api",
            href: "/incidents/i1",
          },
        ],
      },
    ];
    render(<OperationalTimeline lanes={overnightLanes} />);
    const axis = screen.getByTestId("timeline-axis");
    // The earlier point (22:52 on the 10th) must still read first (left),
    // now carrying its own date rather than a bare "22:52 UTC" that would
    // look later than a bare "09:00 UTC" to its right.
    expect(axis).toHaveTextContent("10 Aug, 22:52 UTC");
    expect(axis).toHaveTextContent("11 Aug, 09:00 UTC");
    const spans = within(axis).getAllByText(/UTC/);
    expect(spans[0]).toHaveTextContent("10 Aug, 22:52 UTC");
    expect(spans[1]).toHaveTextContent("11 Aug, 09:00 UTC");
  });

  it("renders no axis when there is nothing plotted to give it a range", () => {
    render(
      <OperationalTimeline
        lanes={[{ key: "alerts", label: "Alerts", historyAvailable: true, events: [] }]}
      />,
    );
    expect(screen.queryByTestId("timeline-axis")).not.toBeInTheDocument();
  });

  it("shows a visible label naming the event and its time, not only an accessible name", () => {
    render(<OperationalTimeline lanes={LANES} />);
    const tooltip = screen.getByText("Incident opened: checkout-api — 08:00 UTC");
    expect(tooltip).toHaveAttribute("role", "tooltip");
    // Hidden until hovered/focused, via opacity rather than `hidden`, so it
    // participates in layout for positioning but isn't visually announced.
    expect(tooltip.className).toMatch(/opacity-0/);
    expect(tooltip.className).toMatch(/group-focus-within:opacity-100/);
  });

  it("anchors a right-edge event's tooltip to that edge instead of centering it off the track", () => {
    render(<OperationalTimeline lanes={LANES} />);
    // "Incident resolved" sits at the window's max, i.e. position 100% —
    // the exact case that used to center-overflow past the right edge.
    const tooltip = screen.getByText("Incident resolved: checkout-api — 09:00 UTC");
    expect(tooltip.className).toMatch(/right-0/);
    expect(tooltip.className).not.toMatch(/left-1\/2/);
  });

  it("keeps a mid-track event's tooltip centered", () => {
    render(<OperationalTimeline lanes={LANES} />);
    // "Deployment" sits at 08:30, the midpoint between 08:00 and 09:00.
    const tooltip = screen.getByText("Deployment: checkout (f00d) — 08:30 UTC");
    expect(tooltip.className).toMatch(/left-1\/2/);
    expect(tooltip.className).toMatch(/-translate-x-1\/2/);
  });
});
