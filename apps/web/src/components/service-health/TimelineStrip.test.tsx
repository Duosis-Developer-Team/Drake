import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TimelineStrip } from "@/components/service-health/TimelineStrip";
import type { TimelinePage } from "@/lib/timeline";
import { installFetchMock } from "@/test/mock-api";

function frame(count: number, startMs = Date.parse("2026-11-15T12:00:00Z"), width = 150_000) {
  return Array.from({ length: count }, (_, i) => ({
    start: new Date(startMs + i * width).toISOString(),
    end: new Date(startMs + (i + 1) * width).toISOString(),
    partial: i === count - 1,
  }));
}

function page(overrides: Partial<TimelinePage> = {}): TimelinePage {
  const buckets = frame(24);
  return {
    binding_id: "b1",
    range: "1h",
    as_of: "2026-11-15T13:00:00Z",
    bucket_seconds: 150,
    window: { start: buckets[0].start, end: buckets[23].end },
    buckets,
    requests: {
      state: "ok",
      buckets: buckets.map((_, i) => ({
        requests: 40,
        errors: i === 10 ? 8 : 0,
        error_ratio: i === 10 ? 0.2 : 0,
        p95_ms: i === 10 ? 1800 : 90,
      })),
    },
    logs: {
      state: "ok",
      buckets: buckets.map((_, i) => ({ lines: 20, errors: i === 10 ? 6 : 0, warnings: 0 })),
    },
    ...overrides,
  };
}

function routes(body: TimelinePage | null, status = 200) {
  return {
    "/v1/service-health/bindings/b1/timeline": { status, body: body ?? {} },
    "/v1/deployments": {
      status: 200,
      body: {
        items: [
          {
            id: "d1",
            rollout_started_at: page().buckets[9].start,
            short_commit: "abc1234",
            short_digest: null,
          },
        ],
        next_cursor: null,
        total: 1,
        limit: 50,
      },
    },
    "/v1/service-health/bindings/b1/incidents": {
      status: 200,
      body: { items: [{ id: "i1", title: "Payments failing", opened_at: page().buckets[11].start }], total: 1 },
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("TimelineStrip", () => {
  it("draws the four lanes, marks deployments and incidents, and selects a slice", async () => {
    installFetchMock(routes(page()));
    const onSelect = vi.fn();
    render(<TimelineStrip bindingId="b1" environmentServiceId="es1" selected={null} onSelect={onSelect} />);

    const strip = await screen.findByTestId("timeline-strip");
    for (const lane of ["Requests", "Failed", "p95 latency", "Error log lines", "Events"]) {
      expect(within(strip).getByText(lane)).toBeInTheDocument();
    }
    const events = within(strip).getByTestId("timeline-events");
    await waitFor(() => expect(events).toHaveTextContent("Deployed abc1234"));
    expect(events).toHaveTextContent("Payments failing");

    const slice = within(strip).getByRole("button", { name: /12:25–12:27: 40 requests, 8 failed/ });
    fireEvent.mouseEnter(slice);
    expect(within(strip).getByTestId("timeline-readout")).toHaveTextContent("12:25–12:27 UTC");
    fireEvent.click(slice);
    expect(onSelect).toHaveBeenCalledWith({
      start: page().buckets[10].start,
      end: page().buckets[10].end,
      range: "1h",
    });
  });

  it("says a lane is not enabled instead of drawing a flat line", async () => {
    installFetchMock(routes(page({ requests: { state: "not_configured", buckets: [] } })));
    render(<TimelineStrip bindingId="b1" environmentServiceId="es1" selected={null} onSelect={() => {}} />);

    const strip = await screen.findByTestId("timeline-strip");
    expect(within(strip).getAllByText("Not enabled on this Tracesis")).toHaveLength(3);
    expect(within(strip).queryByText("The store did not answer")).not.toBeInTheDocument();
  });

  it("shows nothing at all to a reader without traces.view or logs.view", async () => {
    installFetchMock(routes(null, 404));
    const { container } = render(
      <TimelineStrip bindingId="b1" environmentServiceId="es1" selected={null} onSelect={() => {}} />,
    );
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("clicking the selected slice again clears the selection", async () => {
    installFetchMock(routes(page()));
    const onSelect = vi.fn();
    const p = page();
    render(
      <TimelineStrip
        bindingId="b1"
        environmentServiceId="es1"
        selected={{ start: p.buckets[10].start, end: p.buckets[10].end, range: "1h" }}
        onSelect={onSelect}
      />,
    );
    const strip = await screen.findByTestId("timeline-strip");
    fireEvent.click(within(strip).getByRole("button", { pressed: true }));
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});
