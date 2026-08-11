"use client";

/**
 * Clusters.
 *
 * Connection and health are separate columns, and that is the whole design.
 * "The agent is connected" and "the cluster is well" are different claims:
 * an agent can be connected while its inventory is an hour stale, and a
 * disconnected agent does not mean the cluster is unhealthy — it means Drake
 * cannot see it. Folding those into one badge is how an operator ends up
 * believing a silent cluster is a healthy one.
 *
 * The strip at the top counts what needs attention, so a fleet of forty does
 * not have to be read row by row to find the two that are stale.
 */

import Link from "next/link";

import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Panel } from "@/components/ui/Panel";
import { CountRow } from "@/components/ui/Stat";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { RelativeTime } from "@/components/ui/identifiers";
import { DeniedState, EmptyState, ErrorState, LoadingSkeleton } from "@/components/ui/states";
import type { Cluster } from "@/lib/catalog";
import { humanize, toneForHealth, toneSpec } from "@/lib/design/status";
import { needsAttention } from "@/lib/overview";
import { useResource } from "@/lib/useResource";

/** `ok` means different things on the two axes; name each one properly. */
function agentTone(cluster: Cluster) {
  const value = cluster.operational?.agent;
  return toneForHealth(value === "ok" ? "connected" : value);
}

function inventoryTone(cluster: Cluster) {
  const value = cluster.operational?.inventory;
  return toneForHealth(value === "ok" ? "fresh" : value);
}

export default function ClustersPage() {
  const resource = useResource<{ clusters: Cluster[]; next_cursor: string | null }>(
    "/v1/clusters",
    { refreshMs: 60_000 },
  );
  const clusters = resource.data?.clusters ?? [];

  const attention = clusters.filter(
    (cluster) => needsAttention(agentTone(cluster)) || needsAttention(inventoryTone(cluster)),
  );
  const connected = clusters.filter((cluster) => cluster.operational?.agent === "ok");
  const fresh = clusters.filter((cluster) => cluster.operational?.inventory === "ok");

  const columns: Column<Cluster>[] = [
    {
      key: "cluster",
      header: "Cluster",
      cell: (cluster) => (
        <>
          <Link
            href={`/clusters/${cluster.id}`}
            className="rounded font-medium text-ink hover:text-brand"
          >
            {cluster.display_name || cluster.cluster_ref}
          </Link>
          <span className="block font-mono text-micro text-ink-muted">
            {cluster.cluster_ref}
            {cluster.site ? ` · ${cluster.site}` : ""}
          </span>
        </>
      ),
    },
    {
      key: "agent",
      header: "Agent connection",
      cell: (cluster) => (
        <StatusBadge
          status={agentTone(cluster)}
          label={humanize(cluster.operational?.agent ?? "unknown")}
          size="compact"
        />
      ),
    },
    {
      key: "inventory",
      header: "Inventory",
      cell: (cluster) => (
        <StatusBadge
          status={inventoryTone(cluster)}
          label={humanize(cluster.operational?.inventory ?? "unknown")}
          size="compact"
        />
      ),
    },
    {
      key: "environments",
      header: "Environments",
      priority: "low",
      align: "right",
      cell: (cluster) => cluster.referenced_environments?.length ?? 0,
    },
    {
      key: "lifecycle",
      header: "Lifecycle",
      priority: "low",
      cell: (cluster) => (
        <StatusBadge
          status={cluster.lifecycle === "active" ? "success" : "neutral"}
          label={humanize(cluster.lifecycle)}
          size="compact"
        />
      ),
    },
    {
      key: "observed",
      header: "Observed",
      align: "right",
      cell: (cluster) => (
        <span className="text-micro text-ink-muted">
          <RelativeTime value={cluster.as_of} />
        </span>
      ),
    },
  ];

  return (
    <PageFrame>
      <PageHeader
        title="Clusters"
        description="Agent connectivity and inventory freshness, each observed rather than assumed. A connected agent is not the same claim as a healthy cluster."
      />

      {resource.data && clusters.length > 0 ? (
        <div className="mb-4">
          <CountRow
            total={{ label: "in scope", count: clusters.length }}
            items={[
              { key: "attention", label: "need attention", count: attention.length, tone: attention.length > 0 ? "warning" : "neutral" },
              { key: "connected", label: "agent connected", count: connected.length, tone: "success" },
              { key: "fresh", label: "inventory fresh", count: fresh.length, tone: "success" },
            ]}
          />
        </div>
      ) : null}

      <Panel flush>
        {resource.loading && !resource.data ? (
          <div className="px-4 py-4">
            <LoadingSkeleton variant="table" rows={4} label="Loading clusters" />
          </div>
        ) : resource.denied ? (
          <div className="px-4 py-2">
            <DeniedState />
          </div>
        ) : !resource.data ? (
          <div className="px-4 py-2">
            <ErrorState
              description={resource.error ?? undefined}
              correlationId={resource.correlationId}
              onRetry={resource.reload}
            />
          </div>
        ) : (
          <div data-testid="cluster-list">
            <DataTable
              caption="Clusters in your authorized scope, with agent connection and inventory freshness"
              rows={clusters}
              columns={columns}
              rowKey={(cluster) => cluster.id}
              rowTone={(cluster) => {
                const worst = needsAttention(agentTone(cluster))
                  ? agentTone(cluster)
                  : needsAttention(inventoryTone(cluster))
                    ? inventoryTone(cluster)
                    : null;
                return worst ? toneSpec(worst).rail : null;
              }}
              emptyState={
                <EmptyState
                  title="No clusters in your scope"
                  description="Clusters you are authorized to see appear here once they are registered in the catalog."
                />
              }
            />
          </div>
        )}
      </Panel>
    </PageFrame>
  );
}
