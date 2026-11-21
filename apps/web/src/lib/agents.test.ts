import { describe, expect, it } from "vitest";

import {
  callsOf,
  compact,
  errorRatio,
  providerColor,
  providerName,
  providerSlot,
  runRows,
  stepLabel,
  timeShare,
  type RunSpan,
} from "@/lib/agents";

const totals = (over: Partial<Record<string, number>> = {}) => ({
  totals: {
    invocations: 2,
    inference_calls: 5,
    tool_calls: 3,
    errors: 1,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    reasoning_tokens: 0,
    duration_ms: 0,
    ...over,
  },
});

describe("agents helpers", () => {
  it("keeps a provider's colour fixed, known or not", () => {
    expect(providerSlot("anthropic")).toBe(6);
    expect(providerSlot("OpenAI")).toBe(3);
    const slot = providerSlot("some.new.provider");
    expect(slot).toBeGreaterThanOrEqual(1);
    expect(slot).toBeLessThanOrEqual(6);
    expect(providerSlot("some.new.provider")).toBe(slot);
    expect(providerColor("anthropic")).toBe("var(--series-6)");
  });

  it("names providers, and falls back to the raw value", () => {
    expect(providerName("aws.bedrock")).toBe("Bedrock");
    expect(providerName("acme")).toBe("acme");
    expect(providerName(null)).toBe("—");
  });

  it("counts runs, model and tool calls; failure ratio only when there were calls", () => {
    expect(callsOf(totals())).toBe(10);
    expect(errorRatio(totals())).toBeCloseTo(0.1);
    expect(
      errorRatio(totals({ invocations: 0, inference_calls: 0, tool_calls: 0 })),
    ).toBeNull();
  });

  it("shortens big numbers", () => {
    expect(compact(1_234_567, "en")).toBe("1.2M");
    expect(compact(42, "en")).toBe("42");
  });
});

function step(
  span_id: string,
  parent_id: string | null,
  offset_ms: number,
  duration_ms: number,
  over: Partial<RunSpan> = {},
): RunSpan {
  return {
    span_id,
    parent_id,
    name: span_id,
    step: "other",
    service: "svc",
    agent: null,
    offset_ms,
    duration_ms,
    status: "ok",
    provider: null,
    model: null,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    reasoning_tokens: 0,
    tool: null,
    tool_type: null,
    mcp_method: null,
    server: null,
    error_type: null,
    http_status: null,
    http_route: null,
    ...over,
  };
}

describe("run helpers", () => {
  const spans = [
    step("tool", "root", 300, 100, { step: "tool", tool: "get_ticket" }),
    step("root", null, 0, 1000, {
      step: "agent",
      agent: { id: "a", name: "triager", self: true },
    }),
    step("chat", "root", 10, 200, { step: "model", model: "gpt-5" }),
    step("orphan", "gone", 500, 50),
    step("inner", "tool", 320, 20),
  ];

  it("orders steps under their parents by start, orphans as roots", () => {
    const rows = runRows(spans);
    expect(rows.map((r) => [r.span.span_id, r.depth])).toEqual([
      ["root", 0],
      ["chat", 1],
      ["tool", 1],
      ["inner", 2],
      ["orphan", 0],
    ]);
    expect(rows.find((r) => r.span.span_id === "inner")?.path).toEqual([
      "root",
      "tool",
    ]);
    expect(rows[0].children).toBe(2);
  });

  it("names a step by what it called", () => {
    expect(spans.map(stepLabel)).toEqual([
      "get_ticket",
      "triager",
      "gpt-5",
      "orphan",
      "inner",
    ]);
  });

  it("splits time without counting overlapping calls twice", () => {
    const parallel = [
      step("a", null, 0, 400, { step: "model" }),
      step("b", null, 100, 400, { step: "model" }),
      step("c", null, 600, 100, { step: "tool" }),
    ];
    expect(timeShare(parallel, 1000)).toEqual({
      model: 500,
      tool: 100,
      other: 400,
    });
    expect(timeShare([], 0)).toEqual({ model: 0, tool: 0, other: 0 });
  });
});
