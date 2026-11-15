import { describe, expect, it } from "vitest";

import { lanePaths, positionIn, timelinePath } from "@/lib/timeline";

describe("timelinePath", () => {
  it("names the binding and the range", () => {
    expect(timelinePath("b 1", "6h")).toBe("/v1/service-health/bindings/b%201/timeline?range=6h");
  });
});

describe("lanePaths", () => {
  it("draws nothing for no values", () => {
    expect(lanePaths([], 10)).toEqual({ line: "", area: "" });
  });

  it("breaks the line at a missing value instead of drawing it as zero", () => {
    const { line, area } = lanePaths([1, 2, null, 4], 4);
    expect(line.match(/M /g)).toHaveLength(2);
    expect(area.match(/Z/g)).toHaveLength(2);
    expect(line).not.toContain("NaN");
  });

  it("clamps above the scale and never draws below the floor", () => {
    const { line } = lanePaths([-5, 50], 10);
    const ys = [...line.matchAll(/[ML] [\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]));
    expect(Math.max(...ys)).toBeLessThanOrEqual(100);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(0);
  });
});

describe("positionIn", () => {
  const window = { start: "2026-11-15T12:00:00Z", end: "2026-11-15T13:00:00Z" };

  it("places a moment across the window", () => {
    expect(positionIn(window, "2026-11-15T12:30:00Z")).toBeCloseTo(0.5);
  });

  it("drops a moment outside the window rather than pinning it to an edge", () => {
    expect(positionIn(window, "2026-11-15T11:59:00Z")).toBeNull();
    expect(positionIn(window, "2026-11-15T13:01:00Z")).toBeNull();
    expect(positionIn(window, "not a time")).toBeNull();
  });
});
