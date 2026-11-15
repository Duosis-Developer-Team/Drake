import { describe, expect, it } from "vitest";

import {
  formatDuration,
  layoutWaterfall,
  summarizeTrace,
  traceDetailPath,
  tracesPath,
  type Span,
} from "@/lib/traces";

function span(overrides: Partial<Span> & Pick<Span, "span_id">): Span {
  return {
    timestamp: "2026-11-04T09:00:00.000+00:00",
    parent_span_id: null,
    name: "GET /orders/*",
    kind: "Server",
    duration_ms: 100,
    error: false,
    status_message: null,
    attributes: {},
    pod: "api-1",
    ...overrides,
  };
}

describe("paths", () => {
  it("sends only the caller's bounded choices", () => {
    const url = new URL(tracesPath("b1", { range: "6h", errorsOnly: true, sort: "slowest" }), "file:///");
    expect(url.pathname).toBe("/v1/service-health/bindings/b1/traces");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      range: "6h",
      status: "error",
      sort: "slowest",
      limit: "50",
    });
  });

  it("encodes the trace id into the detail path", () => {
    expect(traceDetailPath("b1", "abc/def")).toBe("/v1/service-health/bindings/b1/traces/abc%2Fdef");
  });
});

describe("layoutWaterfall", () => {
  it("nests children under their parent in start order with offsets as fractions", () => {
    const rows = layoutWaterfall([
      span({ span_id: "root", duration_ms: 100 }),
      span({
        span_id: "late",
        parent_span_id: "root",
        name: "processing",
        kind: "Internal",
        timestamp: "2026-11-04T09:00:00.050+00:00",
        duration_ms: 50,
      }),
      span({
        span_id: "early",
        parent_span_id: "root",
        name: "in queue",
        kind: "Internal",
        timestamp: "2026-11-04T09:00:00.000+00:00",
        duration_ms: 10,
      }),
    ]);
    expect(rows.map((row) => [row.span.span_id, row.depth])).toEqual([
      ["root", 0],
      ["early", 1],
      ["late", 1],
    ]);
    expect(rows[0].offset).toBe(0);
    expect(rows[0].width).toBe(1);
    expect(rows[2].offset).toBeCloseTo(0.5);
    expect(rows[2].width).toBeCloseTo(0.5);
  });

  it("draws a span whose parent is missing as a root instead of dropping it", () => {
    const rows = layoutWaterfall([span({ span_id: "orphan", parent_span_id: "elsewhere" })]);
    expect(rows).toHaveLength(1);
    expect(rows[0].depth).toBe(0);
  });

  it("survives a parent cycle and clamps bars to the track", () => {
    const rows = layoutWaterfall([
      span({ span_id: "a", parent_span_id: "b", duration_ms: 5 }),
      span({ span_id: "b", parent_span_id: "a", duration_ms: -50 }),
    ]);
    expect(rows.length).toBeLessThanOrEqual(2);
    for (const row of rows) {
      expect(row.offset).toBeGreaterThanOrEqual(0);
      expect(row.offset + row.width).toBeLessThanOrEqual(1);
      expect(row.width).toBeGreaterThanOrEqual(0);
    }
  });

  it("returns nothing for no spans", () => {
    expect(layoutWaterfall([])).toEqual([]);
  });
});

describe("formatDuration", () => {
  it.each([
    [null, "—"],
    [0.25, "250 µs"],
    [4.2, "4.2 ms"],
    [107.6, "108 ms"],
    [1200, "1.20 s"],
    [12_500, "12.5 s"],
  ] as const)("%s → %s", (ms, text) => {
    expect(formatDuration(ms)).toBe(text);
  });
});

describe("summarizeTrace", () => {
  const span = (over: Partial<Span>): Span => ({
    timestamp: "2026-11-15T10:00:00.000Z",
    span_id: "s",
    parent_span_id: null,
    name: "x",
    kind: "Internal",
    duration_ms: 0,
    error: false,
    status_message: null,
    attributes: {},
    pod: null,
    ...over,
  });
  const rootSpan = span({ span_id: "r", name: "POST /pay", kind: "Server", duration_ms: 1000 });

  it("names the outgoing call that took most of the time", () => {
    const rows = layoutWaterfall([
      rootSpan,
      span({ span_id: "c", parent_span_id: "r", name: "POST gw/charge", kind: "Client", duration_ms: 930, error: true }),
    ]);
    expect(summarizeTrace(rows)).toMatchObject({ kind: "call", name: "POST gw/charge", failed: true });
    expect((summarizeTrace(rows) as { share: number }).share).toBeCloseTo(0.93);
  });

  it("says the request waited in the queue when that is where the time went", () => {
    const rows = layoutWaterfall([
      rootSpan,
      span({ span_id: "q", parent_span_id: "r", name: "in queue", duration_ms: 600 }),
      span({ span_id: "p", parent_span_id: "r", name: "processing", duration_ms: 390 }),
    ]);
    expect(summarizeTrace(rows)).toMatchObject({ kind: "queue", durationMs: 600 });
  });

  it("falls back to the service's own work, counting the calls it made", () => {
    const rows = layoutWaterfall([
      rootSpan,
      span({ span_id: "c", parent_span_id: "r", name: "SELECT", kind: "Client", duration_ms: 100 }),
    ]);
    expect(summarizeTrace(rows)).toMatchObject({ kind: "self", calls: 1 });
  });

  it("says nothing about a trace with no measured root", () => {
    expect(summarizeTrace([])).toBeNull();
    expect(summarizeTrace(layoutWaterfall([span({ kind: "Server", duration_ms: null })]))).toBeNull();
  });
});
