"use client";

import Link from "next/link";

import { LifecycleBadge, LoadGate, useApi } from "@/components/catalog/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import type { Cluster } from "@/lib/catalog";

export default function ClustersPage() {
  const [clusters, retry] = useApi<{ clusters: Cluster[]; next_cursor: string | null }>("/v1/clusters");

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Clusters</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Cluster catalog. Live inventory and agent health arrive with the
          inventory sprint.
        </p>
      </div>
      <Card>
        <LoadGate value={clusters} retry={retry}>
          {(body) =>
            body.clusters.length === 0 ? (
              <DataState
                kind="empty"
                title="No clusters in your scope"
                description="Clusters you are authorized to see will appear here."
              />
            ) : (
              <ul className="divide-y divide-border" data-testid="cluster-list">
                {body.clusters.map((cluster) => (
                  <li key={cluster.id}>
                    <Link
                      href={`/clusters/${cluster.id}`}
                      className="flex items-center justify-between gap-3 px-1 py-3 hover:bg-surface-sunken"
                    >
                      <span>
                        <span className="block text-sm font-medium text-ink">
                          {cluster.display_name}
                        </span>
                        <span className="block font-mono text-xs text-ink-muted">
                          {cluster.cluster_ref}
                          {cluster.site ? ` · ${cluster.site}` : ""}
                        </span>
                      </span>
                      <LifecycleBadge lifecycle={cluster.lifecycle} />
                    </Link>
                  </li>
                ))}
              </ul>
            )
          }
        </LoadGate>
      </Card>
    </div>
  );
}
