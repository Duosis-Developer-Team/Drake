"use client";

/**
 * A cluster at a glance — one design for the Overview's fleet panel and the
 * Clusters page: whose machines these are, whether the agent answers and
 * the sweep is current, how much of the machines is in use, the machines
 * themselves, and the apps that run on them.
 *
 * Connection and freshness stay two separate claims, in the agent's own
 * words. Usage comes from the infrastructure snapshot and is drawn only
 * where both the use and the capacity behind it were reported.
 */

import { ArrowUpRight, Server } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { RingProgress } from "@/components/charts/visuals";
import { useResolvedTheme } from "@/components/shell/ThemeControl";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { RelativeTime } from "@/components/ui/identifiers";
import type { Cluster } from "@/lib/catalog";
import { clusterPicture, type ClusterPicture, type Usage } from "@/lib/clusterPicture";
import { humanize, toneForHealth } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import { GiB, fetchSnapshot, type InfraSnapshot, type NodeSnapshot } from "@/lib/infrastructure";
import type { InventorySummary } from "@/lib/inventory";
import { envRank } from "@/lib/platform";
import { useResource } from "@/lib/useResource";

/* ---------------- whose machines ---------------- */

/** Duosis's own wordmark, from the Duosis website's brand files. */
export function DuosisLogo({ className = "" }: { className?: string }) {
  const theme = useResolvedTheme();
  return (
    <img
      src={theme === "dark" ? "/owners/duosis-logo-dark.svg" : "/owners/duosis-logo-light.svg"}
      alt="Duosis" // i18n-ignore: a company name
      className={`h-auto object-contain ${className}`}
    />
  );
}

export function isDuosis(cluster: { cluster_ref?: string; ref?: string; site?: string }): boolean {
  return /duosis/i.test(`${cluster.cluster_ref ?? cluster.ref ?? ""} ${cluster.site ?? ""}`);
}

