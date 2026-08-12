"use client";

/**
 * Command Center.
 *
 * One question, answered in ten seconds: where is the problem?
 *
 * The layout follows that and nothing else. A triage strip of real counts, a
 * ranked list of the actual things that are wrong, and only then the standing
 * inventory — fleet, services, integrations, catalog. There is no hero, no
 * row of equal-sized vanity tiles, and no chart that exists to fill space.
 *
 * Every section is independently authorized. A caller without `cluster.view`
 * sees the fleet panel say "permission required" while the rest of the page
 * works; it never contributes a zero to the counts above, because a zero
 * there would read as "no clusters need attention" when the truth is "you
 * cannot see the clusters".
 *
 * The empty state is the part worth reading twice. When nothing needs
 * attention the page does NOT claim the platform is healthy — it says what it
 * checked, how many of those sources answered, and when. A green tick that
 * actually means "four of your six sources are not configured" is the single
 * most dangerous thing a monitoring product can render.
 */

import { ArrowRight, Boxes, Plug, RefreshCw, ShieldAlert, Siren, Waypoints } from "lucide-react";
import Link from "next/link";

import { Donut, RingProgress } from "@/components/charts/visuals";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader, SectionHeader } from "@/components/ui/Panel";
import { StatusBadge, StatusDot } from "@/components/ui/StatusBadge";
import { Button } from "@/components/ui/controls";
import { FreshnessIndicator, RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  NotConfiguredState,
} from "@/components/ui/states";
import type { AlertSummary } from "@/lib/alerting";
import type { CatalogContext, Cluster, IntegrationHealth } from "@/lib/catalog";
import { humanize, toneForHealth, toneSpec, type StatusTone } from "@/lib/design/status";
import type { IncidentSummary } from "@/lib/incidents";
import {
  alertItems,
  clusterItems,
  incidentItems,
  integrationItems,
  serviceItems,
  sortAttention,
  tallyByTone,
  type AttentionItem,
} from "@/lib/overview";
import type { InventorySummary } from "@/lib/inventory";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import { useResource, type Resource } from "@/lib/useResource";

const REFRESH_MS = 60_000;

export default function CommandCenterPage() {
  const context = useResource<CatalogContext>("/v1/catalog/context", { refreshMs: REFRESH_MS });
  const incidents = useResource<{ items: IncidentSummary[]; total: number }>(
    "/v1/incidents?state=open&limit=25",
    { refreshMs: REFRESH_MS },
  );
  const alerts = useResource<AlertSummary>("/v1/alerts/summary", { refreshMs: REFRESH_MS });
  const clusters = useResource<{ clusters: Cluster[] }>("/v1/clusters", { refreshMs: REFRESH_MS });
  const services = useResource<{ items: ServiceHealthRow[] }>("/v1/service-health/services", {
    refreshMs: REFRESH_MS,
  });
  const integrations = useResource<{ integrations: IntegrationHealth[] }>(
    "/v1/integrations/health",
    { refreshMs: REFRESH_MS },
  );

  const sources = [
    { key: "incidents", label: "Incidents", resource: incidents },
    { key: "alerts", label: "Alerts", resource: alerts },
    { key: "clusters", label: "Clusters", resource: clusters },
    { key: "services", label: "Service health", resource: services },
    { key: "integrations", label: "Integrations", resource: integrations },
  ] as const;

  const anyLoading = sources.some(({ resource }) => resource.loading && !resource.data);
  const refreshing = sources.some(({ resource }) => resource.refreshing);
  const answered = sources.filter(({ resource }) => resource.data !== null);
  const unavailable = sources.filter(
    ({ resource }) => resource.data === null && !resource.loading,
  );

  const attention = sortAttention([
    ...(incidents.data ? incidentItems(incidents.data.items) : []),
    ...(alerts.data ? alertItems(alerts.data) : []),
    ...(clusters.data ? clusterItems(clusters.data.clusters) : []),
    ...(services.data ? serviceItems(services.data.items) : []),
    ...(integrations.data ? integrationItems(integrations.data.integrations) : []),
  ]);

  const reloadAll = () => sources.forEach(({ resource }) => resource.reload());

  return (
    <PageFrame width="wide">
      <PageHeader
        title="Command Center"
        description="Everything that needs attention across your authorized scope, worst first."
        meta={
          <>
            <FreshnessIndicator
              asOf={context.fetchedAt}
              state={context.error ? "unknown" : "fresh"}
            />
            <span>
              {answered.length} of {sources.length} sources answered
            </span>
            {refreshing ? <span>refreshing…</span> : null}
          </>
        }
        actions={
          <Button icon={RefreshCw} onClick={reloadAll} disabled={refreshing}>
            Refresh
          </Button>
        }
      />

      <TriageStrip
        attention={attention}
        incidents={incidents}
        alerts={alerts}
        clusters={clusters}
        services={services}
        integrations={integrations}
      />

      <div className="mt-5 grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <NeedsAttention
          items={attention}
          loading={anyLoading}
          answered={answered.map(({ label }) => label)}
          unavailable={unavailable.map(({ label, resource }) => ({
            label,
            reason: resource.denied ? "permission required" : (resource.error ?? "unavailable"),
          }))}
        />
        <div className="flex flex-col gap-4">
          <CatalogPanel resource={context} />
          <ServiceHealthPanel resource={services} />
        </div>
      </div>

      <div className="mt-6">
        <SectionHeader
          title="Standing state"
          description="What Drake is watching, and how current each source is."
        />
        <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <FleetPanel resource={clusters} />
          <IntegrationsPanel resource={integrations} />
        </div>
      </div>
    </PageFrame>
  );
}

