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

import {
  AlertTriangle,
  ChevronRight,
  Clock,
  Layers,
  Plug,
  RefreshCw,
  Server,
} from "lucide-react";
import Link from "next/link";

import {
  IconBubble,
  type Segment,
  SegmentBar,
  ShareBar,
  StatTile,
  StateCard,
} from "@/components/clusters/primitives";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
} from "@/components/ui/states";
import type { Cluster } from "@/lib/catalog";
import {
  compareTone,
  humanize,
  toneForHealth,
  type StatusTone,
} from "@/lib/design/status";
import { needsAttention } from "@/lib/overview";
import { useResource } from "@/lib/useResource";

/**
 * The two axes speak the agent's vocabulary, not the integration one.
 *
 * `connected`/`disconnected` for the agent, `fresh`/`stale` for the sweep —
 * the shared status map already knows both, so neither needs translating
 * here. (An earlier version compared against `"ok"`, which the corrected type
 * on `Cluster.operational` now proves could never match.)
 */
function agentTone(cluster: Cluster) {
  return toneForHealth(cluster.operational?.agent);
}

function inventoryTone(cluster: Cluster) {
  return toneForHealth(cluster.operational?.inventory);
}

/** The worse of the two claims, only when it is worth a colour. */
function attentionTone(cluster: Cluster): StatusTone | null {
  const tones = [agentTone(cluster), inventoryTone(cluster)].filter(
    needsAttention,
  );
  if (tones.length === 0) return null;
  return tones.sort(compareTone).at(-1) ?? null;
}

/** Clusters grouped by the word the API reported, in first-seen order. */
function groupBy(
  clusters: Cluster[],
  pick: (cluster: Cluster) => string | undefined,
): Segment[] {
  const counts = new Map<string, number>();
  for (const cluster of clusters) {
    const key = pick(cluster) ?? "unknown";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].map(([key, value]) => ({
    key,
    label: humanize(key),
    value,
    tone: toneForHealth(key),
  }));
}

function ClusterRow({ cluster }: { cluster: Cluster }) {
  const tone = attentionTone(cluster);
  const environments = cluster.referenced_environments?.length ?? 0;
  return (
    <li className="relative flex flex-wrap items-center gap-x-5 gap-y-3 px-7 py-5 transition-colors hover:bg-surface-hover">
      <IconBubble icon={Server} tone={tone} />

      <div className="min-w-[9rem] flex-1">
        <div className="flex flex-wrap items-center gap-2">
          {/* The name is the row's one real link; its ::after covers the row
              so the whole card is the click target without nesting links. */}
          <Link
            href={`/clusters/${cluster.id}`}
            className="rounded font-semibold text-ink after:absolute after:inset-0 after:content-[''] hover:text-brand"
          >
            {cluster.display_name || cluster.cluster_ref}
          </Link>
          <StatusBadge
            status={cluster.lifecycle === "active" ? "success" : "neutral"}
            label={humanize(cluster.lifecycle)}
            size="compact"
          />
        </div>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-muted">
          <span className="font-mono">
            {cluster.cluster_ref}
            {cluster.site ? ` · ${cluster.site}` : ""}
          </span>
          <span
            className="inline-flex items-center gap-1"
            title="Authorized environments on this cluster"
          >
            <Layers aria-hidden className="h-3.5 w-3.5" />
            <span data-tabular>{environments}</span>
            {environments === 1 ? "environment" : "environments"}
          </span>
          <span
            className="inline-flex items-center gap-1"
            title="When this record was observed"
          >
            <Clock aria-hidden className="h-3.5 w-3.5" />
            <RelativeTime value={cluster.as_of} />
          </span>
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-2 rounded-full bg-surface-2 py-1 pr-1 pl-3 text-micro">
          <span className="text-ink-muted">Agent</span>
          <StatusBadge
            status={agentTone(cluster)}
            label={humanize(cluster.operational?.agent ?? "unknown")}
            size="compact"
          />
        </span>
        <span className="inline-flex items-center gap-2 rounded-full bg-surface-2 py-1 pr-1 pl-3 text-micro">
          <span className="text-ink-muted">Inventory</span>
          <StatusBadge
            status={inventoryTone(cluster)}
            label={humanize(cluster.operational?.inventory ?? "unknown")}
            size="compact"
          />
        </span>
      </div>

      <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-ink-muted" />
    </li>
  );
}

