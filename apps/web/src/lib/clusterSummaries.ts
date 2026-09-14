"use client";

/**
 * One inventory-summary fetch per cluster, batched behind a single hook.
 *
 * `FleetCounts` (`app/page.tsx`) keeps its own independent per-row
 * `useResource` call rather than being rewired onto this map — that panel
 * already has full test coverage and its own well-understood per-row
 * loading/error rendering, and the risk of destabilizing it outweighs
 * removing a second N+1 fetch set. This hook exists for panels, like the
 * capacity-risk board, that need every cluster's summary as one collection
 * rather than one row at a time.
 *
 * A plain loop of `useResource(...)` calls was rejected here on purpose: the
 * number of clusters changes across renders (zero while `/v1/clusters` is
 * still loading, then N once it resolves), and calling a hook a
 * variable number of times breaks React's hook-call-order guarantee. This
 * hook instead owns a single `useState`/`useEffect` pair and fetches every
 * summary itself.
 */
import { useEffect, useState } from "react";
import { ApiError, apiGet } from "@/lib/api";
import type { Cluster } from "@/lib/catalog";
import type { InventorySummary } from "@/lib/inventory";

export interface ClusterSummaryState {
  data: InventorySummary | null;
  loading: boolean;
  denied: boolean;
}

export function useClusterInventorySummaries(
  clusters: Cluster[],
): Map<string, ClusterSummaryState> {
  const ids = clusters.map((cluster) => cluster.id).join(",");
  const [state, setState] = useState<Map<string, ClusterSummaryState>>(new Map());

  useEffect(() => {
    if (!ids) {
      setState(new Map());
      return;
    }
    const clusterIds = ids.split(",");
    const controller = new AbortController();
    setState(new Map(clusterIds.map((id) => [id, { data: null, loading: true, denied: false }])));

    Promise.all(
      clusterIds.map((id) =>
        apiGet<InventorySummary>(`/v1/clusters/${id}/inventory/summary`, controller.signal)
          .then((data): [string, ClusterSummaryState] => [id, { data, loading: false, denied: false }])
          .catch((cause: unknown): [string, ClusterSummaryState] => [
            id,
            {
              data: null,
              loading: false,
              denied: cause instanceof ApiError && (cause.status === 403 || cause.status === 401),
            },
          ]),
      ),
    ).then((entries) => {
      if (controller.signal.aborted) return;
      setState(new Map(entries));
    });

    return () => controller.abort();
  }, [ids]);

  return state;
}
