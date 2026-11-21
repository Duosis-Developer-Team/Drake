import { describe, expect, it } from "vitest";

import { callsOf, compact, errorRatio, providerColor, providerName, providerSlot } from "@/lib/agents";

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
    expect(errorRatio(totals({ invocations: 0, inference_calls: 0, tool_calls: 0 }))).toBeNull();
  });

  it("shortens big numbers", () => {
    expect(compact(1_234_567, "en")).toBe("1.2M");
    expect(compact(42, "en")).toBe("42");
  });
});