export default function ClustersPage() {
  const resource = useResource<{
    clusters: Cluster[];
    next_cursor: string | null;
  }>("/v1/clusters", { refreshMs: 60_000 });
  const clusters = resource.data?.clusters ?? [];

  const attention = clusters.filter(
    (cluster) => attentionTone(cluster) !== null,
  );
  const connected = clusters.filter(
    (cluster) => cluster.operational?.agent === "connected",
  );
  const fresh = clusters.filter(
    (cluster) => cluster.operational?.inventory === "fresh",
  );
  const total = clusters.length;

  return (
    <PageFrame>
      <PageHeader
        title="Clusters"
        description="Agent connection and inventory freshness — two separate claims, each observed."
      />

      {resource.data && total > 0 ? (
        <div className="page-grid mb-6">
          <StatTile
            icon={Server}
            label="In scope"
            value={total}
            suffix={total === 1 ? "cluster" : "clusters"}
          >
            <SegmentBar
              label="Clusters by lifecycle"
              segments={groupBy(clusters, (cluster) => cluster.lifecycle).map(
                (segment) => ({
                  ...segment,
                  tone: segment.key === "active" ? "info" : "neutral",
                }),
              )}
            />
          </StatTile>
          <StatTile
            icon={AlertTriangle}
            tone={attention.length > 0 ? "warning" : null}
            label="Need attention"
            value={attention.length}
            suffix={`of ${total}`}
          >
            <ShareBar
              label="Clusters needing attention"
              value={attention.length}
              total={total}
              tone="warning"
            />
          </StatTile>
          <StatTile
            icon={Plug}
            label="Agent connected"
            value={connected.length}
            suffix={`of ${total}`}
          >
            <ShareBar
              label="Agents connected"
              value={connected.length}
              total={total}
              tone="success"
            />
          </StatTile>
          <StatTile
            icon={RefreshCw}
            label="Inventory fresh"
            value={fresh.length}
            suffix={`of ${total}`}
          >
            <ShareBar
              label="Inventories fresh"
              value={fresh.length}
              total={total}
              tone="success"
            />
          </StatTile>
        </div>
      ) : null}

      <div
        className={resource.data && total > 0 ? "page-split" : "flex flex-col"}
      >
        <div className="page-main">
          <Panel flush>
            <PanelHeader
              flush
              title="Fleet"
              description="Every cluster in your authorized scope"
              actions={
                resource.data ? (
                  <span
                    data-tabular
                    className="rounded-full bg-surface-2 px-3 py-1 text-micro font-medium text-ink-secondary"
                  >
                    {total} shown
                  </span>
                ) : undefined
              }
            />
            {resource.loading && !resource.data ? (
              <div className="px-7 py-5">
                <LoadingSkeleton
                  variant="table"
                  rows={4}
                  label="Loading clusters"
                />
              </div>
            ) : resource.denied ? (
              <div className="px-7 py-4">
                <DeniedState />
              </div>
            ) : !resource.data ? (
              <div className="px-7 py-4">
                <ErrorState
                  description={resource.error ?? undefined}
                  correlationId={resource.correlationId}
                  onRetry={resource.reload}
                />
              </div>
            ) : (
              <div data-testid="cluster-list">
                {total === 0 ? (
                  <StateCard
                    testId="state-empty"
                    icon={Server}
                    title="No clusters in your scope"
                    description="Clusters you are authorized to see appear here once they are registered in the catalog."
                  />
                ) : (
                  <ul
                    aria-label="Clusters in your authorized scope, with agent connection and inventory freshness"
                    className="divide-y divide-border"
                  >
                    {clusters.map((cluster) => (
                      <ClusterRow key={cluster.id} cluster={cluster} />
                    ))}
                  </ul>
                )}
              </div>
            )}
          </Panel>
        </div>

        {resource.data && total > 0 ? (
          <div className="page-aside">
            <Panel data-testid="fleet-signals">
              <PanelHeader
                title="Fleet signals"
                description="How the fleet splits on each claim"
              />
              <div className="space-y-6">
                <div>
                  <p className="mb-2.5 flex items-center justify-between text-caption font-medium text-ink">
                    <span className="flex items-center gap-2">
                      <Plug aria-hidden className="h-4 w-4 text-ink-muted" />
                      Agent connection
                    </span>
                  </p>
                  <SegmentBar
                    label="Clusters by agent connection"
                    segments={groupBy(
                      clusters,
                      (cluster) => cluster.operational?.agent,
                    )}
                  />
                </div>
                <div className="border-t border-border pt-6">
                  <p className="mb-2.5 flex items-center gap-2 text-caption font-medium text-ink">
                    <RefreshCw aria-hidden className="h-4 w-4 text-ink-muted" />
                    Inventory freshness
                  </p>
                  <SegmentBar
                    label="Clusters by inventory freshness"
                    segments={groupBy(
                      clusters,
                      (cluster) => cluster.operational?.inventory,
                    )}
                  />
                </div>
                <div className="flex items-center justify-between border-t border-border pt-6">
                  <span className="flex items-center gap-2 text-caption font-medium text-ink">
                    <Layers aria-hidden className="h-4 w-4 text-ink-muted" />
                    Environments referenced
                  </span>
                  <span
                    data-tabular
                    className="text-[1.5rem] leading-none font-semibold tracking-[-0.02em] text-ink"
                  >
                    {clusters.reduce(
                      (sum, cluster) =>
                        sum + (cluster.referenced_environments?.length ?? 0),
                      0,
                    )}
                  </span>
                </div>
              </div>
            </Panel>
          </div>
        ) : null}
      </div>
    </PageFrame>
  );
}
