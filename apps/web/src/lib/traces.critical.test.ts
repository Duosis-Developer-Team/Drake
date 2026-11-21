import { describe, expect, it } from "vitest";

import { criticalPath, layoutWaterfall, type Span } from "@/lib/traces";

const span = (id: string, parent: string | null, startMs: number, durationMs: number, kind = "Internal"): Span => ({
  timestamp: new Date(Date.UTC(2026, 10, 21, 12, 0, 0) + startMs).toISOString(),
  span_id: id,
  parent_span_id: parent,
  name: id,
  kind,
  duration_ms: durationMs,
  error: false,
  status_message: null,
  attributes: {},
  pod: null,
});

describe("criticalPath", () => {
  it("walks back from the end through what the request waited on", () => {
    const rows = layoutWaterfall([
      span("root", null, 0, 2110, "Server"),
      span("auth", "root", 30, 1600, "Client"),
      span("connect", "auth", 36, 980),
      span("select", "auth", 1020, 14),
      span("connect2", "auth", 1052, 548),
      span("db", "root", 1650, 18, "Client"),
      span("serialize", "root", 1730, 360),
    ]);
    const path = criticalPath(rows);
    // serialize ran last; before it the db call; before that the auth call,
    // inside which the second connect and then the first.
    expect([...path]).toEqual(["root", "serialize", "db", "auth", "connect2", "select", "connect"]);
  });

  it("descends through a slow call when it is what the request waited for", () => {
    const rows = layoutWaterfall([
      span("root", null, 0, 1700, "Server"),
      span("auth", "root", 30, 1600, "Client"),
      span("connect", "auth", 36, 980),
      span("connect2", "auth", 1052, 570),
      span("db", "root", 200, 18, "Client"),
    ]);
    // The db call ran alongside auth and finished first: it held nothing up.
    expect([...criticalPath(rows)]).toEqual(["root", "auth", "connect2", "connect"]);
  });

  it("is empty for an empty trace and survives a parent cycle", () => {
    expect(criticalPath([]).size).toBe(0);
    const rows = layoutWaterfall([span("a", "b", 0, 10), span("b", "a", 0, 10)]);
    expect(() => criticalPath(rows)).not.toThrow();
  });
});
