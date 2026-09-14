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
  formatUtc,
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
import type { HealthRollup, InventorySummary } from "@/lib/inventory";
import { parseRangePreset } from "@/lib/telemetry";

/** One rollup, as donut slices. The unknown bucket is always present. */
function healthSlices(rollup: HealthRollup) {
  return [
    { name: "Healthy", value: rollup.healthy, tone: "success" as const },
    { name: "Degraded", value: rollup.degraded, tone: "warning" as const },
    { name: "Unhealthy", value: rollup.unhealthy, tone: "critical" as const },
    { name: "Unknown", value: rollup.unknown, tone: "unknown" as const },
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
  return value ? (
    <span className="font-mono text-caption text-ink">{formatUtc(value)}</span>
  ) : (
    <span className="text-ink-muted">Not reported</span>
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
  return (
    <Panel data-testid={testId} className={`h-full ${className}`}>
      <div className="flex items-center gap-3">
        <IconBubble icon={icon} />
        <h3 className="min-w-0 flex-1 text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
          {title}
        </h3>
        <span data-tabular className="text-caption text-ink-muted">
          <span className="font-semibold text-ink">{rollup.total}</span> total
        </span>
      </div>
      <div
        className={
          children ? "flex flex-wrap items-center gap-x-10 gap-y-6" : ""
        }
      >
        <div className="min-w-0 flex-1">
          {rollup.total > 0 ? (
            <Donut
              size={120}
              thickness={13}
              label={`${title} by health`}
              centerLabel={`${rollup.total}`}
              slices={healthSlices(rollup)}
            />
          ) : (
            <div className="flex items-center gap-4 rounded-[1rem] bg-surface-2 px-5 py-4">
              <span
                aria-hidden
                className="h-10 w-10 shrink-0 rounded-full border-[5px] border-surface-3"
              />
              <div className="min-w-0">
                <p className="text-body font-medium text-ink">None recorded</p>
                <p className="text-caption text-ink-muted">
                  All health buckets are zero
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
    summary.state === "error" ? "Unavailable" : "Loading…";

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
            title="Not found"
            description="This resource does not exist in your authorized scope."
            action={
              <PillLink LinkComponent={Link} href="/clusters">
                Back to clusters
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
                aria-label="Breadcrumb"
                className="mb-3 flex items-center gap-1.5 text-micro text-ink-muted"
              >
                <Link href="/clusters" className="rounded hover:text-ink">
                  Clusters
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
                    ? `Site ${data.site} · catalog v${data.version}`
                    : `Catalog v${data.version}`
                }
                status={
                  <StatusBadge
                    status={data.lifecycle === "active" ? "success" : "unknown"}
                    label={humanize(data.lifecycle)}
                  />
                }
                actions={
                  <PillLink
                    LinkComponent={Link}
                    href={`/clusters/${clusterId}/inventory`}
                    icon={Boxes}
                    variant="primary"
                  >
                    Browse inventory
                  </PillLink>
                }
              />

              <div className="page-grid mb-6">
                <StatTile
                  icon={Plug}
                  tone={ready ? toneForHealth(ready.agent.status) : null}
                  label="Agent connection"
                  value={
                    <StatWord muted={!ready}>
                      {ready ? humanize(ready.agent.status) : summaryFallback}
                    </StatWord>
                  }
                >
                  <p className="text-micro text-ink-muted">
                    Heartbeat{" "}
                    <span className="text-ink-secondary">
                      {ready?.agent.last_heartbeat_at ? (
                        <RelativeTime value={ready.agent.last_heartbeat_at} />
                      ) : (
                        "not reported"
                      )}
                    </span>
                  </p>
                </StatTile>
                <StatTile
                  icon={RefreshCw}
                  tone={ready ? toneForHealth(ready.inventory.state) : null}
                  label="Inventory"
                  value={
                    <StatWord muted={!ready}>
                      {ready
                        ? humanize(ready.inventory.state)
                        : summaryFallback}
                    </StatWord>
                  }
                >
                  <p className="text-micro text-ink-muted">
                    Reconciled{" "}
                    <span className="text-ink-secondary">
                      {ready?.inventory.last_reconcile_at ? (
                        <RelativeTime
                          value={ready.inventory.last_reconcile_at}
                        />
                      ) : (
                        "not reported"
                      )}
                    </span>
                  </p>
                </StatTile>
                <StatTile
                  icon={Boxes}
                  label="Resources"
                  value={ready ? (ready.inventory.active_resources ?? 0) : "—"}
                  suffix="active"
                >
                  {ready ? (
                    <SegmentBar
                      label="Resources by lifecycle"
                      segments={[
                        {
                          key: "active",
                          label: "Active",
                          value: ready.inventory.active_resources ?? 0,
                          tone: "info",
                        },
                        {
                          key: "missing",
                          label: "Missing",
                          value: ready.inventory.missing_resources ?? 0,
                          tone: "stale",
                        },
                      ]}
                    />
                  ) : null}
                </StatTile>
                <StatTile
                  icon={Layers}
                  label="Environments"
                  value={environments.length}
                  suffix="authorized"
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
                      None you can see run here
                    </p>
                  )}
                </StatTile>
              </div>

              <section
                aria-label="Cluster agent and inventory"
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
                      ["Nodes", Server, inventory.nodes],
                      ["Namespaces", FolderTree, inventory.namespaces],
                      ["Workloads", Workflow, inventory.workloads],
                      ["Pods", Container, inventory.pods],
                      [
                        "Volume claims",
                        Database,
                        inventory.persistent_volume_claims,
                      ],
                    ] as const;
                    return (
                      <div className="space-y-6">
                        <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2">
                          <Panel data-testid="agent-card" className="h-full">
                            <PanelHeader
                              title="Cluster agent"
                              description="What the agent last reported about itself"
                              actions={
                                <AgentBadge status={inventory.agent.status} />
                              }
                            />
                            <DefinitionGrid
                              items={[
                                {
                                  label: "Agent version",
                                  value: inventory.agent.agent_version ? (
                                    <span className="font-mono text-caption">
                                      {inventory.agent.agent_version}
                                    </span>
                                  ) : (
                                    <span className="text-ink-muted">
                                      Not reported
                                    </span>
                                  ),
                                },
                                {
                                  label: "Last heartbeat",
                                  value: (
                                    <Instant
                                      value={inventory.agent.last_heartbeat_at}
                                    />
                                  ),
                                },
                                {
                                  label: "Certificate expires",
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
                                          label="expires soon"
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
                                  label="Certificate runway"
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
                              title="Inventory freshness"
                              description="How current the last sweep is"
                              actions={
                                <InventoryStateBadge
                                  state={inventory.inventory.state}
                                />
                              }
                            />
                            <DefinitionGrid
                              items={[
                                {
                                  label: "Last full reconcile",
                                  value: (
                                    <Instant
                                      value={
                                        inventory.inventory.last_reconcile_at
                                      }
                                    />
                                  ),
                                },
                                {
                                  label: "Last change applied",
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
                                  Active resources
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
                                  Missing resources
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
                          title="What the last sweep found"
                          description="Health composition per resource class, as the agent reported it."
                        />
                        {classes.every(([, , rollup]) => rollup.total === 0) ? (
                          // Nothing recorded in any class: one card of five
                          // zero tiles, not five cards repeating one sentence.
                          <Panel>
                            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
                              {classes.map(([title, icon, rollup]) => (
                                <div
                                  key={title}
                                  data-testid={
                                    title === "Pods" ? "pods-card" : undefined
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
                                      recorded
                                    </span>
                                  </span>
                                </div>
                              ))}
                            </div>
                            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
                              <p className="text-caption text-ink-muted">
                                Every health bucket is zero in the last summary.
                              </p>
                              <ToneCounters
                                size="compact"
                                items={[
                                  {
                                    label: "restarts",
                                    count: inventory.pods.restarts,
                                    tone: "warning",
                                  },
                                  {
                                    label: "CrashLoop",
                                    count: inventory.pods.crashloop,
                                    tone: "critical",
                                  },
                                  {
                                    label: "OOM killed",
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
                              title="Nodes"
                              icon={Server}
                              rollup={inventory.nodes}
                            />
                            <RollupCard
                              title="Namespaces"
                              icon={FolderTree}
                              rollup={inventory.namespaces}
                            />
                            <RollupCard
                              title="Workloads"
                              icon={Workflow}
                              rollup={inventory.workloads}
                            />
                            <RollupCard
                              data-testid="pods-card"
                              title="Pods"
                              icon={Container}
                              rollup={inventory.pods}
                              className="xl:col-span-2"
                            >
                              {/* Failure modes, not a composition: these do not
                                  add up to the pod total, so they are counters
                                  rather than wedges. */}
                              <div className="min-w-[12rem] rounded-[1rem] bg-surface-2 px-5 py-4">
                                <p className="mb-3 text-micro tracking-[0.08em] text-ink-muted uppercase">
                                  Pod instability
                                </p>
                                <ToneCounters
                                  items={[
                                    {
                                      label: "restarts",
                                      count: inventory.pods.restarts,
                                      tone: "warning",
                                    },
                                    {
                                      label: "CrashLoop",
                                      count: inventory.pods.crashloop,
                                      tone: "critical",
                                    },
                                    {
                                      label: "OOM killed",
                                      count: inventory.pods.oom_killed,
                                      tone: "critical",
                                    },
                                  ]}
                                />
                              </div>
                            </RollupCard>
                            <RollupCard
                              title="Persistent volume claims"
                              icon={Database}
                              rollup={inventory.persistent_volume_claims}
                            />
                          </div>
                        )}

                        <Panel flush>
                          <PanelHeader
                            flush
                            title="Resources by kind"
                            description="Sorted by count — the shape of the cluster before any digit"
                            meta={
                              kinds.length > 0 ? (
                                <span data-tabular>{kinds.length} kinds</span>
                              ) : undefined
                            }
                          />
                          {kinds.length === 0 ? (
                            <StateCard
                              compact
                              testId="kinds-empty"
                              icon={PackageOpen}
                              title="No snapshot yet"
                              description="No completed snapshot has been received from this cluster."
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
                                      aria-label={`Browse ${kind}`}
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
                aria-label="Cluster capacity"
                className="mt-10 space-y-4"
              >
                <SectionHeader
                  title="Capacity"
                  description="What the hosts can still give, from the metrics backend."
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
                    title="Cluster metadata"
                    description="The catalog record"
                  />
                  <DefinitionGrid
                    items={[
                      {
                        label: "Reference",
                        value: (
                          <span className="font-mono text-caption">
                            {data.cluster_ref}
                          </span>
                        ),
                      },
                      {
                        label: "Site",
                        value: data.site || (
                          <span className="text-ink-muted">Not set</span>
                        ),
                      },
                      {
                        label: "Catalog version",
                        value: (
                          <span className="font-mono text-caption">
                            v{data.version}
                          </span>
                        ),
                      },
                      { label: "Lifecycle", value: humanize(data.lifecycle) },
                    ]}
                  />
                  <div className="mt-auto rounded-[1rem] bg-surface-2 px-5 py-4">
                    <Provenance {...provenanceProps(data.source, data.as_of)} />
                  </div>
                </Panel>

                <Panel flush className="h-full">
                  <PanelHeader
                    flush
                    title="Referenced environments"
                    description="Authorized only"
                    meta={
                      <span data-tabular>
                        {environments.length} environments
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
                      title="No environments"
                      description="Environments you can see that run on this cluster will appear here."
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
