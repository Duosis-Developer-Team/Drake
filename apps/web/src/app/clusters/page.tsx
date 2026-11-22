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

import { AlertTriangle, Plug, RefreshCw, Server } from "lucide-react";

import {
  type Segment,
  SegmentBar,
  ShareBar,
  StatTile,
  StateCard,
} from "@/components/clusters/primitives";
import { ClusterCard } from "@/components/clusters/ClusterCard";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel } from "@/components/ui/Panel";
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
import { useT, type Translator } from "@/lib/i18n";
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

/**
 * Clusters grouped by the word the API reported, in first-seen order. The
 * label is the catalogue's word for that token; an unexpected token still
 * shows, humanized, rather than vanishing.
 */
function groupBy(
  clusters: Cluster[],
  pick: (cluster: Cluster) => string | undefined,
  labelOf: (key: string) => string,
): Segment[] {
  const counts = new Map<string, number>();
  for (const cluster of clusters) {
    const key = pick(cluster) ?? "unknown";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].map(([key, value]) => ({
    key,
    label: labelOf(key),
    value,
    tone: toneForHealth(key),
  }));
}

/** `t.dyn` over one enum group, with the humanized token as the fallback. */
function enumLabel(t: Translator<"clusters">, group: string) {
  return (key: string) => t.dyn(`enum.${group}`, key, humanize(key));
}

export default function ClustersPage() {
  const t = useT("clusters");
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
      <PageHeader title={t("list.title")} description={t("list.description")} />

      {resource.data && total > 0 ? (
        <div className="page-grid mb-6">
          <StatTile
            icon={Server}
            label={t("list.stat.inScope")}
            value={total}
            suffix={t("list.stat.clusters", { count: total })}
          >
            <SegmentBar
              label={t("list.stat.byLifecycle")}
              segments={groupBy(
                clusters,
                (cluster) => cluster.lifecycle,
                enumLabel(t, "lifecycle"),
              ).map((segment) => ({
                ...segment,
                tone: segment.key === "active" ? "info" : "neutral",
              }))}
            />
          </StatTile>
          <StatTile
            icon={AlertTriangle}
            tone={attention.length > 0 ? "warning" : null}
            label={t("list.stat.needAttention")}
            value={attention.length}
            suffix={t("list.stat.ofTotal", { total })}
          >
            <ShareBar
              label={t("list.stat.needingAttention")}
              value={attention.length}
              total={total}
              tone="warning"
            />
          </StatTile>
          <StatTile
            icon={Plug}
            label={t("list.stat.agentConnected")}
            value={connected.length}
            suffix={t("list.stat.ofTotal", { total })}
          >
            <ShareBar
              label={t("list.stat.agentsConnected")}
              value={connected.length}
              total={total}
              tone="success"
            />
          </StatTile>
          <StatTile
            icon={RefreshCw}
            label={t("list.stat.inventoryFresh")}
            value={fresh.length}
            suffix={t("list.stat.ofTotal", { total })}
          >
            <ShareBar
              label={t("list.stat.inventoriesFresh")}
              value={fresh.length}
              total={total}
              tone="success"
            />
          </StatTile>
        </div>
      ) : null}

      {resource.loading && !resource.data ? (
        <LoadingSkeleton variant="table" rows={4} label={t("list.fleet.loading")} />
      ) : resource.denied ? (
        <DeniedState />
      ) : !resource.data ? (
        <ErrorState
          description={resource.error ?? undefined}
          correlationId={resource.correlationId}
          onRetry={resource.reload}
        />
      ) : (
        <section data-testid="cluster-list" aria-label={t("list.fleet.title")}>
          <div className="mb-3 flex items-baseline justify-between gap-3 px-0.5">
            <div>
              <h2 className="text-[1.125rem] font-semibold tracking-[-0.02em] text-ink">{t("list.fleet.title")}</h2>
              <p className="text-caption text-ink-muted">{t("list.fleet.description")}</p>
            </div>
            <span data-tabular className="rounded-full bg-surface-2 px-3 py-1 text-micro font-medium text-ink-secondary">
              {t("list.fleet.shown", { count: total })}
            </span>
          </div>
          {total === 0 ? (
            <Panel>
              <StateCard
                testId="state-empty"
                icon={Server}
                title={t("list.fleet.emptyTitle")}
                description={t("list.fleet.emptyDescription")}
              />
            </Panel>
          ) : (
            <ul
              aria-label={t("list.fleet.listLabel")}
              className={`grid gap-5 ${total > 1 ? "xl:grid-cols-2" : ""}`}
            >
              {clusters.map((cluster) => (
                <ClusterCard key={cluster.id} cluster={cluster} />
              ))}
            </ul>
          )}
        </section>
      )}
    </PageFrame>
  );
}
