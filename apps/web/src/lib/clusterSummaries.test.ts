import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useClusterInventorySummaries } from "@/lib/clusterSummaries";
import { installFetchMock } from "@/test/mock-api";
import type { Cluster } from "@/lib/catalog";

function cluster(id: string): Cluster {
  return {
    id,
    cluster_ref: `ref-${id}`,
    display_name: `Cluster ${id}`,
    site: "site-1",
    lifecycle: "active",
    version: 1,
    scope: { type: "cluster", ref: `ref-${id}` },
    source: { kind: "operator", ref: "operator:-", revision: "v1", accepted_at: "2026-08-11T00:00:00Z" },
    operational: { agent: "connected", inventory: "fresh" },
    as_of: "2026-08-11T00:00:00Z",
  } as Cluster;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useClusterInventorySummaries", () => {
  it("fetches one summary per cluster and keys the result by cluster id", async () => {
    installFetchMock({
      "/v1/clusters/c1/inventory/summary": { status: 200, body: { cluster_id: "c1" } },
      "/v1/clusters/c2/inventory/summary": { status: 200, body: { cluster_id: "c2" } },
    });
    const { result } = renderHook(() => useClusterInventorySummaries([cluster("c1"), cluster("c2")]));

    await waitFor(() => expect(result.current.get("c1")?.loading).toBe(false));
    expect(result.current.get("c1")?.data).toEqual({ cluster_id: "c1" });
    expect(result.current.get("c2")?.data).toEqual({ cluster_id: "c2" });
  });

  it("marks a denied cluster as denied, not as missing data", async () => {
    installFetchMock({
      "/v1/clusters/c1/inventory/summary": {
        status: 403,
        body: { error: { code: "forbidden", message: "denied" } },
      },
    });
    const { result } = renderHook(() => useClusterInventorySummaries([cluster("c1")]));

    await waitFor(() => expect(result.current.get("c1")?.loading).toBe(false));
    expect(result.current.get("c1")?.denied).toBe(true);
    expect(result.current.get("c1")?.data).toBeNull();
  });

  it("returns an empty map for an empty cluster list", () => {
    const { result } = renderHook(() => useClusterInventorySummaries([]));
    expect(result.current.size).toBe(0);
  });
});