/** The cluster's tile: its owner's logo when known, a server otherwise. */
export function ClusterLogo({ cluster, size = "md" }: { cluster: Cluster; size?: "md" | "lg" }) {
  const box = size === "lg" ? "h-20 w-36 rounded-[1.25rem] px-4" : "h-14 w-24 rounded-2xl px-3";
  return (
    <span
      aria-hidden
      className={`grid shrink-0 place-items-center border border-border bg-gradient-to-b from-surface to-surface-2 ${box}`}
      style={{ boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.06), 0 10px 22px -16px rgb(0 0 0 / 0.5)" }}
    >
      {isDuosis(cluster) ? (
        <DuosisLogo className="w-full" />
      ) : (
        <Server className={size === "lg" ? "h-8 w-8 text-ink-secondary" : "h-6 w-6 text-ink-secondary"} />
      )}
    </span>
  );
}

/* ---------------- the snapshot ---------------- */

/** The cluster's infrastructure snapshot, read once per mount. */
export function useClusterSnapshot(clusterId: string | null): { snap: InfraSnapshot | null; failed: boolean } {
  const [state, setState] = useState<{ id: string | null; snap: InfraSnapshot | null; failed: boolean }>({
    id: null,
    snap: null,
    failed: false,
  });
  useEffect(() => {
    if (!clusterId) return;
    const controller = new AbortController();
    fetchSnapshot(clusterId, controller.signal)
      .then((snap) => setState({ id: clusterId, snap, failed: false }))
      .catch(() => {
        if (!controller.signal.aborted) setState({ id: clusterId, snap: null, failed: true });
      });
    return () => controller.abort();
  }, [clusterId]);
  const current = state.id === clusterId;
  return { snap: current ? state.snap : null, failed: current && state.failed };
}

export function usableSnapshot(snap: InfraSnapshot | null): snap is InfraSnapshot {
  return !!snap && (snap.state === "ok" || snap.state === "partial") && snap.nodes.length > 0;
}

/* ---------------- small marks ---------------- */

function meterTone(ratio: number | null): string {
  if (ratio === null) return "bg-surface-3";
  return ratio >= 0.9 ? "bg-critical" : ratio >= 0.75 ? "bg-warning" : "bg-brand-accent";
}

/** One resource: name, share, a bar, and the absolute numbers under it. */
export function UsageMeter({
  label,
  usage,
  detail,
  size = "md",
}: {
  label: string;
  usage: Usage;
  detail: string;
  size?: "md" | "lg";
}) {
  const fmt = useFormat();
  const pct =
    usage.ratio === null ? "—" : `${fmt.number(usage.ratio * 100, { maximumFractionDigits: usage.ratio < 0.1 ? 1 : 0 })}%`;
  return (
    <div className="min-w-0" data-testid={`usage-${label.toLowerCase()}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">{label}</span>
        <span
          data-tabular
          className={`font-[family-name:var(--font-display)] font-bold tracking-[-0.02em] text-ink ${size === "lg" ? "text-[1.75rem] leading-none" : "text-[1.125rem] leading-none"}`}
        >
          {pct}
        </span>
      </div>
      <span className={`mt-2 block overflow-hidden rounded-full bg-surface-3 ${size === "lg" ? "h-2" : "h-1.5"}`}>
        <span
          className={`block h-full rounded-full ${meterTone(usage.ratio)} transition-[width] duration-700`}
          style={{ width: `${Math.min(100, (usage.ratio ?? 0) * 100)}%` }}
        />
      </span>
      <span data-tabular className="mt-1.5 block truncate text-[11px] text-ink-muted">
        {detail}
      </span>
    </div>
  );
}

export function useUsageDetails() {
  const t = useT("clusters");
  const fmt = useFormat();
  const num = (v: number | null, d = 1) => (v === null ? "—" : fmt.number(v, { maximumFractionDigits: d }));
  return {
    cpu: (u: Usage) => t("card.coresOf", { used: num(u.used), total: num(u.capacity, 0) }),
    bytes: (u: Usage) =>
      t("card.gibOf", { used: num(u.used === null ? null : u.used / GiB), total: num(u.capacity === null ? null : u.capacity / GiB, 0) }),
    pods: (u: Usage) => t("card.podsOf", { used: num(u.used, 0), total: num(u.capacity, 0) }),
  };
}

/** The machines as a strip: each with its health and how full it is. */
export function NodeStrip({ nodes }: { nodes: NodeSnapshot[] }) {
  const fmt = useFormat();
  return (
    <ul className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(10.5rem,1fr))]" data-testid="node-strip">
      {nodes.map((node) => {
        const worst = Math.max(node.cpu.now ?? 0, node.memory.now ?? 0, node.disk.now ?? 0);
        return (
          <li key={node.name} className="min-w-0 rounded-xl border border-border bg-surface-2/60 px-3 py-2.5">
            <p className="flex items-center gap-2">
              <span
                aria-hidden
                className={`h-2 w-2 shrink-0 rounded-full ${
                  node.health !== "healthy" ? "bg-warning" : worst >= 0.9 ? "bg-critical" : worst >= 0.75 ? "bg-warning" : "bg-healthy"
                }`}
              />
              <span className="truncate font-mono text-[12px] font-semibold text-ink">{node.name}</span>
            </p>
            <div className="mt-2 space-y-1">
              {(
                [
                  ["CPU", node.cpu.now],
                  ["MEM", node.memory.now],
                  ["DISK", node.disk.now],
                ] as const
              ).map(([k, v]) => (
                <span key={k} className="grid grid-cols-[2.4rem_1fr_2.4rem] items-center gap-1.5">
                  <span className="font-mono text-[9.5px] tracking-[0.1em] text-ink-muted">{k}</span>
                  <span className="block h-1 overflow-hidden rounded-full bg-surface-3">
                    <span className={`block h-full rounded-full ${meterTone(v)}`} style={{ width: `${Math.min(100, (v ?? 0) * 100)}%` }} />
                  </span>
                  <span data-tabular className="text-right font-mono text-[10.5px] text-ink-secondary">
                    {v === null ? "—" : `${fmt.number(v * 100, { maximumFractionDigits: 0 })}%`}
                  </span>
                </span>
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** The apps on the cluster: logo, name and each environment's dashboard. */
export function AppChips({ picture }: { picture: ClusterPicture }) {
  const t = useT("clusters");
  if (picture.apps.length === 0) return <p className="text-micro text-ink-muted">{t("card.noApps")}</p>;
  return (
    <ul className="flex flex-wrap gap-2" data-testid="cluster-apps">
      {picture.apps.map((app) => (
        <li
          key={app.projectId}
          className="relative z-10 flex items-center gap-2 rounded-full border border-border bg-surface py-1 pr-1.5 pl-1"
        >
          <ProjectLogo projectId={app.projectId} name={app.name} size="sm" variant="mark" />
          <span className="text-caption font-semibold text-ink">{app.name}</span>
          {[...app.envs]
            .sort((a, b) => envRank(a.key) - envRank(b.key))
            .map((env) => (
              <Link
                key={env.environmentId}
                href={`/dashboards/${env.environmentId}`}
                className="rounded-full bg-surface-2 px-2 py-0.5 font-mono text-[10.5px] text-ink-secondary transition-colors hover:bg-brand-soft hover:text-brand-accent"
              >
                {env.key}
              </Link>
            ))}
        </li>
      ))}
    </ul>
  );
}

/**
 * What the agent's last sweep counted: healthy over total, per class,
 * straight from the summary — never a percentage computed here, and
 * nothing at all for an agent that is not connected.
 */
export function SweepRings({ clusterId }: { clusterId: string }) {
  const t = useT("clusters");
  const summary = useResource<InventorySummary>(`/v1/clusters/${clusterId}/inventory/summary`);
  if (summary.loading && !summary.data) return <span className="text-micro text-ink-muted">…</span>;
  if (!summary.data) return <span className="text-micro text-ink-muted">—</span>;
  if (summary.data.agent.status !== "connected") {
    return (
      <span className="text-micro text-ink-muted" data-testid="fleet-counts-unavailable">
        {t("card.noSweep")}
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-x-5 gap-y-3" data-testid="fleet-counts">
      {(
        [
          ["nodes", summary.data.nodes],
          ["workloads", summary.data.workloads],
          ["pods", summary.data.pods],
        ] as const
      ).map(([key, rollup]) => {
        const label = t(`card.counts.${key}`);
        return (
          <span key={key} className="flex items-center gap-2">
            <RingProgress
              size={42}
              label={t("card.healthyRing", { what: label })}
              value={rollup.total > 0 ? (rollup.healthy / rollup.total) * 100 : null}
              tone={rollup.unhealthy > 0 ? "critical" : rollup.degraded > 0 || rollup.unknown > 0 ? "warning" : "success"}
            />
            <span className="text-micro whitespace-nowrap text-ink-secondary">
              <span data-tabular className="block text-[0.9375rem] font-semibold text-ink">
                {rollup.healthy}
                <span className="text-ink-muted">/{rollup.total}</span>
              </span>
              {label}
            </span>
          </span>
        );
      })}
    </span>
  );
}

export function StateChip({ label, value, word }: { label: string; value: string | undefined; word: string }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-surface-2 py-1 pr-1 pl-3 text-micro">
      <span className="text-ink-muted">{label}</span>
      <StatusBadge status={toneForHealth(value)} label={word} size="compact" />
    </span>
  );
}

/* ---------------- the card ---------------- */

export function ClusterCard({ cluster, className = "" }: { cluster: Cluster; className?: string }) {
  const t = useT("clusters");
  const details = useUsageDetails();
  const { snap, failed } = useClusterSnapshot(cluster.id);
  const picture = usableSnapshot(snap) ? clusterPicture(snap) : null;
  const name = cluster.display_name || cluster.cluster_ref;
  const agent = cluster.operational?.agent ?? "unknown";
  const inventory = cluster.operational?.inventory ?? "unknown";

  return (
    <li
      data-testid={`cluster-card-${cluster.cluster_ref}`}
      className={`@container group relative flex min-w-0 flex-col gap-5 rounded-2xl border border-border bg-surface p-6 transition-[border-color,box-shadow] duration-200 hover:border-border-strong hover:shadow-[0_18px_40px_-26px_rgb(0_0_0/0.5)] ${className}`}
    >
      <div className="flex flex-wrap items-start gap-4">
        <ClusterLogo cluster={cluster} />
        <div className="min-w-0 flex-1">
          {/* The name is the card's one link; its ::after covers the card. */}
          <Link
            href={`/clusters/${cluster.id}`}
            aria-label={t("card.open", { cluster: name })}
            className="block truncate font-[family-name:var(--font-display)] text-[1.25rem] font-bold tracking-[-0.02em] text-ink outline-none after:absolute after:inset-0 after:rounded-2xl after:content-[''] focus-visible:after:ring-2 focus-visible:after:ring-focus"
          >
            {name}
          </Link>
          <p className="mt-0.5 truncate text-micro text-ink-muted">
            <span className="font-mono">
              {cluster.cluster_ref}
              {cluster.site ? ` · ${cluster.site}` : ""}
            </span>
            {" · "}
            <span data-tabular>{t("list.row.environments", { count: cluster.referenced_environments?.length ?? 0 })}</span>
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <StateChip label={t("card.agent")} value={agent} word={t.dyn("enum.agent", agent, humanize(agent))} />
            <StateChip
              label={t("card.inventory")}
              value={inventory}
              word={t.dyn("enum.inventory", inventory, humanize(inventory))}
            />
          </div>
        </div>
        <ArrowUpRight aria-hidden className="h-4 w-4 shrink-0 text-ink-muted transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
      </div>

      <div className="grid gap-5 border-t border-border pt-5 @[54rem]:grid-cols-[minmax(0,1.4fr)_auto]">
        {picture ? (
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 @[34rem]:grid-cols-4">
            <UsageMeter label={t("card.cpu")} usage={picture.cpu} detail={details.cpu(picture.cpu)} />
            <UsageMeter label={t("card.memory")} usage={picture.memory} detail={details.bytes(picture.memory)} />
            <UsageMeter label={t("card.disk")} usage={picture.disk} detail={details.bytes(picture.disk)} />
            <UsageMeter label={t("card.pods")} usage={picture.pods} detail={details.pods(picture.pods)} />
          </div>
        ) : (
          <p className="self-center text-micro text-ink-muted">{failed || snap ? t("card.noMetrics") : t("card.reading")}</p>
        )}
        <div className="flex flex-col justify-center gap-1.5 @[54rem]:items-end">
          <SweepRings clusterId={cluster.id} />
          <span className="text-micro text-ink-muted">
            {t("card.sweep")} · <RelativeTime value={cluster.as_of} />
          </span>
        </div>
      </div>

      {picture ? (
        <>
          <div>
            <p className="mb-2 flex items-baseline justify-between gap-2 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
              <span>{t("card.nodes")}</span>
              <span className="normal-case tracking-normal">
                {t("card.nodesHealthy", { healthy: picture.nodes.healthy, total: picture.nodes.total })}
              </span>
            </p>
            <NodeStrip nodes={snap!.nodes} />
          </div>
          <div className="mt-auto">
            <p className="mb-2 flex items-baseline justify-between gap-2 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
              <span>{t("card.apps")}</span>
              <span className="normal-case tracking-normal">
                {t("card.projects", { count: picture.apps.length })} · {t("card.namespaces", { count: picture.namespaces })}
              </span>
            </p>
            <AppChips picture={picture} />
          </div>
        </>
      ) : null}
    </li>
  );
}
