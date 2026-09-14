import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMediaQuery } from "@/lib/useMediaQuery";

/** Drives `window.matchMedia` for one query string. */
function stubMediaQuery(initialMatch: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: initialMatch,
      media: query,
      addEventListener: (_: string, handler: (event: MediaQueryListEvent) => void) =>
        listeners.add(handler),
      removeEventListener: (_: string, handler: (event: MediaQueryListEvent) => void) =>
        listeners.delete(handler),
    })),
  );
  return {
    flip(next: boolean) {
      for (const handler of listeners) handler({ matches: next } as MediaQueryListEvent);
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useMediaQuery", () => {
  it("reflects the current match on mount", () => {
    stubMediaQuery(true);
    const { result } = renderHook(() => useMediaQuery("(max-width: 1023px)"));
    expect(result.current).toBe(true);
  });

  it("updates when the query's match changes", () => {
    const media = stubMediaQuery(false);
    const { result } = renderHook(() => useMediaQuery("(max-width: 1023px)"));
    expect(result.current).toBe(false);
    act(() => media.flip(true));
    expect(result.current).toBe(true);
  });

  it("defaults to false when matchMedia is unavailable, instead of throwing", () => {
    vi.stubGlobal("matchMedia", undefined);
    const { result } = renderHook(() => useMediaQuery("(max-width: 1023px)"));
    expect(result.current).toBe(false);
  });
});