/**
 * The triage strip.
 *
 * Counts, not gauges, and each one is a link into the list it summarises. A
 * source that could not be read shows a dash and the reason — never `0`.
 */
function TriageStrip({
  attention,
  incidents,
  alerts,
  clusters,
  services,
  integrations,
}: {
  attention: AttentionItem[];
  incidents: Resource<{ items: IncidentSummary[]; total: number }>;
  alerts: Resource<AlertSummary>;
  clusters: Resource<{ clusters: Cluster[] }>;
  services: Resource<{ items: ServiceHealthRow[] }>;
  integrations: Resource<{ integrations: IntegrationHealth[] }>;
}) {
  const countBy = (origin: AttentionItem["origin"], tone?: StatusTone) =>
    attention.filter((item) => item.origin === origin && (!tone || item.tone === tone)).length;

  const tiles = [
    {
      key: "incidents",
      label: "Open incidents",
      icon: Siren,
      href: "/incidents",
      resource: incidents,
      value: incidents.data?.items.filter((item) => item.state !== "resolved").length ?? null,
      total: incidents.data?.items.length ?? null,
      tone: countBy("incident", "critical") > 0 ? ("critical" as const) : ("neutral" as const),
      detail: incidents.data
        ? `${incidents.data.items.filter((item) => item.state === "acknowledged").length} acknowledged`
        : null,
    },
    {
      key: "alerts",
      label: "Firing alerts",
      icon: ShieldAlert,
      href: "/alerts",
      resource: alerts,
      value: alerts.data?.firing ?? null,
      total: alerts.data ? alerts.data.firing + alerts.data.silenced : null,
      tone:
        (alerts.data?.p1 ?? 0) > 0
          ? ("critical" as const)
          : (alerts.data?.p2 ?? 0) > 0
            ? ("warning" as const)
            : ("neutral" as const),
      detail: alerts.data ? `P1 ${alerts.data.p1} · P2 ${alerts.data.p2}` : null,
    },
    {
      key: "clusters",
      label: "Clusters needing attention",
      icon: Boxes,
      href: "/clusters",
      resource: clusters,
      value: clusters.data ? countBy("cluster") : null,
      total: clusters.data?.clusters.length ?? null,
      tone: countBy("cluster") > 0 ? ("warning" as const) : ("neutral" as const),
      detail: clusters.data ? `${clusters.data.clusters.length} in scope` : null,
    },
    {
      key: "services",
      label: "Services not healthy",
      icon: Waypoints,
      href: "/service-health",
      resource: services,
      value: services.data ? countBy("service") : null,
      total: services.data?.items.length ?? null,
      tone:
        countBy("service", "critical") > 0
          ? ("critical" as const)
          : countBy("service") > 0
            ? ("warning" as const)
            : ("neutral" as const),
      detail: services.data ? `${services.data.items.length} tracked` : null,
    },
    {
      key: "integrations",
      label: "Integrations degraded",
      icon: Plug,
      href: "/integrations",
      resource: integrations,
      value: integrations.data ? countBy("integration") : null,
      total:
        integrations.data?.integrations.filter(
          (entry) => entry.configuration_state === "configured",
        ).length ?? null,
      tone: countBy("integration") > 0 ? ("warning" as const) : ("neutral" as const),
      detail: integrations.data
        ? `${integrations.data.integrations.filter((entry) => entry.configuration_state === "configured").length} configured`
        : null,
    },
  ];

  return (
    <div
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5"
      data-testid="triage-strip"
    >
      {tiles.map((tile) => {
        const spec = toneSpec(tile.value === 0 ? "neutral" : tile.tone);
        const Icon = tile.icon;
        const unreadable = tile.value === null;
        return (
          <Link
            key={tile.key}
            href={tile.href}
            data-testid={`triage-${tile.key}`}
            className="flex min-w-0 flex-col rounded-panel border border-border bg-surface px-3.5 py-3 transition-colors hover:border-border-strong hover:bg-surface-hover"
          >
            <span className="flex items-center gap-1.5 text-caption text-ink-secondary">
              <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{tile.label}</span>
            </span>
            <span className="mt-1.5 flex items-baseline gap-2">
              {tile.resource.loading && unreadable ? (
                <span className="inline-block h-7 w-10 animate-pulse rounded bg-surface-3 motion-reduce:animate-none" />
              ) : unreadable ? (
                <span className="text-title font-semibold text-ink-muted">—</span>
              ) : (
                <span
                  data-tabular
                  className={`text-metric font-semibold ${
                    tile.value === 0 ? "text-ink" : spec.text
                  }`}
                >
                  {tile.value}
                </span>
              )}
            </span>
            {/* The share of what Drake watches that is affected. A bare count
                cannot say whether 1 is one-of-two or one-of-four-hundred. */}
            {!unreadable && tile.total ? (
              <span
                aria-hidden
                className="mt-1.5 block h-1 w-full overflow-hidden rounded-full bg-surface-3"
              >
                <span
                  className={`block h-full rounded-full ${
                    tile.value === 0 ? "bg-border-strong" : spec.dot
                  }`}
                  style={{
                    width: `${Math.min(100, ((tile.value ?? 0) / tile.total) * 100)}%`,
                  }}
                />
              </span>
            ) : null}
            <span className="mt-1 truncate text-micro text-ink-muted">
              {unreadable
                ? tile.resource.denied
                  ? "permission required"
                  : tile.resource.loading
                    ? "loading"
                    : "source unavailable"
                : (tile.detail ?? "")}
            </span>
          </Link>
        );
      })}
    </div>
  );
}

