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
  Container,
  Database,
  FolderTree,
  Layers,
  Server,
  ShieldCheck,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";

import { useApi, provenanceProps } from "@/components/catalog/primitives";
import { Countdown, ToneCounters } from "@/components/charts/visuals";
import {
  DefinitionGrid,
  IconBubble,
  PillLink,
  ScreenGate,
  SegmentBar,
  StateCard,
} from "@/components/clusters/primitives";
import {
  AgentBadge,
  InventoryStateBadge,
} from "@/components/inventory/primitives";
import { Provenance } from "@/components/provenance/Provenance";
import { ClusterLogo, StateChip, useClusterSnapshot, usableSnapshot } from "@/components/clusters/ClusterCard";
import {
  CapacityCard,
  ClusterApps,
  DashCard,
  KindsViz,
  NamespaceGroups,
} from "@/components/clusters/ClusterDashboard";
import { NodeLedger } from "@/components/features/infrastructure/InfrastructureScreen";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import { LoadGate } from "@/components/catalog/primitives";
import { Panel, PanelHeader, SectionHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { Cluster } from "@/lib/catalog";
import { clusterPicture } from "@/lib/clusterPicture";
import { humanize } from "@/lib/design/status";
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
    <Panel data-testid={testId ?? "rollup-card"} className={`h-full ${className}`}>
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
            <SegmentBar
              label={t("detail.sweep.byHealth", { title })}
              segments={healthSlices(t, rollup).map((slice) => ({
                key: slice.tone,
                label: slice.name,
                value: slice.value,
                tone: slice.tone,
              }))}
            />
          ) : (
            <div className="flex items-center gap-4 rounded-[0.625rem] bg-surface-2 px-5 py-4">
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
  const { clusterId } = useParams<{ clusterId: string }>();
  // The same range control the service boards use, read from the URL,
  // so a link to a cluster at 7d opens at 7d.
  const preset = parseRangePreset(useSearchParams().get("range"));
  const [cluster, retry] = useApi<Cluster>(`/v1/clusters/${clusterId}`);
  const [summary, retrySummary] = useApi<InventorySummary>(
    `/v1/clusters/${clusterId}/inventory/summary`,
  );
  const ti = useT("infrastructure");
  const { snap, failed: snapFailed } = useClusterSnapshot(clusterId);
  const picture = usableSnapshot(snap) ? clusterPicture(snap) : null;

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
              <PageHeader
                leading={<ClusterLogo cluster={data} size="lg" />}
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
                meta={
                  <>
                    <StateChip
                      label={t("card.agent")}
                      value={data.operational?.agent}
                      word={t.dyn("enum.agent", data.operational?.agent ?? "unknown", humanize(data.operational?.agent ?? "unknown"))}
                    />
                    <StateChip
                      label={t("card.inventory")}
                      value={data.operational?.inventory}
                      word={t.dyn("enum.inventory", data.operational?.inventory ?? "unknown", humanize(data.operational?.inventory ?? "unknown"))}
                    />
                  </>
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

              <div className="mb-6 flex flex-col gap-6">
                {picture ? (
                  <>
                    <CapacityCard picture={picture} clusterId={clusterId} />
                    <ClusterApps picture={picture} />
                    <DashCard
                      title={t("dash.nodes.title")}
                      description={t("dash.nodes.description")}
                      testId="cluster-nodes"
                    >
                      <NodeLedger snap={snap!} />
                    </DashCard>
                    <NamespaceGroups
                      picture={picture}
                      groupName={(g) =>
                        g.kind === "project" || g.kind === "unclaimed" ? g.name : ti.dyn("group", g.kind, g.name)
                      }
                    />
                  </>
                ) : (
                  <DashCard title={t("card.usageTitle")} description={t("dash.capacity.description")} testId="cluster-capacity">
                    <p className="text-caption text-ink-muted">{snapFailed || snap ? t("card.noMetrics") : t("card.reading")}</p>
                  </DashCard>
                )}
              </div>

              <section
                aria-label={t("detail.section")}
                className="space-y-6"
              >
                <LoadGate value={summary} retry={retrySummary}>
                  {(inventory) => {
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
                        <KindsViz byKind={inventory.by_kind} clusterId={clusterId} />
                        <SectionHeader title={t("dash.status.title")} />
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
                            <div className="mt-auto flex items-center gap-4 rounded-[0.625rem] bg-surface-2 px-5 py-4">
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
                              <div className="rounded-[0.625rem] bg-surface-2 px-5 py-4">
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
                              <div className="rounded-[0.625rem] bg-surface-2 px-5 py-4">
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
                          title={t("dash.health.title")}
                          description={t("dash.health.description")}
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
                                  className="flex min-w-0 flex-col gap-3 rounded-[0.625rem] bg-surface-2 px-5 py-4"
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
                              <div className="min-w-[12rem] rounded-[0.625rem] bg-surface-2 px-5 py-4">
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
                  <div className="mt-auto rounded-[0.625rem] bg-surface-2 px-5 py-4">
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
