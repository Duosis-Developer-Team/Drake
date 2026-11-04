import { describe, expect, it } from "vitest";

import { signalItems } from "@/lib/overview";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import {
  MAX_SIGNAL_BINDINGS,
  judgeSignal,
  percent,
  rankSignals,
  signalsPath,
  type SignalItem,
  type SignalPage,
} from "@/lib/signals";

function row(key: string, bindingId: string | null): ServiceHealthRow {
  return {
    environment_service_id: `es-${key}`,
    service_key: key,
    display_name: null,
    project_key: "hermes",
    environment_key: "dev",
    binding: bindingId ? { id: bindingId } : null,
    health: { status: "healthy" },
  } as unknown as ServiceHealthRow;
}

function item(id: string, traces: Partial<SignalItem["traces"]> | null, logs?: Partial<SignalItem["logs"]>): SignalItem {
  return {
    binding_id: id,
    traces: traces
      ? { requests: 0, errors: 0, error_ratio: null, p95_ms: null, newest_at: null, ...traces }
      : null,
    logs: logs ? { lines: 0, errors: 0, warnings: 0, newest_at: null, ...logs } : null,
  };
}

function page(items: SignalItem[]): SignalPage {
  return {
    range: "1h",
    as_of: "2026-11-04T12:00:00Z",
    logs: { state: "ok" },
    traces: { state: "ok" },
    items,
  };
}

describe("signalsPath", () => {
  it("is null with nothing to ask, so no request is made", () => {
    expect(signalsPath([])).toBeNull();
  });

  it("dedupes, sorts and caps ids", () => {
    const path = signalsPath(["b", "a", "b"]);
    expect(path).toBe("/v1/service-health/signals?binding_id=a&binding_id=b&range=1h");
    const many = Array.from({ length: MAX_SIGNAL_BINDINGS + 5 }, (_, i) => `id-${String(i).padStart(3, "0")}`);
    expect(new URLSearchParams(signalsPath(many)!.split("?")[1]).getAll("binding_id")).toHaveLength(
      MAX_SIGNAL_BINDINGS,
    );
  });
});

describe("judgeSignal", () => {
  it("does not judge a ratio over too few requests", () => {
    expect(judgeSignal(item("a", { requests: 3, errors: 3, error_ratio: 1 })).cause).toBeNull();
  });

  it("warns from 5% and is critical from 25% failed requests", () => {
    expect(judgeSignal(item("a", { requests: 100, errors: 5, error_ratio: 0.05 }))).toEqual({
      tone: "warning",
      cause: "requests",
    });
    expect(judgeSignal(item("a", { requests: 100, errors: 30, error_ratio: 0.3 })).tone).toBe("critical");
  });

  it("error log lines alone only ever warn", () => {
    expect(judgeSignal(item("a", null, { errors: 5000 }))).toEqual({ tone: "warning", cause: "logs" });
    expect(judgeSignal(item("a", null, { errors: 9 })).cause).toBeNull();
  });
});

describe("rankSignals", () => {
  it("puts the worst first and leaves out services with no readable signal", () => {
    const rows = [row("quiet", "b1"), row("noisy", "b2"), row("failing", "b3"), row("unbound", null), row("denied", "b9")];
    const ranked = rankSignals(
      rows,
      page([
        item("b1", { requests: 50, errors: 0, error_ratio: 0 }),
        item("b2", null, { errors: 40 }),
        item("b3", { requests: 100, errors: 40, error_ratio: 0.4 }),
      ]),
    );
    expect(ranked.map((r) => r.row.service_key)).toEqual(["failing", "noisy", "quiet"]);
  });
});

describe("signalItems", () => {
  it("queues only flagged services, as services, linking to their page", () => {
    const ranked = rankSignals(
      [row("failing", "b3"), row("quiet", "b1")],
      page([item("b3", { requests: 100, errors: 40, error_ratio: 0.4 }), item("b1", { requests: 9 })]),
    );
    const [only, ...rest] = signalItems(ranked);
    expect(rest).toEqual([]);
    expect(only).toMatchObject({
      tone: "critical",
      origin: "service",
      subject: "failing",
      href: "/service-health/b3",
      reason: { kind: "signal", cause: "requests", ratio: 0.4 },
    });
  });
});

describe("percent", () => {
  it("keeps small ratios visible", () => {
    expect(percent(0.004)).toBe("0.4%");
    expect(percent(0.25)).toBe("25%");
    expect(percent(null)).toBe("—");
  });
});
