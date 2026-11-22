import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DurationBuckets, ShareBar, SpanTimeline } from "./Visuals";

describe("DurationBuckets", () => {
  it("puts each duration in its bucket and keeps ongoing ones apart", () => {
    render(<DurationBuckets seconds={[60, 1200, 1800, 7200, 200_000]} ongoing={2} ongoingLabel="still open" />);
    const rows = screen.getAllByRole("listitem");
    const counts = rows.map((row) => row.lastElementChild?.textContent);
    // < 15m, 15m–1h, 1h–4h, 4h–1d, > 1d, still open
    expect(counts).toEqual(["1", "2", "1", "0", "1", "2"]);
    expect(screen.getByText("still open")).toBeInTheDocument();
  });

  it("draws no ongoing row when nothing is ongoing", () => {
    render(<DurationBuckets seconds={[60]} ongoing={0} ongoingLabel="still open" />);
    expect(screen.queryByText("still open")).not.toBeInTheDocument();
  });
});

describe("ShareBar", () => {
  it("shows each part with its share, and nothing for an empty total", () => {
    const { container, rerender } = render(
      <ShareBar
        segments={[
          { key: "a", label: "Hermes", colour: "red", count: 3 },
          { key: "b", label: "LogiSlot", colour: "blue", count: 1 },
          { key: "c", label: "Nobody", colour: "gray", count: 0 },
        ]}
      />,
    );
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.queryByText("Nobody")).not.toBeInTheDocument();
    rerender(<ShareBar segments={[{ key: "c", label: "Nobody", colour: "gray", count: 0 }]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("SpanTimeline", () => {
  it("draws one lane per place with its summary, and names its spans", () => {
    const to = Date.parse("2026-11-22T12:00:00Z");
    const from = to - 7 * 86_400_000;
    render(
      <SpanTimeline
        from={from}
        to={to}
        tickDays={1}
        label="last 7 days"
        lanes={[
          {
            key: "hermes/test",
            label: "Hermes test",
            meta: "2",
            spans: [
              { id: "1", start: to - 3_600_000 * 30, end: to - 3_600_000 * 29, colour: "red", title: "HighErrorRate" },
              { id: "2", start: to - 3_600_000 * 2, end: null, colour: "red", title: "SlowRequests" },
            ],
          },
        ]}
      />,
    );
    expect(screen.getByText("Hermes test")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "HighErrorRate; SlowRequests" })).toBeInTheDocument();
  });
});
