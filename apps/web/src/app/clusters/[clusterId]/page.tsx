"use client";

/**
 * Cluster detail.
 *
 * An operator lands here to answer one question first: is this cluster's
 * agent talking, and is what it last reported still current? Those two
 * claims lead the page as tiles, then the agent and freshness cards repeat
 * them with precision, then what the sweep found, then capacity, and the
 * catalog record last.
 */

import {
  Boxes,
  ChevronRight,
  Container,
  Database,
  FolderTree,
  Layers,
  PackageOpen,
  Plug,
  RefreshCw,
  Server,
  ShieldCheck,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";

import { useApi, provenanceProps } from "@/components/catalog/primitives";
import { Countdown, Donut, ToneCounters } from "@/components/charts/visuals";
import {
  DefinitionGrid,
  IconBubble,
  PillLink,
  ScreenGate,
  SegmentBar,
  StatTile,
  StateCard,
} from "@/components/clusters/primitives";
import {
  AgentBadge,
  HealthBadge,
  InventoryStateBadge,
} from "@/components/inventory/primitives";
import { Provenance } from "@/components/provenance/Provenance";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import { LoadGate } from "@/components/catalog/primitives";
import { Panel, PanelHeader, SectionHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { RelativeTime } from "@/components/ui/identifiers";
import type { Cluster } from "@/lib/catalog";
import { humanize, toneForHealth } from "@/lib/design/status";
import { useFormat, useT, type Translator } from "@/lib/i18n";
import type { HealthRollup, InventorySummary } from "@/lib/inventory";
import { parseRangePreset } from "@/lib/telemetry";

/** One rollup, as donut slices. The unknown bucket is always present. */
function healthSlices(t: Translator<"clusters">, rollup: HealthRollup) {
  return [
    { name: t("enum.health.healthy"), value: rollup.healthy, tone: "success" as const },
    { name: t("enum.health.degraded"), value: rollup.degraded, tone: "warning" as const },
    { name: t("enum.health.unhealthy"), value: rollup.unhealthy, tone: "critical" as const },
    { name: t("enum.health.unknown"), value: rollup.unknown, tone: "unknown" as const },
  ];
}

/** A status as the tile's headline — a word, sized to sit where a number would. */
function StatWord({
  children,
  muted = false,
}: {
  children: React.ReactNode;
  muted?: boolean;
}) {
  return (
    <span
      className={`block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] ${
        muted ? "text-ink-muted" : "text-ink"
      }`}
    >
      {children}
    </span>
  );
}

/** A mono UTC instant, or an honest dash. */
function Instant({ value }: { value: string | null | undefined }) {
  const t = useT("clusters");
  const fmt = useFormat();
  return value ? (
    <span className="font-mono text-caption text-ink">{fmt.utc(value)}</span>
  ) : (
    <span className="text-ink-muted">{t("detail.agent.notReported")}</span>
  );
}

/**
 * One resource class. Composition when there is something to compose; a
 * designed "none recorded" when every bucket is zero, rather than an empty
 * donut sentence floating in a card.
 */
function RollupCard({
  title,
  icon,
  rollup,
  className = "",
  "data-testid": testId,
  children,
}: {
  title: string;
  icon: LucideIcon;
  rollup: HealthRollup;
  className?: string;
  "data-testid"?: string;
  children?: React.ReactNode;
}) {
  const t = useT("clusters");
  return (
    <Panel data-testid={testId} className={`h-full ${className}`}>
      <div className="flex items-center gap-3">
        <IconBubble icon={icon} />
        <h3 className="min-w-0 flex-1 text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
          {title}
        </h3>
        <span data-tabular className="text-caption text-ink-muted">
          <span className="font-semibold text-ink">{rollup.total}</span> {t("detail.sweep.total")}
        </span>
      </div>
      <div
        className={
          children ? "flex flex-wrap items-center gap-x-10 gap-y-6" : "" // i18n-ignore
        }
      >
        <div className="min-w-0 flex-1">
          {rollup.total > 0 ? (
            <Donut
              size={120}
              thickness={13}
              label={t("detail.sweep.byHealth", { title })}
              centerLabel={`${rollup.total}`}
              slices={healthSlices(t, rollup)}
            />
          ) : (
            <div className="flex items-center gap-4 rounded-[1rem] bg-surface-2 px-5 py-4">
              <span
                aria-hidden
                className="h-10 w-10 shrink-0 rounded-full border-[5px] border-surface-3"
              />
              <div className="min-w-0">
                <p className="text-body font-medium text-ink">{t("detail.sweep.noneRecorded")}</p>
                <p className="text-caption text-ink-muted">
                  {t("detail.sweep.allBucketsZero")}
                </p>
              </div>
            </div>
          )}
        </div>
        {children}
      </div>
    </Panel>
  );
}

export default function ClusterDetailPage() {
  const t = useT("clusters");
  const tc = useT("common");
  const { clusterId } = useParams<{ clusterId: string }>();
  // The same range control the service boards use, read from the URL,
  // so a link to a cluster at 7d opens at 7d.
  const preset = parseRangePreset(useSearchParams().get("range"));
  const [cluster, retry] = useApi<Cluster>(`/v1/clusters/${clusterId}`);
  const [summary, retrySummary] = useApi<InventorySummary>(
    `/v1/clusters/${clusterId}/inventory/summary`,
  );
  const ready = summary.state === "ready" ? summary.data : null;
  const summaryFallback =
    summary.state === "error" ? tc("state.unavailable") : t("shared.loadingEllipsis");

  return (
    <PageFrame>
      <ScreenGate
        value={cluster}
        retry={retry}
        notFound={
          <StateCard
            testId="state-not-found"
            icon={Server}
            tone="not-applicable"
            title={t("shared.notFoundTitle")}
            description={t("shared.notFoundDescription")}
            action={
              <PillLink LinkComponent={Link} href="/clusters">
                {t("detail.backToClusters")}
              </PillLink>
            }
          />
        }
      >
        {(data) => {
          const environments = data.referenced_environments ?? [];
          return (
            <>
              <nav
                aria-label={t("breadcrumb.label")}
                className="mb-3 flex items-center gap-1.5 text-micro text-ink-muted"
              >
                <Link href="/clusters" className="rounded hover:text-ink">
                  {t("breadcrumb.clusters")}
                </Link>
                <ChevronRight aria-hidden className="h-3 w-3" />
                <span className="font-mono text-ink-secondary">
                  {data.cluster_ref}
                </span>
              </nav>

              <PageHeader
                title={data.display_name || data.cluster_ref}
                description={
                  data.site
                    ? t("detail.subtitleWithSite", { site: data.site, version: data.version })
                    : t("detail.subtitle", { version: data.version })
                }
                status={
                  <StatusBadge
                    status={data.lifecycle === "active" ? "success" : "unknown"}
                    label={t.dyn("enum.lifecycle", data.lifecycle, humanize(data.lifecycle))}
                  />
                }
                actions={
                  <PillLink
                    LinkComponent={Link}
                    href={`/clusters/${clusterId}/inventory`}
                    icon={Boxes}
                    variant="primary"
                  >
                    {t("detail.browseInventory")}
                  </PillLink>
                }
              />

              <div className="page-grid mb-6">
                <StatTile
                  icon={Plug}
                  tone={ready ? toneForHealth(ready.agent.status) : null}
                  label={t("detail.tile.agentConnection")}
                  value={
                    <StatWord muted={!ready}>
                      {ready
                        ? t.dyn("enum.agent", ready.agent.status, humanize(ready.agent.status))
                        : summaryFallback}
                    </StatWord>
                  }
                >
                  <p className="text-micro text-ink-muted">
                    {t("detail.tile.heartbeat")}{" "}
                    <span className="text-ink-secondary">
                      {ready?.agent.last_heartbeat_at ? (
                        <RelativeTime value={ready.agent.last_heartbeat_at} />
                      ) : (
                        t("detail.tile.notReported")
                      )}
                    </span>
                  </p>
                </StatTile>
                <StatTile
                  icon={RefreshCw}
                  tone={ready ? toneForHealth(ready.inventory.state) : null}
                  label={t("detail.tile.inventory")}
                  value={
                    <StatWord muted={!ready}>
                      {ready
                        ? t.dyn(
                            "enum.inventory",
                            ready.inventory.state,
                            humanize(ready.inventory.state),
                          )
                        : summaryFallback}
                    </StatWord>
                  }
                >
                  <p className="text-micro text-ink-muted">
                    {t("detail.tile.reconciled")}{" "}
                    <span className="text-ink-secondary">
                      {ready?.inventory.last_reconcile_at ? (
                        <RelativeTime
                          value={ready.inventory.last_reconcile_at}
                        />
                      ) : (
                        t("detail.tile.notReported")
                      )}
                    </span>
                  </p>
                </StatTile>
                <StatTile
                  icon={Boxes}
                  label={t("detail.tile.resources")}
                  value={ready ? (ready.inventory.active_resources ?? 0) : "—"}
                  suffix={t("detail.tile.resourcesSuffix")}
                >
                  {ready ? (
                    <SegmentBar
                      label={t("shared.resourcesByLifecycle")}
                      segments={[
                        {
                          key: "active",
                          label: t("enum.resourceLifecycle.active"),
                          value: ready.inventory.active_resources ?? 0,
                          tone: "info",
                        },
                        {
                          key: "missing",
                          label: t("enum.resourceLifecycle.missing"),
                          value: ready.inventory.missing_resources ?? 0,
                          tone: "stale",
                        },
                      ]}
                    />
                  ) : null}
                </StatTile>
                <StatTile
                  icon={Layers}
                  label={t("detail.tile.environments")}
                  value={environments.length}
                  suffix={t("detail.tile.environmentsSuffix")}
                >
                  {environments.length > 0 ? (
                    <ul className="flex flex-wrap gap-1.5">
                      {environments.slice(0, 3).map((environment) => (
                        <li
                          key={`${environment.project_key}/${environment.environment_key}`}
                          className="rounded-full bg-surface-2 px-2.5 py-1 font-mono text-micro text-ink-secondary"
                        >
                          {environment.project_key}/
                          {environment.environment_key}
                        </li>
                      ))}
                      {environments.length > 3 ? (
                        <li className="rounded-full bg-surface-2 px-2.5 py-1 text-micro text-ink-muted">
                          +{environments.length - 3}
                        </li>
                      ) : null}
                    </ul>
                  ) : (
                    <p className="text-micro text-ink-muted">
                      {t("detail.tile.noEnvironments")}
                    </p>
                  )}
                </StatTile>
              </div>

              <section
                aria-label={t("detail.section")}
                className="space-y-6"
              >
                <LoadGate value={summary} retry={retrySummary}>
                  {(inventory) => {
                    const kinds = Object.entries(inventory.by_kind).sort(
                      ([, a], [, b]) => b.total - a.total,
                    );
                    const largest = Math.max(
                      ...kinds.map(([, rollup]) => rollup.total),
                      1,
                    );
                    const classes = [
                      ["nodes", t("detail.sweep.nodes"), Server, inventory.nodes],
                      ["namespaces", t("detail.sweep.namespaces"), FolderTree, inventory.namespaces],
                      ["workloads", t("detail.sweep.workloads"), Workflow, inventory.workloads],
                      ["pods", t("detail.sweep.pods"), Container, inventory.pods],
                      [
                        "volumeClaims",
                        t("detail.sweep.volumeClaims"),
                        Database,
                        inventory.persistent_volume_claims,
                      ],
                    ] as const;
                    return (
                      <div className="space-y-6">
                        <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2">
                          <Panel data-testid="agent-card" className="h-full">
                            <PanelHeader
                              title={t("detail.agent.title")}
                              description={t("detail.agent.description")}
                              actions={
                                <AgentBadge status={inventory.agent.status} />
                              }
                            />
                            <DefinitionGrid
                              items={[
                                {
                                  label: t("detail.agent.version"),
                                  value: inventory.agent.agent_version ? (
                                    <span className="font-mono text-caption">
                                      {inventory.agent.agent_version}
                                    </span>
                                  ) : (
                                    <span className="text-ink-muted">
                                      {t("detail.agent.notReported")}
                                    </span>
                                  ),
                                },
                                {
                                  label: t("detail.agent.lastHeartbeat"),
                                  value: (
                                    <Instant
                                      value={inventory.agent.last_heartbeat_at}
                                    />
                                  ),
                                },
                                {
                                  label: t("detail.agent.certificateExpires"),
                                  value: (
                                    <span className="flex flex-wrap items-center gap-2">
                                      <Instant
                                        value={
                                          inventory.agent.certificate_not_after
                                        }
                                      />
                                      {inventory.agent
                                        .certificate_expiry_warning ? (
                                        <StatusBadge
                                          status="warning"
                                          label={t("detail.agent.expiresSoon")}
                                        />
                                      ) : null}
                                    </span>
                                  ),
                                },
                              ]}
                            />
                            {/* A deadline, not a measurement — so it drains toward
                                the near end rather than filling toward a limit.
                                The server owns the warning; this only draws how
                                much runway is left. */}
                            <div className="mt-auto flex items-center gap-4 rounded-[1rem] bg-surface-2 px-5 py-4">
                              <IconBubble icon={ShieldCheck} size="small" />
                              <div className="min-w-0 flex-1">
                                <Countdown
                                  label={t("detail.agent.certificateRunway")}
                                  deadline={
                                    inventory.agent.certificate_not_after
                                  }
                                />
                              </div>
                            </div>
                          </Panel>

                          <Panel
                            data-testid="freshness-card"
                            className="h-full"
                          >
                            <PanelHeader
                              title={t("detail.freshness.title")}
                              description={t("detail.freshness.description")}
                              actions={
                                <InventoryStateBadge
                                  state={inventory.inventory.state}
                                />
                              }
                            />
                            <DefinitionGrid
                              items={[
                                {
                                  label: t("detail.freshness.lastReconcile"),
                                  value: (
                                    <Instant
                                      value={
                                        inventory.inventory.last_reconcile_at
                                      }
                                    />
                                  ),
                                },
                                {
                                  label: t("detail.freshness.lastEvent"),
                                  value: (
                                    <Instant
                                      value={inventory.inventory.last_event_at}
                                    />
                                  ),
                                },
                              ]}
                            />
                            <div className="mt-auto grid grid-cols-2 gap-3">
                              <div className="rounded-[1rem] bg-surface-2 px-5 py-4">
                                <span className="text-micro tracking-[0.08em] text-ink-muted uppercase">
                                  {t("detail.freshness.activeResources")}
                                </span>
                                <span
                                  data-tabular
                                  className="mt-2 block text-[1.75rem] leading-none font-semibold tracking-[-0.03em] text-ink"
                                >
                                  {inventory.inventory.active_resources ?? 0}
                                </span>
                              </div>
                              <div className="rounded-[1rem] bg-surface-2 px-5 py-4">
                                <span className="text-micro tracking-[0.08em] text-ink-muted uppercase">
                                  {t("detail.freshness.missingResources")}
                                </span>
                                <span
                                  data-tabular
                                  className={`mt-2 block text-[1.75rem] leading-none font-semibold tracking-[-0.03em] ${
                                    (inventory.inventory.missing_resources ??
                                      0) > 0
                                      ? "text-stale"
                                      : "text-ink"
                                  }`}
                                >
                                  {inventory.inventory.missing_resources ?? 0}
                                </span>
                              </div>
                            </div>
                          </Panel>
                        </div>

                        <SectionHeader
                          title={t("shared.sweepTitle")}
                          description={t("shared.sweepDescription")}
                        />
                        {classes.every(([, , , rollup]) => rollup.total === 0) ? (
                          // Nothing recorded in any class: one card of five
                          // zero tiles, not five cards repeating one sentence.
                          <Panel>
                            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                              {classes.map(([key, title, icon, rollup]) => (
                                <div
                                  key={key}
                                  data-testid={
                                    key === "pods" ? "pods-card" : undefined
                                  }
                                  className="flex min-w-0 flex-col gap-3 rounded-[1rem] bg-surface-2 px-5 py-4"
                                >
                                  <span className="flex items-center gap-2.5">
                                    <IconBubble icon={icon} size="small" />
                                    <span className="truncate text-caption font-medium text-ink">
                                      {title}
                                    </span>
                                  </span>
                                  <span className="flex items-baseline gap-2">
                                    <span
                                      data-tabular
                                      className="text-[1.75rem] leading-none font-semibold tracking-[-0.03em] text-ink"
                                    >
                                      {rollup.total}
                                    </span>
                                    <span className="text-micro text-ink-muted">
                                      {t("detail.sweep.recorded")}
                                    </span>
                                  </span>
                                </div>
                              ))}
                            </div>
                            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
                              <p className="text-caption text-ink-muted">
                                {t("detail.sweep.allZero")}
                              </p>
                              <ToneCounters
                                size="compact"
                                items={[
                                  {
                                    label: t("shared.restarts"),
                                    count: inventory.pods.restarts,
                                    tone: "warning",
                                  },
                                  {
                                    label: t("shared.crashLoop"),
                                    count: inventory.pods.crashloop,
                                    tone: "critical",
                                  },
                                  {
                                    label: t("shared.oomKilled"),
                                    count: inventory.pods.oom_killed,
                                    tone: "critical",
                                  },
                                ]}
                              />
                            </div>
                          </Panel>
                        ) : (
                          <div className="grid grid-cols-1 items-stretch gap-6 md:grid-cols-2 xl:grid-cols-3">
                            <RollupCard
                              title={t("detail.sweep.nodes")}
                              icon={Server}
                              rollup={inventory.nodes}
                            />
                            <RollupCard
                              title={t("detail.sweep.namespaces")}
                              icon={FolderTree}
                              rollup={inventory.namespaces}
                            />
                            <RollupCard
                              title={t("detail.sweep.workloads")}
                              icon={Workflow}
                              rollup={inventory.workloads}
                            />
                            <RollupCard
                              data-testid="pods-card"
                              title={t("detail.sweep.pods")}
                              icon={Container}
                              rollup={inventory.pods}
                              className="xl:col-span-2"
                            >
                              {/* Failure modes, not a composition: these do not
                                  add up to the pod total, so they are counters
                                  rather than wedges. */}
                              <div className="min-w-[12rem] rounded-[1rem] bg-surface-2 px-5 py-4">
                                <p className="mb-3 text-micro tracking-[0.08em] text-ink-muted uppercase">
                                  {t("shared.podInstability")}
                                </p>
                                <ToneCounters
                                  items={[
                                    {
                                      label: t("shared.restarts"),
                                      count: inventory.pods.restarts,
                                      tone: "warning",
                                    },
                                    {
                                      label: t("shared.crashLoop"),
                                      count: inventory.pods.crashloop,
                                      tone: "critical",
                                    },
                                    {
                                      label: t("shared.oomKilled"),
                                      count: inventory.pods.oom_killed,
                                      tone: "critical",
                                    },
                                  ]}
                                />
                              </div>
                            </RollupCard>
                            <RollupCard
                              title={t("detail.sweep.persistentVolumeClaims")}
                              icon={Database}
                              rollup={inventory.persistent_volume_claims}
                            />
                          </div>
                        )}

                        <Panel flush>
                          <PanelHeader
                            flush
                            title={t("shared.resourcesByKind")}
                            description={t("detail.kinds.description")}
                            meta={
                              kinds.length > 0 ? (
                                <span data-tabular>{t("detail.kinds.count", { count: kinds.length })}</span>
                              ) : undefined
                            }
                          />
                          {kinds.length === 0 ? (
                            <StateCard
                              compact
                              testId="kinds-empty"
                              icon={PackageOpen}
                              title={t("detail.kinds.emptyTitle")}
                              description={t("detail.kinds.emptyDescription")}
                            />
                          ) : (
                            <ul className="divide-y divide-border">
                              {kinds.map(([kind, rollup]) => {
                                const worst =
                                  rollup.unhealthy > 0
                                    ? "unhealthy"
                                    : rollup.degraded > 0
                                      ? "degraded"
                                      : rollup.unknown > 0
                                        ? "unknown"
                                        : "healthy";
                                return (
                                  <li
                                    key={kind}
                                    className="relative flex flex-wrap items-center gap-x-5 gap-y-2 px-7 py-4 transition-colors hover:bg-surface-hover"
                                  >
                                    <IconBubble icon={Boxes} size="small" />
                                    <Link
                                      href={`/clusters/${clusterId}/inventory?kind=${kind}`}
                                      aria-label={t("detail.kinds.browse", { kind })}
                                      className="w-44 shrink-0 rounded font-mono text-caption font-semibold text-ink after:absolute after:inset-0 after:content-[''] hover:text-brand"
                                    >
                                      {kind}
                                    </Link>
                                    {/* The bar is the share of the largest kind. */}
                                    <span
                                      aria-hidden
                                      className="block h-2 min-w-[6rem] flex-1 overflow-hidden rounded-full bg-surface-3"
                                    >
                                      <span
                                        className="block h-full rounded-full bg-info"
                                        style={{
                                          width: `${(rollup.total / largest) * 100}%`,
                                        }}
                                      />
                                    </span>
                                    <span
                                      data-tabular
                                      className="w-12 text-right text-body font-semibold text-ink"
                                    >
                                      {rollup.total}
                                    </span>
                                    <HealthBadge health={worst} />
                                    <ChevronRight
                                      aria-hidden
                                      className="h-4 w-4 text-ink-muted"
                                    />
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                        </Panel>
                      </div>
                    );
                  }}
                </LoadGate>
              </section>

              {/* Capacity is what the HOST can give, and it comes from the
                  metrics backend rather than from inventory — an agent can be
                  perfectly healthy on a node that is full. */}
              <section
                aria-label={t("detail.capacity.label")}
                className="mt-10 space-y-4"
              >
                <SectionHeader
                  title={t("detail.capacity.title")}
                  description={t("detail.capacity.description")}
                />
                <div>
                  <DashboardRenderer
                    templateKey="cluster-capacity-v1"
                    scopeType="cluster"
                    scopeId={clusterId}
                    preset={preset}
                    profile="kubernetes-service-v1"
                  />
                </div>
              </section>

              <div className="mt-10 grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2">
                <Panel data-testid="cluster-metadata" className="h-full">
                  <PanelHeader
                    title={t("detail.metadata.title")}
                    description={t("detail.metadata.description")}
                  />
                  <DefinitionGrid
                    items={[
                      {
                        label: t("detail.metadata.reference"),
                        value: (
                          <span className="font-mono text-caption">
                            {data.cluster_ref}
                          </span>
                        ),
                      },
                      {
                        label: t("detail.metadata.site"),
                        value: data.site || (
                          <span className="text-ink-muted">{t("detail.metadata.notSet")}</span>
                        ),
                      },
                      {
                        label: t("detail.metadata.catalogVersion"),
                        value: (
                          <span className="font-mono text-caption">
                            v{data.version}
                          </span>
                        ),
                      },
                      {
                        label: t("shared.lifecycle"),
                        value: t.dyn("enum.lifecycle", data.lifecycle, humanize(data.lifecycle)),
                      },
                    ]}
                  />
                  <div className="mt-auto rounded-[1rem] bg-surface-2 px-5 py-4">
                    <Provenance {...provenanceProps(data.source, data.as_of)} />
                  </div>
                </Panel>

                <Panel flush className="h-full">
                  <PanelHeader
                    flush
                    title={t("detail.environments.title")}
                    description={t("detail.environments.description")}
                    meta={
                      <span data-tabular>
                        {t("detail.environments.count", { count: environments.length })}
                      </span>
                    }
                  />
                  {environments.length > 0 ? (
                    <ul className="divide-y divide-border">
                      {environments.map((environment) => (
                        <li
                          key={`${environment.project_key}/${environment.environment_key}`}
                          className="flex items-center gap-4 px-7 py-3.5 transition-colors hover:bg-surface-hover"
                        >
                          <IconBubble icon={Layers} size="small" />
                          <span className="min-w-0 flex-1 truncate font-mono text-caption font-medium text-ink">
                            {environment.project_key}/
                            {environment.environment_key}
                          </span>
                          {environment.namespace ? (
                            <span className="shrink-0 rounded-full bg-surface-2 px-2.5 py-1 font-mono text-micro text-ink-secondary">
                              {environment.namespace}
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <StateCard
                      compact
                      testId="environments-empty"
                      icon={Layers}
                      title={t("detail.environments.emptyTitle")}
                      description={t("detail.environments.emptyDescription")}
                    />
                  )}
                </Panel>
              </div>
            </>
          );
        }}
      </ScreenGate>
    </PageFrame>
  );
}
