/**
 * Value formatting.
 *
 * The rule the whole file exists to hold: `null` is not zero. A missing
 * measurement renders as an em dash everywhere — in a tile, an axis, a
 * tooltip, a table cell — and never as `0`, `0%`, `0 B` or a blank.
 */
import { describe, expect, it } from "vitest";

import {
  MISSING,
  formatRelative,
  formatTimeAxis,
  formatUnit,
  formatUtc,
  formatUtcShort,
} from "@/lib/design/format";

describe("missing values", () => {
  it("renders as a dash for every unit, and never as zero", () => {
    const units = [
      "bytes",
      "cores",
      "count",
      "currency_usd",
      "duration_seconds",
      "milliseconds",
      "percent",
      "ratio",
      "requests_per_second",
      "restarts",
      "seconds",
      "something_unknown",
    ];
    for (const unit of units) {
      for (const value of [null, undefined, Number.NaN]) {
        const rendered = formatUnit(value, unit);
        expect(rendered, `${unit} + ${value}`).toBe(MISSING);
        expect(rendered).not.toMatch(/0/);
      }
    }
  });

  it("renders a measured zero as zero, distinctly from missing", () => {
    expect(formatUnit(0, "count")).toBe("0");
    expect(formatUnit(0, "bytes")).toBe("0 B");
    expect(formatUnit(0, "ratio")).toBe("0%");
    expect(formatUnit(0, "count")).not.toBe(MISSING);
  });
});

describe("units", () => {
  it("scales bytes binary, with the unit attached", () => {
    expect(formatUnit(512, "bytes")).toBe("512 B");
    expect(formatUnit(1024, "bytes")).toBe("1.00 KiB");
    expect(formatUnit(1024 ** 3 * 2.5, "bytes")).toBe("2.50 GiB");
  });

  it("switches duration units at the boundaries a reader expects", () => {
    expect(formatUnit(0.42, "seconds")).toBe("420 ms");
    expect(formatUnit(4.2, "seconds")).toBe("4.20 s");
    expect(formatUnit(90, "seconds")).toBe("1m 30s");
    expect(formatUnit(5400, "seconds")).toBe("1h 30m");
    expect(formatUnit(90_000, "seconds")).toBe("1d 1h");
  });

  it("treats ratio as a fraction and percent as already-scaled", () => {
    expect(formatUnit(0.0345, "ratio")).toBe("3.45%");
    expect(formatUnit(3.45, "percent")).toBe("3.5%");
  });

  it("compacts for an axis but stays exact for a value", () => {
    expect(formatUnit(12_345, "count")).toBe("12,345");
    expect(formatUnit(12_345, "count", { compact: true })).toBe("12.3k");
    expect(formatUnit(2_500_000, "count", { compact: true })).toBe("2.50M");
  });

  it("keeps the sign on a negative", () => {
    expect(formatUnit(-1536, "bytes")).toBe("-1.50 KiB");
    expect(formatUnit(-5, "count")).toBe("-5");
  });
});

describe("timestamps", () => {
  it("prints UTC, always labelled", () => {
    expect(formatUtc("2026-08-11T09:30:15Z")).toBe("2026-08-11 09:30:15 UTC");
    // The label is not optional: an unlabelled instant is a different number
    // to every reader.
    expect(formatUtc("2026-08-11T09:30:15Z")).toMatch(/UTC$/);
  });

  it("returns a dash for absent instants and echoes an unparseable one", () => {
    expect(formatUtc(null)).toBe(MISSING);
    expect(formatUtc(undefined)).toBe(MISSING);
    expect(formatUtc("")).toBe(MISSING);
    expect(formatUtc("not-a-date")).toBe("not-a-date");
  });

  it("drops the year only inside the current one", () => {
    const now = new Date("2026-08-11T00:00:00Z");
    expect(formatUtcShort("2026-03-02T04:05:00Z", now)).toBe("03-02 04:05");
    expect(formatUtcShort("2025-03-02T04:05:00Z", now)).toBe("2025-03-02 04:05");
  });

  it("says just now inside the rounding window, then counts in both directions", () => {
    const now = new Date("2026-08-11T12:00:00Z");
    expect(formatRelative("2026-08-11T11:59:50Z", now)).toBe("just now");
    expect(formatRelative("2026-08-11T11:55:00Z", now)).toBe("5m ago");
    expect(formatRelative("2026-08-11T09:00:00Z", now)).toBe("3h ago");
    expect(formatRelative("2026-08-08T12:00:00Z", now)).toBe("3d ago");
    expect(formatRelative("2026-08-11T14:00:00Z", now)).toBe("in 2h");
    expect(formatRelative(null, now)).toBe(MISSING);
  });

  it("picks an axis tick from the window it has to cover", () => {
    const stamp = Date.UTC(2026, 7, 11, 9, 30);
    expect(formatTimeAxis(stamp, 3600)).toBe("09:30");
    expect(formatTimeAxis(stamp, 86_400)).toBe("08-11 09:30");
    expect(formatTimeAxis(stamp, 30 * 86_400)).toBe("08-11");
  });
});
