import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useProgressiveCollection, type PageSlice } from "@/lib/useProgressiveCollection";
import { installFetchMock } from "@/test/mock-api";

interface StringPage {
  items: string[];
  next: string | null;
}

function pageToSlice(page: StringPage): PageSlice<string> {
  return { items: page.items, nextPath: page.next };
}

describe("useProgressiveCollection", () => {
  it("loads only the first page until loadMore is called", async () => {
    installFetchMock({
      "/page-1": { status: 200, body: { items: ["a"], next: "/page-2" } },
      "/page-2": { status: 200, body: { items: ["b"], next: null } },
    });
    const { result } = renderHook(() =>
      useProgressiveCollection<StringPage, string>({ firstPath: "/page-1", pageToSlice }),
    );
    await waitFor(() => expect(result.current.items).toEqual(["a"]));
    expect(result.current.complete).toBe(false);

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.items).toEqual(["a", "b"]));
    expect(result.current.complete).toBe(true);
  });

  it("retains first-page rows and reports complete:false after a later-page failure", async () => {
    installFetchMock({
      "/page-1": { status: 200, body: { items: ["a"], next: "/page-2" } },
      "/page-2": { status: 500, body: { error: { code: "error", message: "boom" } } },
    });
    const { result } = renderHook(() =>
      useProgressiveCollection<StringPage, string>({ firstPath: "/page-1", pageToSlice }),
    );
    await waitFor(() => expect(result.current.items).toEqual(["a"]));

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.loadingMore).toBe(false));

    expect(result.current.items).toEqual(["a"]);
    expect(result.current.complete).toBe(false);
  });

  it("clears the old scope's rows and starts fresh loading when firstPath changes", async () => {
    installFetchMock({
      "/scope-a": { status: 200, body: { items: ["a"], next: null } },
      "/scope-b": { status: 200, body: { items: ["b"], next: null } },
    });
    const { result, rerender } = renderHook(
      ({ firstPath }: { firstPath: string }) =>
        useProgressiveCollection<StringPage, string>({ firstPath, pageToSlice }),
      { initialProps: { firstPath: "/scope-a" } },
    );
    await waitFor(() => expect(result.current.items).toEqual(["a"]));

    rerender({ firstPath: "/scope-b" });
    // The old scope's row must not render under the new scope's heading, even
    // for a frame, and the fresh fetch must not be mistaken for a refresh of
    // the same scope.
    expect(result.current.items).toEqual([]);
    expect(result.current.loading).toBe(true);
    expect(result.current.refreshing).toBe(false);

    await waitFor(() => expect(result.current.items).toEqual(["b"]));
  });

  it("keeps last-good rows visible while a same-scope reload is refreshing", async () => {
    installFetchMock({
      "/scope": { status: 200, body: { items: ["a"], next: null } },
    });
    const { result } = renderHook(() =>
      useProgressiveCollection<StringPage, string>({ firstPath: "/scope", pageToSlice }),
    );
    await waitFor(() => expect(result.current.items).toEqual(["a"]));

    act(() => result.current.reload());
    expect(result.current.items).toEqual(["a"]);
    expect(result.current.refreshing).toBe(true);

    await waitFor(() => expect(result.current.refreshing).toBe(false));
    expect(result.current.items).toEqual(["a"]);
  });

  it("ignores a repeated loadMore call while one is already in flight", async () => {
    let resolvePage2: (value: Response) => void = () => {};
    const page2Promise = new Promise<Response>((resolve) => {
      resolvePage2 = resolve;
    });
    let page2Calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input).split("?")[0];
        if (path === "/page-1") {
          return new Response(JSON.stringify({ items: ["a"], next: "/page-2" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (path === "/page-2") {
          page2Calls += 1;
          return page2Promise;
        }
        return new Response("{}", { status: 404 });
      }),
    );

    const { result } = renderHook(() =>
      useProgressiveCollection<StringPage, string>({ firstPath: "/page-1", pageToSlice }),
    );
    await waitFor(() => expect(result.current.items).toEqual(["a"]));

    act(() => {
      result.current.loadMore();
      result.current.loadMore();
    });
    expect(page2Calls).toBe(1);

    resolvePage2(
      new Response(JSON.stringify({ items: ["b"], next: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await waitFor(() => expect(result.current.items).toEqual(["a", "b"]));
    expect(page2Calls).toBe(1);
  });
});