function NeedsAttention({
  items,
  loading,
  answered,
  unavailable,
}: {
  items: AttentionItem[];
  loading: boolean;
  answered: string[];
  unavailable: { label: string; reason: string }[];
}) {
  return (
    <Panel flush data-testid="needs-attention">
      <PanelHeader
        flush
        title="Needs attention"
        description="Critical first, then warnings, then anything Drake cannot currently see."
        meta={items.length > 0 ? <span>{items.length} items</span> : undefined}
        actions={
          items.length > 0 ? (
            <Link
              href="/incidents"
              className="inline-flex items-center gap-1 rounded text-caption font-medium text-brand hover:underline"
            >
              All incidents
              <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : undefined
        }
      />

      {loading ? (
        <div className="px-4 py-4">
          <LoadingSkeleton variant="table" rows={4} label="Loading attention list" />
        </div>
      ) : items.length === 0 ? (
        <div className="px-4 py-5" data-testid="attention-empty">
          <p className="text-body font-medium text-ink">Nothing is currently flagged.</p>
          <p className="mt-1 max-w-prose text-caption text-ink-secondary">
            This is not a statement that the platform is healthy — it is the result of the
            checks below. Anything Drake has no source for cannot appear here.
          </p>
          <dl className="mt-3 space-y-1.5 text-caption">
            <div className="flex flex-wrap items-baseline gap-2">
              <dt className="text-ink-muted">Checked:</dt>
              <dd className="text-ink">{answered.join(", ") || "nothing"}</dd>
            </div>
            {unavailable.length > 0 ? (
              <div className="flex flex-wrap items-baseline gap-2">
                <dt className="text-ink-muted">Not checked:</dt>
                <dd className="text-warning">
                  {unavailable.map((entry) => `${entry.label} (${entry.reason})`).join(", ")}
                </dd>
              </div>
            ) : null}
          </dl>
        </div>
      ) : (
        <ul className="divide-y divide-border" data-testid="attention-list">
          {items.map((item) => {
            const spec = toneSpec(item.tone);
            const Icon = spec.icon;
            return (
              <li key={item.key}>
                <Link
                  href={item.href}
                  className={`flex items-start gap-3 border-l-2 px-4 py-2.5 transition-colors hover:bg-surface-hover ${spec.rail}`}
                >
                  <Icon aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${spec.text}`} />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-body font-medium text-ink">{item.subject}</span>
                      <span className={`text-caption ${spec.text}`}>{item.state}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-micro text-ink-muted">
                      {item.context}
                    </span>
                  </span>
                  {item.asOf ? (
                    <span className="shrink-0 text-micro text-ink-muted">
                      <RelativeTime value={item.asOf} />
                    </span>
                  ) : null}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function CatalogPanel({ resource }: { resource: Resource<CatalogContext> }) {
  return (
    <Panel data-testid="catalog-counts">
      <PanelHeader title="Your catalog" description="Records you are authorized to see." />
      {resource.loading && !resource.data ? (
        <LoadingSkeleton rows={2} />
      ) : resource.denied ? (
        <DeniedState compact />
      ) : !resource.data ? (
        <ErrorState compact description={resource.error ?? undefined} onRetry={resource.reload} />
      ) : (
        /* A list of counts, not term/definition pairs: a <dl> whose children
           are links is both wrong markup and an axe violation. */
        <ul className="grid grid-cols-3 gap-2">
          {(
            [
              ["Projects", resource.data.projects, "/projects"],
              ["Environments", resource.data.environments, null],
              ["Clusters", resource.data.clusters, "/clusters"],
            ] as const
          ).map(([label, count, href]) => {
            const body = (
              <>
                <span data-tabular className="text-title font-semibold text-ink">
                  {count}
                </span>
                <span className="mt-0.5 block text-micro text-ink-muted">{label}</span>
              </>
            );
            return (
              <li key={label}>
                {href ? (
                  <Link
                    href={href}
                    className="block rounded-control px-2 py-1.5 transition-colors hover:bg-surface-hover"
                  >
                    {body}
                  </Link>
                ) : (
                  <span className="block px-2 py-1.5">{body}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function ServiceHealthPanel({ resource }: { resource: Resource<{ items: ServiceHealthRow[] }> }) {
  const rows = resource.data?.items ?? [];
  const tally = tallyByTone(rows, (row) => toneForHealth(row.health.status));
  return (
    <Panel data-testid="service-health-rollup">
      <PanelHeader
        title="Service health"
        description="Every tracked service, by the state its own binding reports."
      />
      {resource.loading && !resource.data ? (
        <LoadingSkeleton rows={2} />
      ) : resource.denied ? (
        <DeniedState compact />
      ) : !resource.data ? (
        <ErrorState compact description={resource.error ?? undefined} onRetry={resource.reload} />
      ) : rows.length === 0 ? (
        <NotConfiguredState
          compact
          title="No services tracked"
          description="Services appear here once an environment service is bound to a workload."
        />
      ) : (
        <>
          <Donut
            label="Service health"
            centerLabel={`${rows.length}`}
            slices={tally.map((entry) => ({
              name: toneSpec(entry.tone).label,
              value: entry.count,
              tone: entry.tone,
            }))}
          />
          <Link
            href="/service-health"
            className="inline-flex items-center gap-1 rounded text-caption font-medium text-brand hover:underline"
          >
            Open service health
            <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </Link>
        </>
      )}
    </Panel>
  );
}

/**
 * The fleet.
 *
 * Connection and inventory are separate columns because they are separate
 * facts: an agent can be connected while its last inventory sweep is an hour
 * stale, and collapsing those into one "cluster status" hides exactly the
 * case an operator needs to catch.
 */
function FleetPanel({ resource }: { resource: Resource<{ clusters: Cluster[] }> }) {
  const clusters = resource.data?.clusters ?? [];
  return (
    <Panel flush data-testid="fleet-panel">
      <PanelHeader
        flush
        title="Cluster fleet"
        description="Connection and inventory freshness are reported separately — connected is not healthy."
        actions={
          <Link
            href="/clusters"
            className="rounded text-caption font-medium text-brand hover:underline"
          >
            All clusters
          </Link>
        }
      />
      {resource.loading && !resource.data ? (
        <div className="px-4 py-4">
          <LoadingSkeleton variant="table" rows={3} />
        </div>
      ) : resource.denied ? (
        <div className="px-4 py-2">
          <DeniedState compact />
        </div>
      ) : !resource.data ? (
        <div className="px-4 py-2">
          <ErrorState compact description={resource.error ?? undefined} onRetry={resource.reload} />
        </div>
      ) : clusters.length === 0 ? (
        <div className="px-4 py-2">
          <NotConfiguredState compact title="No clusters in scope" />
        </div>
      ) : (
        <div className="w-full min-w-0 max-w-full overflow-x-auto [contain:paint]">
        <table className="w-full text-body" data-tabular>
          <caption className="sr-only">
            Clusters in your scope, with agent connection and inventory freshness
          </caption>
          <thead className="bg-surface-2 text-caption text-ink-secondary">
            <tr>
              <th scope="col" className="px-4 py-1.5 text-left font-medium">
                Cluster
              </th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Agent
              </th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Inventory
              </th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Healthy / total
              </th>
              <th scope="col" className="px-4 py-1.5 text-right font-medium">
                Observed
              </th>
            </tr>
          </thead>
          <tbody>
            {clusters.map((cluster) => (
              <tr key={cluster.id} className="border-t border-border hover:bg-surface-hover">
                <td className="px-4 py-2">
                  <Link
                    href={`/clusters/${cluster.id}`}
                    className="rounded font-medium text-ink hover:text-brand"
                  >
                    {cluster.display_name || cluster.cluster_ref}
                  </Link>
                  <span className="block font-mono text-micro text-ink-muted">
                    {cluster.cluster_ref}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <StatusDot
                    status={toneForHealth(cluster.operational?.agent)}
                    label={humanize(cluster.operational?.agent ?? "unknown")}
                  />
                </td>
                <td className="px-3 py-2">
                  <StatusDot
                    status={toneForHealth(cluster.operational?.inventory)}
                    label={humanize(cluster.operational?.inventory ?? "unknown")}
                  />
                </td>
                <td className="px-3 py-2">
                  <FleetCounts cluster={cluster} />
                </td>
                <td className="px-4 py-2 text-right text-micro text-ink-muted">
                  <RelativeTime value={cluster.as_of} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </Panel>
  );
}

/**
 * What the agent's last sweep actually counted.
 *
 * Healthy-over-total, per cluster, straight from the summary — never a
 * percentage this component computed and never a bare "healthy".
 *
 * An agent that is not connected has no current view to report, and saying so
 * beats showing its last numbers as if they were now. That is the same rule
 * the freshness column states, applied to the numbers themselves.
 */
function FleetCounts({ cluster }: { cluster: Cluster }) {
  const summary = useResource<InventorySummary>(
    `/v1/clusters/${cluster.id}/inventory/summary`,
  );

  if (summary.loading && !summary.data) {
    return <span className="text-micro text-ink-muted">…</span>;
  }
  if (!summary.data) {
    return <span className="text-micro text-ink-muted">—</span>;
  }
  if (summary.data.agent.status !== "connected") {
    // No current view to report. The agent and inventory columns beside this
    // one already name which half is missing, and showing the last numbers
    // here would present them as if they were now.
    return (
      <span className="text-micro text-ink-muted" data-testid="fleet-counts-unavailable">
        no current sweep
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-3" data-testid="fleet-counts">
      {(
        [
          ["nodes", summary.data.nodes],
          ["workloads", summary.data.workloads],
          ["pods", summary.data.pods],
        ] as const
      ).map(([label, rollup]) => (
        <span key={label} className="flex items-center gap-1.5">
          {/* The ring reads before the digits do; the digits stay exact. */}
          <RingProgress
            size={34}
            label={`${label} healthy`}
            value={rollup.total > 0 ? (rollup.healthy / rollup.total) * 100 : null}
            tone={
              rollup.unhealthy > 0
                ? "critical"
                : rollup.degraded > 0 || rollup.unknown > 0
                  ? "warning"
                  : "success"
            }
          />
          <span className="text-micro whitespace-nowrap text-ink-secondary">
            <span data-tabular className="block font-medium text-ink">
              {rollup.healthy}
              <span className="text-ink-muted">/{rollup.total}</span>
            </span>
            {label}
          </span>
        </span>
      ))}
    </span>
  );
}

/**
 * Integration health.
 *
 * An operational list, not a marketplace. Configured-and-failing sorts above
 * configured-and-fine, and not-configured sits at the bottom in muted type,
 * because "you have not connected this" is not a problem to be triaged.
 */
function IntegrationsPanel({
  resource,
}: {
  resource: Resource<{ integrations: IntegrationHealth[] }>;
}) {
  const all = resource.data?.integrations ?? [];
  const configured = all.filter((entry) => entry.configuration_state === "configured");
  const notConfigured = all.length - configured.length;
  const sorted = [...configured].sort(
    (a, b) =>
      toneSpec(toneForHealth(a.observed_state)).label.localeCompare(
        toneSpec(toneForHealth(b.observed_state)).label,
      ) || a.integration_type.localeCompare(b.integration_type),
  );

  return (
    <Panel flush data-testid="integrations-panel">
      <PanelHeader
        flush
        title="Integrations"
        description="Only configured providers report a state; the rest are simply not connected."
        actions={
          <Link
            href="/integrations"
            className="rounded text-caption font-medium text-brand hover:underline"
          >
            Manage
          </Link>
        }
      />
      {resource.loading && !resource.data ? (
        <div className="px-4 py-4">
          <LoadingSkeleton variant="table" rows={3} />
        </div>
      ) : resource.denied ? (
        <div className="px-4 py-2">
          <DeniedState compact />
        </div>
      ) : !resource.data ? (
        <div className="px-4 py-2">
          <ErrorState compact description={resource.error ?? undefined} onRetry={resource.reload} />
        </div>
      ) : (
        <>
          {all.length > 0 ? (
            <div className="border-b border-border px-4 py-3">
              <Donut
                size={110}
                thickness={12}
                label="Integrations by state"
                centerLabel={`${all.length}`}
                slices={[
                  {
                    name: "Reporting ok",
                    value: configured.filter((entry) => entry.observed_state === "ok").length,
                    tone: "success",
                  },
                  {
                    name: "Degraded",
                    value: configured.filter((entry) => entry.observed_state !== "ok").length,
                    tone: "warning",
                  },
                  { name: "Not connected", value: notConfigured, tone: "not-applicable" },
                ]}
              />
            </div>
          ) : null}
          <ul className="divide-y divide-border">
            {sorted.map((integration) => (
              <li
                key={`${integration.integration_type}:${integration.scope.ref}`}
                className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-2"
              >
                <span className="min-w-0">
                  <span className="block truncate text-body text-ink">
                    {humanize(integration.integration_type)}
                  </span>
                  <span className="block truncate font-mono text-micro text-ink-muted">
                    {integration.scope.type}:{integration.scope.ref}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-3">
                  <FreshnessIndicator
                    asOf={integration.last_success_at}
                    state={integration.last_success_at ? "fresh" : "unknown"}
                  />
                  <StatusBadge
                    status={toneForHealth(integration.observed_state)}
                    label={humanize(integration.observed_state)}
                    size="compact"
                  />
                </span>
              </li>
            ))}
          </ul>
          {sorted.length === 0 ? (
            <div className="px-4 py-2">
              <NotConfiguredState
                compact
                title="No provider is connected"
                description="Nothing reports a live state yet. Connect a provider from Integrations."
              />
            </div>
          ) : null}
          {notConfigured > 0 ? (
            <p className="border-t border-border px-4 py-2 text-micro text-ink-muted">
              {notConfigured} further integration{notConfigured === 1 ? " is" : "s are"} not
              configured and report no state.
            </p>
          ) : null}
        </>
      )}
    </Panel>
  );
}
