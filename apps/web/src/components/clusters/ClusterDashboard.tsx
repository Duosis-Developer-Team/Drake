"use client";

/**
 * The cluster dashboard's sections, in the environment dashboards' visual
 * language: capacity in use, the apps running on the cluster (each opening
 * its dashboard), namespaces grouped by owner and opened in place, and the
 * agent's sweep by kind.
 */

import {
  Activity,
  Boxes,
  CalendarClock,
  ChevronDown,
  Container,
  Cpu,
  Database,
  FolderTree,
  Globe,
  Layers,
  Network,
  Server,
  ShieldCheck,
  Timer,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { kindColour } from "@/components/apps/AppLauncher";
import { TracesisMark } from "@/components/shell/Brand";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import type { ClusterPicture, NamespaceGroupUse } from "@/lib/clusterPicture";
import { useFormat, useT } from "@/lib/i18n";
import { GiB } from "@/lib/infrastructure";
import type { HealthRollup } from "@/lib/inventory";
import { envRank } from "@/lib/platform";

import { SweepRings, UsageMeter, useUsageDetails } from "./ClusterCard";

/** A dashboard card: title, one line under it, then the body. */
export function DashCard({
  title,
  description,
  actions,
  children,
  className = "",
  testId,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      aria-label={title}
      className={`flex min-w-0 animate-[rise-in_.45s_cubic-bezier(.2,.8,.2,1)_both] flex-col rounded-panel border border-border bg-surface ${className}`}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 px-6 pt-5 pb-4">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{title}</h2>
          {description ? <p className="mt-0.5 text-[12.5px] text-ink-muted">{description}</p> : null}
        </div>
        {actions}
      </header>
      <div className="flex-1 px-6 pb-6">{children}</div>
    </section>
  );
}

/* ---------------- capacity ---------------- */

export function CapacityCard({ picture, clusterId }: { picture: ClusterPicture; clusterId: string }) {
  const t = useT("clusters");
  const details = useUsageDetails();
  return (
    <DashCard title={t("card.usageTitle")} description={t("dash.capacity.description")} testId="cluster-capacity">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
        <div className="grid grid-cols-2 gap-x-8 gap-y-6 md:grid-cols-4">
          <UsageMeter size="lg" label={t("card.cpu")} usage={picture.cpu} detail={details.cpu(picture.cpu)} />
          <UsageMeter size="lg" label={t("card.memory")} usage={picture.memory} detail={details.bytes(picture.memory)} />
          <UsageMeter size="lg" label={t("card.disk")} usage={picture.disk} detail={details.bytes(picture.disk)} />
          <UsageMeter size="lg" label={t("card.pods")} usage={picture.pods} detail={details.pods(picture.pods)} />
        </div>
        <div className="rounded-xl bg-surface-2 px-5 py-4 lg:border-l lg:border-border">
          <SweepRings clusterId={clusterId} />
        </div>
      </div>
    </DashCard>
  );
}

/* ---------------- apps ---------------- */

export function ClusterApps({ picture }: { picture: ClusterPicture }) {
  const t = useT("clusters");
  const fmt = useFormat();
  const num = (v: number | null, d = 2) => (v === null ? "—" : fmt.number(v, { maximumFractionDigits: d }));
  const totalCpu = picture.cpu.used ?? picture.apps.reduce((a, x) => a + x.cpu, 0);
  const n = Math.max(1, Math.min(picture.apps.length, 3));
  return (
    <DashCard title={t("dash.apps.title")} description={t("dash.apps.description")} testId="cluster-apps-card">
      {picture.apps.length === 0 ? (
        <p className="text-caption text-ink-muted">{t("dash.apps.empty")}</p>
      ) : (
        <ul
          className="grid gap-4 md:[grid-template-columns:repeat(var(--n),minmax(0,1fr))]"
          style={{ "--n": n } as React.CSSProperties}
        >
          {picture.apps.map((app) => (
            <li key={app.projectId} className="flex min-w-0 flex-col overflow-hidden rounded-2xl border border-border bg-surface-2/50">
              <div className="flex items-center gap-3.5 px-5 pt-5 pb-4">
                <span
                  className="grid h-14 w-14 shrink-0 place-items-center rounded-[1rem] border border-border bg-gradient-to-b from-surface to-surface-2"
                  style={{ boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.07), 0 10px 20px -14px rgb(0 0 0 / 0.5)" }}
                >
                  <ProjectLogo projectId={app.projectId} name={app.name} size="lg" variant="mark" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-[family-name:var(--font-display)] text-[1.125rem] font-bold tracking-[-0.02em] text-ink">
                    {app.name}
                  </span>
                  <span className="block text-micro text-ink-muted">
                    {t("dash.apps.cpu", { value: num(app.cpu) })} · {t("dash.apps.memory", { value: num(app.memory / GiB, 1) })}
                  </span>
                </span>
              </div>
              <ul className="mt-auto border-t border-border">
                {[...app.envs]
                  .sort((a, b) => envRank(a.key) - envRank(b.key))
                  .map((env) => {
                    const share = totalCpu ? (env.cpu ?? 0) / totalCpu : 0;
                    return (
                      <li key={env.environmentId} className="border-b border-border last:border-b-0">
                        <Link
                          href={`/dashboards/${env.environmentId}`}
                          className="group grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-3 px-5 py-2.5 transition-colors hover:bg-surface-hover"
                        >
                          <span className="flex items-center gap-2">
                            <span aria-hidden className="h-3 w-[3px] rounded-full" style={{ background: kindColour(env.key) }} />
                            <span className="truncate text-caption font-semibold text-ink">{env.key}</span>
                          </span>
                          <span className="min-w-0">
                            <span className="block h-1.5 overflow-hidden rounded-full bg-surface-3">
                              <span className="block h-full rounded-full bg-brand-accent" style={{ width: `${Math.min(100, share * 100 * 2.5)}%` }} />
                            </span>
                            <span className="mt-1 block truncate font-mono text-[10.5px] text-ink-muted">
                              {env.namespace} · {num(env.cpu)} CPU · {num(env.memory === null ? null : env.memory / GiB, 1)} GiB
                            </span>
                          </span>
                          <span className="inline-flex items-center gap-1 text-micro font-semibold text-brand-accent">
                            {t("dash.apps.open")}
                            <span aria-hidden className="transition-transform group-hover:translate-x-0.5">→</span>
                          </span>
                        </Link>
                      </li>
                    );
                  })}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </DashCard>
  );
}

/* ---------------- namespaces by owner ---------------- */

const GROUP_ICON: Record<NamespaceGroupUse["kind"], LucideIcon> = {
  project: Layers,
  kubernetes: Boxes,
  monitoring: Activity,
  drake: Activity,
  unclaimed: FolderTree,
};

/** An owner's mark: a project's logo, Tracesis's own, or an icon. */
export function GroupMark({ group, size = "md" }: { group: { kind: NamespaceGroupUse["kind"]; name: string; projectId: string | null }; size?: "sm" | "md" }) {
  const box = size === "sm" ? "h-7 w-7 rounded-[8px]" : "h-10 w-10 rounded-[11px]";
  if (group.projectId) {
    return <ProjectLogo projectId={group.projectId} name={group.name} size={size === "sm" ? "md" : "lg"} variant="mark" />;
  }
  if (group.kind === "drake") {
    return (
      <span className={`grid shrink-0 place-items-center border border-border bg-surface-2 ${box}`}>
        <TracesisMark height={size === "sm" ? 9 : 12} />
      </span>
    );
  }
  const Icon = GROUP_ICON[group.kind] ?? FolderTree;
  return (
    <span className={`grid shrink-0 place-items-center border border-border bg-surface-2 text-ink-secondary ${box}`}>
      <Icon aria-hidden className={size === "sm" ? "h-3.5 w-3.5" : "h-4.5 w-4.5"} />
    </span>
  );
}

export function NamespaceGroups({ picture, groupName }: { picture: ClusterPicture; groupName: (g: NamespaceGroupUse) => string }) {
  const t = useT("clusters");
  const fmt = useFormat();
  const [open, setOpen] = useState<string | null>(null);
  const num = (v: number | null, d = 2) => (v === null ? "—" : fmt.number(v, { maximumFractionDigits: d }));
  const totalCpu = picture.groups.reduce((a, g) => a + g.cpu, 0);
  const pct = (v: number) => `${fmt.number(v * 100, { maximumFractionDigits: v < 0.1 ? 1 : 0 })}%`;
  return (
    <DashCard
      title={t("dash.namespaces.title")}
      description={t("dash.namespaces.description")}
      testId="cluster-namespaces"
      actions={
        <Link href="/infrastructure" className="text-caption font-medium text-brand-accent hover:underline">
          {t("dash.namespaces.open")} →
        </Link>
      }
    >
      <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {picture.groups.map((g) => {
          const isOpen = open === g.key;
          const share = totalCpu ? g.cpu / totalCpu : 0;
          return (
            <li key={g.key} className={`min-w-0 rounded-2xl border bg-surface-2/40 transition-colors ${isOpen ? "border-brand-accent/60 md:col-span-2 xl:col-span-3" : "border-border"}`}>
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() => setOpen(isOpen ? null : g.key)}
                className="flex w-full items-center gap-3.5 px-4 py-3.5 text-left"
                data-testid={`ns-group-${g.key}`}
              >
                <GroupMark group={g} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[0.9375rem] font-semibold text-ink">{groupName(g)}</span>
                  <span className="block text-micro text-ink-muted">
                    {t("card.namespaces", { count: g.namespaces.length })} · {t("dash.namespaces.pods", { count: Math.round(g.pods) })}
                  </span>
                  <span className="mt-2 flex items-center gap-2">
                    <span className="block h-1 flex-1 overflow-hidden rounded-full bg-surface-3">
                      <span className="block h-full rounded-full bg-brand-accent" style={{ width: `${Math.min(100, share * 100)}%` }} />
                    </span>
                    <span data-tabular className="shrink-0 font-mono text-[10.5px] text-ink-secondary">
                      {t("dash.namespaces.cpuShare", { share: pct(share) })}
                    </span>
                  </span>
                </span>
                <ChevronDown aria-hidden className={`h-4 w-4 shrink-0 text-ink-muted transition-transform ${isOpen ? "rotate-180" : ""}`} />
              </button>
              {isOpen ? (
                <ul className="grid animate-[rise-in_.3s_ease-out_both] gap-2 border-t border-border p-3 sm:grid-cols-2 xl:grid-cols-3">
                  {[...g.namespaces]
                    .sort((a, b) => (b.cpu ?? 0) - (a.cpu ?? 0))
                    .map((ns) => {
                      const memShare = ns.memory !== null && ns.memoryLimit ? ns.memory / ns.memoryLimit : null;
                      return (
                        <li key={ns.namespace} className="min-w-0 rounded-xl border border-border bg-surface px-3.5 py-3">
                          <p className="flex items-center justify-between gap-2">
                            <span className="truncate font-mono text-[12px] font-semibold text-ink">{ns.namespace}</span>
                            {(ns.restarts ?? 0) > 0 ? (
                              <span className="shrink-0 rounded-full bg-warning-soft px-2 py-0.5 text-[10.5px] font-medium text-warning">
                                {t("dash.namespaces.restarts", { count: Math.round(ns.restarts ?? 0) })}
                              </span>
                            ) : null}
                          </p>
                          <p className="mt-0.5 truncate text-[11px] text-ink-muted">
                            {ns.nodes.join(", ")} · {t("dash.namespaces.pods", { count: Math.round(ns.pods ?? 0) })}
                          </p>
                          <dl className="mt-2.5 grid grid-cols-2 gap-3">
                            <div>
                              <dt className="font-mono text-[9.5px] tracking-[0.1em] text-ink-muted">CPU</dt>
                              <dd data-tabular className="font-mono text-[12.5px] font-semibold text-ink">
                                {num(ns.cpu)}
                                <span className="font-normal text-ink-muted"> / {num(ns.cpuRequested)}</span>
                              </dd>
                            </div>
                            <div>
                              <dt className="font-mono text-[9.5px] tracking-[0.1em] text-ink-muted">MEM</dt>
                              <dd data-tabular className="font-mono text-[12.5px] font-semibold text-ink">
                                {num(ns.memory === null ? null : ns.memory / GiB, 1)}
                                <span className="font-normal text-ink-muted"> GiB</span>
                              </dd>
                              {memShare !== null ? (
                                <span className="mt-1 block h-1 overflow-hidden rounded-full bg-surface-3">
                                  <span
                                    className={`block h-full rounded-full ${memShare >= 0.9 ? "bg-critical" : memShare >= 0.8 ? "bg-warning" : "bg-healthy"}`}
                                    style={{ width: `${Math.min(100, memShare * 100)}%` }}
                                  />
                                </span>
                              ) : null}
                            </div>
                          </dl>
                        </li>
                      );
                    })}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </DashCard>
  );
}

/* ---------------- resources by kind ---------------- */

const KIND_ICON: Record<string, LucideIcon> = {
  Pod: Container,
  Deployment: Workflow,
  ReplicaSet: Layers,
  StatefulSet: Database,
  DaemonSet: Cpu,
  Job: Timer,
  CronJob: CalendarClock,
  Service: Network,
  Ingress: Globe,
  Node: Server,
  Namespace: FolderTree,
  PersistentVolumeClaim: Database,
  PersistentVolume: Database,
  NetworkPolicy: ShieldCheck,
};
const KIND_COLOURS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)", "var(--status-info)", "var(--gold)"];

function HealthStrip({ rollup }: { rollup: HealthRollup }) {
  const parts = [
    ["bg-healthy", rollup.healthy],
    ["bg-warning", rollup.degraded],
    ["bg-critical", rollup.unhealthy],
    ["bg-surface-3", rollup.unknown],
  ] as const;
  return (
    <span className="flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full bg-surface-3">
      {parts.filter(([, v]) => v > 0).map(([c, v]) => (
        <span key={c} className={`h-full ${c}`} style={{ flexGrow: v }} />
      ))}
    </span>
  );
}

export function KindsViz({ byKind, clusterId }: { byKind: Record<string, HealthRollup>; clusterId: string }) {
  const t = useT("clusters");
  const fmt = useFormat();
  const kinds = Object.entries(byKind).sort(([, a], [, b]) => b.total - a.total);
  const total = kinds.reduce((a, [, r]) => a + r.total, 0);
  const shown = kinds.slice(0, 12);
  const strip = kinds.slice(0, KIND_COLOURS.length);
  const restTotal = kinds.slice(KIND_COLOURS.length).reduce((a, [, r]) => a + r.total, 0);
  return (
    <DashCard
      title={t("dash.kinds.title")}
      description={t("dash.kinds.description")}
      testId="cluster-kinds"
      actions={
        <span data-tabular className="rounded-full bg-surface-2 px-3 py-1 text-micro font-medium text-ink-secondary">
          {t("dash.kinds.total", { count: total })}
        </span>
      }
    >
      {kinds.length === 0 ? (
        <p className="text-caption text-ink-muted">{t("detail.kinds.emptyDescription")}</p>
      ) : (
        <>
          {/* The whole sweep as one proportional bar, largest kind first. */}
          <div className="flex h-9 w-full overflow-hidden rounded-xl">
            {strip.map(([kind, r], i) => (
              <Link
                key={kind}
                href={`/clusters/${clusterId}/inventory?kind=${kind}`}
                title={`${kind} · ${r.total}`}
                aria-label={t("detail.kinds.browse", { kind })}
                className="group relative flex min-w-0 items-center justify-center border-r border-surface/60 transition-[filter] last:border-r-0 hover:brightness-110"
                style={{ flexGrow: r.total, background: KIND_COLOURS[i] }}
              >
                {r.total / total > 0.07 ? (
                  <span className="truncate px-2 font-mono text-[10.5px] font-semibold text-white/95">{kind}</span>
                ) : null}
              </Link>
            ))}
            {restTotal > 0 ? <span className="bg-surface-3" style={{ flexGrow: restTotal }} /> : null}
          </div>

          <ul className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
            {shown.map(([kind, r], i) => {
              const Icon = KIND_ICON[kind] ?? Boxes;
              return (
                <li key={kind}>
                  <Link
                    href={`/clusters/${clusterId}/inventory?kind=${kind}`}
                    aria-label={t("detail.kinds.browse", { kind })}
                    className="group flex h-full flex-col gap-3 rounded-xl border border-border bg-surface-2/40 px-4 py-3.5 transition-colors hover:border-border-strong hover:bg-surface-hover"
                  >
                    <span className="flex items-center gap-2.5">
                      <span
                        className="grid h-8 w-8 shrink-0 place-items-center rounded-[9px]"
                        style={{ background: `color-mix(in srgb, ${KIND_COLOURS[i] ?? "var(--text-muted)"} 16%, transparent)`, color: KIND_COLOURS[i] ?? "var(--text-muted)" }}
                      >
                        <Icon aria-hidden className="h-4 w-4" />
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[12px] font-semibold text-ink">{kind}</span>
                      <span data-tabular className="font-[family-name:var(--font-display)] text-[1.25rem] leading-none font-bold tracking-[-0.02em] text-ink">
                        {fmt.number(r.total)}
                      </span>
                    </span>
                    <HealthStrip rollup={r} />
                    {r.unhealthy > 0 ? (
                      <span className="text-[11px] font-medium text-critical">{t("dash.kinds.unhealthy", { count: r.unhealthy })}</span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
          {kinds.length > shown.length ? (
            <Link href={`/clusters/${clusterId}/inventory`} className="mt-3 inline-block text-caption font-medium text-brand-accent hover:underline">
              {t("dash.kinds.other", { count: kinds.length - shown.length })} →
            </Link>
          ) : null}
        </>
      )}
    </DashCard>
  );
}
