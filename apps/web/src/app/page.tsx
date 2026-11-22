"use client";

/**
 * Command Center.
 *
 * One question, answered in ten seconds: where is the problem?
 *
 * The layout follows that and nothing else. One operational verdict built
 * from real counts, a ranked list of the actual things that are wrong, and
 * only then the standing inventory — fleet, services, integrations, catalog.
 * There is no hero, no row of five equal-sized KPI tiles (brief §9.3 forbids
 * it), and no chart that exists to fill space.
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

import { ArrowRight, Server } from "lucide-react";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";

import { RingProgress } from "@/components/charts/visuals";
import { AttentionQueue } from "@/components/command-center/AttentionQueue";
import { AppsBoard, PlatformPulse } from "@/components/command-center/Platform";
import { EvidenceCoverage } from "@/components/command-center/EvidenceCoverage";
import { VerdictPanel } from "@/components/command-center/VerdictPanel";
import { CapacityRiskBoard } from "@/components/data-viz/CapacityRiskBoard";
import { HealthMatrix } from "@/components/data-viz/HealthMatrix";
import { OperationalTimeline } from "@/components/data-viz/OperationalTimeline";
import { SignalBoard } from "@/components/service-health/SignalBoard";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge, StatusDot } from "@/components/ui/StatusBadge";
import { Modal } from "@/components/ui/overlay";
import { FreshnessIndicator, RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  NotConfiguredState,
} from "@/components/ui/states";
import { alertListPath, type AlertInstance, type AlertSummary, type Page } from "@/lib/alerting";
import type { CatalogContext, Cluster, IntegrationHealth } from "@/lib/catalog";
import { compareTone, humanize, toneForHealth, toneSpec } from "@/lib/design/status";
import { deploymentListPath, type DeploymentPage } from "@/lib/deployments";
import { useFormat, useT } from "@/lib/i18n";
import type { IncidentSummary } from "@/lib/incidents";
import {
  alertItems,
  clusterItems,
  incidentItems,
  integrationItems,
  serviceItems,
  signalItems,
  sortAttention,
  tallyByTone,
} from "@/lib/overview";
import { certificateRiskItems, pvcRiskItems } from "@/lib/view-models/capacity-risk";
import { buildHealthMatrix } from "@/lib/view-models/health-matrix";
import { buildTimeline, type TimelineLane } from "@/lib/view-models/timeline";
import { buildVerdict } from "@/lib/view-models/verdict";
import { useClusterInventorySummaries } from "@/lib/clusterSummaries";
import type { OverviewRange } from "@/lib/dashboards";
import { groupApps, usePlatformOverview, useProjectNames } from "@/lib/platform";
import type { InventorySummary } from "@/lib/inventory";
import { useMediaQuery } from "@/lib/useMediaQuery";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import { boundIds, rankSignals, signalsPath, type SignalPage } from "@/lib/signals";
import { resourceStatus, useResource, type Resource } from "@/lib/useResource";

const REFRESH_MS = 60_000;

export default function CommandCenterPage() {
  const t = useT("commandCenter");
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
  // What each bound service's own telemetry says (failed requests, error
  // logs). Additive like the timeline reads: it adds rows to the queue but
  // is not one of the verdict's sources, so a deployment without a
  // telemetry store loses nothing from its sources-answered count.
  const signals = useResource<SignalPage>(signalsPath(boundIds(services.data?.items ?? [])), {
    refreshMs: REFRESH_MS,
  });
  // Additive reads for the correlation timeline only — already-used
  // endpoints, not part of the verdict's own sources-answered accounting.
  const recentAlerts = useResource<Page<AlertInstance>>(alertListPath({ window: "24h" }), {
    refreshMs: REFRESH_MS,
  });
  const recentDeployments = useResource<DeploymentPage>(
    deploymentListPath({ startedWithin: "24h" }),
    { refreshMs: REFRESH_MS },
  );

  // The platform's own picture: one overview read over every bound
  // service, drawn the way an environment dashboard draws its own. Additive
  // like the timeline reads, never one of the verdict's sources.
  const [pulseRange, setPulseRange] = useState<OverviewRange>("24h");
  const platform = usePlatformOverview(services.data?.items ?? null, pulseRange);
  const projectNames = useProjectNames();
  const apps = useMemo(() => groupApps(services.data?.items ?? [], projectNames), [services.data, projectNames]);

  const sources = [
    { key: "incidents", label: t("source.incidents"), resource: incidents },
    { key: "alerts", label: t("source.alerts"), resource: alerts },
    { key: "clusters", label: t("source.clusters"), resource: clusters },
    { key: "services", label: t("source.services"), resource: services },
    { key: "integrations", label: t("source.integrations"), resource: integrations },
  ] as const;

  const anyLoading = sources.some(({ resource }) => resource.loading && !resource.data);
  const refreshing = sources.some(({ resource }) => resource.refreshing);

  const attention = sortAttention([
    ...(incidents.data ? incidentItems(incidents.data.items) : []),
    ...(alerts.data ? alertItems(alerts.data) : []),
    ...(clusters.data ? clusterItems(clusters.data.clusters) : []),
    ...(services.data ? serviceItems(services.data.items) : []),
    ...(services.data && signals.data
      ? signalItems(rankSignals(services.data.items, signals.data))
      : []),
    ...(integrations.data ? integrationItems(integrations.data.integrations) : []),
  ]);

  const timelineLanes = buildTimeline(
    incidents.data?.items ?? [],
    recentAlerts.data?.items ?? [],
    recentDeployments.data?.items ?? [],
  );

  const clusterList = clusters.data?.clusters ?? [];
  const clusterSummaries = useClusterInventorySummaries(clusterList);
  const resolvedSummaries = new Map(
    [...clusterSummaries]
      .filter(([, state]) => state.data !== null)
      .map(([id, state]) => [id, state.data as InventorySummary]),
  );
  const unassessedClusters = clusterList
    .filter((cluster) => {
      const state = clusterSummaries.get(cluster.id);
      return state && !state.loading && state.data === null;
    })
    .map((cluster) => cluster.display_name || cluster.cluster_ref);
  const capacityRiskItems = [
    ...certificateRiskItems(clusterList, resolvedSummaries),
    ...pvcRiskItems(clusterList, resolvedSummaries),
  ];

  const reloadAll = () => {
    sources.forEach(({ resource }) => resource.reload());
    recentAlerts.reload();
    recentDeployments.reload();
    signals.reload();
  };

  // Below 1024px the reading order becomes verdict → attention queue →
  // timeline summary (brief §9.4): a CSS `order` utility would move what a
  // reader SEES without moving what Tab reaches, so the DOM itself reorders
  // here instead, driven by an actual viewport match rather than a media
  // query the accessibility tree can't see.
  const isNarrow = useMediaQuery("(max-width: 1023px)");
  const [timelineDialogOpen, setTimelineDialogOpen] = useState(false);

  const timelineSection = (
    <Panel data-testid="correlation-timeline">
      <PanelHeader title={t("timeline.title")} description={t("timeline.description")} />
      {isNarrow ? (
        <TimelineSummary lanes={timelineLanes} onExpand={() => setTimelineDialogOpen(true)} />
      ) : (
        <OperationalTimeline lanes={timelineLanes} />
      )}
    </Panel>
  );

  const healthMatrixSection = (
    <Panel data-testid="health-matrix-panel">
      <PanelHeader title={t("matrix.title")} description={t("matrix.description")} />
      <HealthMatrix cells={buildHealthMatrix(services.data?.items ?? [])} status={resourceStatus(services)} />
    </Panel>
  );

  const capacityRiskSection = (
    <Panel flush data-testid="capacity-risk-panel">
      <PanelHeader flush title={t("capacity.title")} description={t("capacity.description")} />
      <CapacityRiskBoard items={capacityRiskItems} unassessedClusters={unassessedClusters} />
    </Panel>
  );

  const attentionQueueSection = <AttentionQueue items={attention} loading={anyLoading} />;

  const hasBound = boundIds(services.data?.items ?? []).length > 0;
  const signalSection = hasBound ? (
    <SignalBoard rows={services.data?.items ?? []} resource={signals} limit={5} />
  ) : null;

  const evidenceCoverageSection = (
    <Panel data-testid="evidence-coverage-panel">
      <PanelHeader title={t("evidence.title")} description={t("evidence.description")} />
      <EvidenceCoverage sources={sources} />
    </Panel>
  );

  const verdict = (
    <VerdictPanel
      verdict={buildVerdict(attention, sources)}
      onRefresh={reloadAll}
      refreshing={refreshing}
    />
  );

  const platformSection = (
    <>
      <PlatformPulse
        overview={platform.overview}
        failed={platform.failed}
        truncated={platform.truncated}
        deploys={recentDeployments.data?.items ?? []}
        range={pulseRange}
        onRange={setPulseRange}
        hasServices={boundIds(services.data?.items ?? []).length > 0}
      />
      <AppsBoard apps={apps} overview={platform.overview} />
    </>
  );

  const kpis = (
    <OverviewKpis
      services={services}
      incidents={incidents}
      alerts={alerts}
      deployments={recentDeployments}
    />
  );

  return (
    <PageFrame width="wide">
      <PageHeader
        title={t("page.title")}
        description={t("page.description")}
        meta={
          <>
            <FreshnessIndicator
              asOf={context.fetchedAt}
              state={context.error ? "unknown" : "fresh"}
            />
            <CatalogCounts resource={context} />
            {refreshing ? <span>{t("page.refreshing")}</span> : null}
          </>
        }
      />

      <div className="flex flex-col gap-5">
        {verdict}
        {platformSection}
        {kpis}
        {isNarrow ? (
          <>
            {attentionQueueSection}
            {timelineSection}
            {signalSection}
            {healthMatrixSection}
            {capacityRiskSection}
            {evidenceCoverageSection}
            <div className="flex flex-col gap-5" data-testid="estate-overview">
              <Panel flush><FleetPanel resource={clusters} /></Panel>
              <Panel flush><IntegrationsPanel resource={integrations} /></Panel>
            </div>
          </>
        ) : (
          <>
            {/* What to act on, beside where it lives. */}
            <div className="grid grid-cols-1 items-stretch gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
              <div className="[&>*]:h-full">{attentionQueueSection}</div>
              <div className="[&>*]:h-full">{healthMatrixSection}</div>
            </div>
            {timelineSection}
            {signalSection ? (
              <div className="grid grid-cols-1 items-stretch gap-5 xl:grid-cols-2">
                <div className="[&>*]:h-full">{signalSection}</div>
                <div className="[&>*]:h-full">{capacityRiskSection}</div>
              </div>
            ) : null}
            <div className="grid grid-cols-1 items-stretch gap-5 xl:grid-cols-2" data-testid="estate-overview">
              <Panel flush><FleetPanel resource={clusters} /></Panel>
              <Panel flush><IntegrationsPanel resource={integrations} /></Panel>
            </div>
            {/* Without a signal board, capacity risk pairs with the evidence
                behind the verdict instead of stretching across the page. */}
            {signalSection ? (
              evidenceCoverageSection
            ) : (
              <div className="grid grid-cols-1 items-stretch gap-5 xl:grid-cols-2">
                <div className="[&>*]:h-full">{capacityRiskSection}</div>
                <div className="[&>*]:h-full">{evidenceCoverageSection}</div>
              </div>
            )}
          </>
        )}
      </div>

      <Modal
        open={timelineDialogOpen}
        onClose={() => setTimelineDialogOpen(false)}
        title={t("timeline.title")}
      >
        <OperationalTimeline lanes={timelineLanes} />
      </Modal>
    </PageFrame>
  );
}

const KPI_TILE =
  "flex min-w-0 flex-col gap-1.5 rounded-panel border border-border bg-surface px-4 py-3.5 shadow-panel transition-colors hover:border-border-strong";

/** Four doors: each number opens the screen that explains it. */
function OverviewKpis({
  services,
  incidents,
  alerts,
  deployments,
}: {
  services: Resource<{ items: ServiceHealthRow[] }>;
  incidents: Resource<{ items: IncidentSummary[]; total: number }>;
  alerts: Resource<AlertSummary>;
  deployments: Resource<DeploymentPage>;
}) {
  const t = useT("commandCenter");
  const fmt = useFormat();
  const rows = services.data?.items ?? [];
  const tally = tallyByTone(rows, (row) => toneForHealth(row.health.status));
  const open = incidents.data?.items ?? [];
  const oldest = open.reduce<string | null>(
    (best, i) => (!best || i.opened_at < best ? i.opened_at : best),
    null,
  );
  const deploys = deployments.data?.items ?? [];
  const bad = deploys.filter((d) => d.rollout_state === "failed" || d.rollout_state === "degraded").length;
  const value = (v: number | null) => (
    <span data-tabular className="font-[family-name:var(--font-display)] text-[1.75rem] leading-none font-bold tracking-[-0.03em] text-ink">
      {v === null ? "—" : fmt.number(v)}
    </span>
  );
  const head = (label: string) => (
    <span className="flex items-center justify-between text-caption text-ink-muted">
      {label}
      <ArrowRight className="h-3.5 w-3.5 text-ink-muted" aria-hidden />
    </span>
  );
  const line = (children: ReactNode) => (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-muted">{children}</span>
  );
  const dot = (tone: Parameters<typeof toneSpec>[0]) => (
    <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${toneSpec(tone).dot}`} />
  );
  return (
    <section aria-label={t("kpi.label")} className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="overview-kpis">
      <Link href="/service-health" className={KPI_TILE}>
        {head(t("kpi.services"))}
        {value(services.data ? rows.length : null)}
        {line(
          services.data
            ? tally.map(({ tone, count }) => (
                <span key={tone} className="inline-flex items-center gap-1.5">
                  {dot(tone)}
                  {count} {t(`tone.${tone}`).toLocaleLowerCase()}
                </span>
              ))
            : t("kpi.unreadable"),
        )}
      </Link>
      <Link href="/incidents" className={KPI_TILE}>
        {head(t("kpi.openIncidents"))}
        {value(incidents.data ? incidents.data.total : null)}
        {line(
          !incidents.data ? (
            t("kpi.unreadable")
          ) : open.length > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-critical">
              {dot("critical")}
              {t("kpi.oldest", { age: fmt.relative(oldest) })}
            </span>
          ) : (
            t("kpi.noneOpen")
          ),
        )}
      </Link>
      <Link href="/alerts" className={KPI_TILE}>
        {head(t("kpi.firing"))}
        {value(alerts.data ? alerts.data.firing : null)}
        {line(
          alerts.data ? (
            <>
              <span className={`inline-flex items-center gap-1.5 ${alerts.data.p1 > 0 ? "text-critical" : ""}`}>
                {dot(alerts.data.p1 > 0 ? "critical" : alerts.data.p2 > 0 ? "warning" : "success")}
                {t("kpi.priorities", { p1: alerts.data.p1, p2: alerts.data.p2 })}
              </span>
              {alerts.data.silenced > 0 ? <span>{t("kpi.silenced", { count: alerts.data.silenced })}</span> : null}
            </>
          ) : (
            t("kpi.unreadable")
          ),
        )}
      </Link>
      <Link href="/deployments" className={KPI_TILE}>
        {head(t("kpi.deploys"))}
        {value(deployments.data ? deploys.length : null)}
        {line(
          !deployments.data ? (
            t("kpi.unreadable")
          ) : bad > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-critical">
              {dot("critical")}
              {t("kpi.deploysBad", { count: bad })}
            </span>
          ) : (
            t("kpi.deploysOk")
          ),
        )}
      </Link>
    </section>
  );
}

/** The catalog as a line under the title: what the reader is authorized to see. */
function CatalogCounts({ resource }: { resource: Resource<CatalogContext> }) {
  const t = useT("commandCenter");
  if (!resource.data) return null;
  const item = (label: string, count: number, href: string | null) => {
    const body = (
      <>
        {label} <b className="font-semibold text-ink-secondary">{count}</b>
      </>
    );
    return href ? (
      <Link href={href} className="hover:text-ink">
        {body}
      </Link>
    ) : (
      <span>{body}</span>
    );
  };
  return (
    <span data-testid="catalog-counts" className="inline-flex flex-wrap items-center gap-x-3">
      {item(t("catalog.projects"), resource.data.projects, "/projects")}
      {item(t("catalog.environments"), resource.data.environments, null)}
      {item(t("catalog.clusters"), resource.data.clusters, "/clusters")}
    </span>
  );
}

/**
 * The narrow-viewport stand-in for the full timeline track.
 *
 * Below 1024px the full multi-lane track competes too hard with the
 * attention queue above it for the one thing a phone screen has little of:
 * vertical space. This states the same facts in one line — how many events,
 * and which lanes Drake has no history for — and opens the real
 * `OperationalTimeline` in a dialog rather than losing it.
 */
function TimelineSummary({ lanes, onExpand }: { lanes: TimelineLane[]; onExpand: () => void }) {
  const t = useT("commandCenter");
  const events = lanes.flatMap((lane) => lane.events);
  const unavailable = lanes.filter((lane) => !lane.historyAvailable);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-caption text-ink-secondary">
        {t("timeline.summary", {
          events: events.length,
          available: lanes.length - unavailable.length,
          total: lanes.length,
        })}
        {unavailable.length > 0 ? (
          <span className="text-ink-muted">
            {" "}
            {t("timeline.summaryUnavailable", {
              lanes: unavailable.map((lane) => t.dyn("timeline.lane", lane.key, lane.label)).join(", "),
            })}
          </span>
        ) : null}
      </p>
      <button
        type="button"
        onClick={onExpand}
        data-testid="view-full-timeline"
        className="shrink-0 rounded-control border border-border px-2.5 py-1 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
      >
        {t("timeline.viewFull")}
      </button>
    </div>
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
  const t = useT("commandCenter");
  const clusters = resource.data?.clusters ?? [];
  return (
    <div data-testid="fleet-panel" className="flex min-w-0 flex-col">
      <PanelHeader
        flush
        title={t("fleet.title")}
        description={t("fleet.description")}
        actions={
          <Link
            href="/clusters"
            className="rounded text-caption font-medium text-brand hover:underline"
          >
            {t("fleet.all")}
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
        <div className="px-6 py-3">
          <NotConfiguredState compact title={t("fleet.empty")} />
        </div>
      ) : (
        <ul className="divide-y divide-border" data-tabular>
          {clusters.map((cluster) => (
            <li
              key={cluster.id}
              className="flex flex-wrap items-center gap-4 px-7 py-5 transition-colors hover:bg-surface-hover"
            >
              <span aria-hidden className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-surface-2 text-ink-secondary">
                <Server className="h-5 w-5" />
              </span>
              <div className="min-w-0 flex-1">
                <Link
                  href={`/clusters/${cluster.id}`}
                  className="font-semibold text-ink hover:text-brand"
                >
                  {cluster.display_name || cluster.cluster_ref}
                </Link>
                <span className="mt-0.5 block font-mono text-micro text-ink-muted">
                  {cluster.cluster_ref}
                </span>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-2 rounded-full bg-surface-2 py-1 pr-3 pl-3 text-micro">
                  <span className="text-ink-muted">{t("fleet.agent")}</span>
                  <StatusDot
                    status={toneForHealth(cluster.operational?.agent)}
                    label={t.dyn("token", cluster.operational?.agent ?? "unknown", humanize(cluster.operational?.agent))}
                  />
                  </span>
                  <span className="inline-flex items-center gap-2 rounded-full bg-surface-2 py-1 pr-3 pl-3 text-micro">
                  <span className="text-ink-muted">{t("fleet.inventory")}</span>
                  <StatusDot
                    status={toneForHealth(cluster.operational?.inventory)}
                    label={t.dyn("token", cluster.operational?.inventory ?? "unknown", humanize(cluster.operational?.inventory))}
                  />
                  </span>
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1.5">
                <FleetCounts cluster={cluster} />
                <span className="text-micro text-ink-muted">
                  <RelativeTime value={cluster.as_of} />
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
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
  const t = useT("commandCenter");
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
        {t("fleet.noSweep")}
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
      ).map(([key, rollup]) => {
        const label = t(`fleet.counts.${key}`);
        return (
        <span key={key} className="flex items-center gap-1.5">
          {/* The ring reads before the digits do; the digits stay exact. */}
          <RingProgress
            size={34}
            label={t("fleet.healthyRing", { what: label })}
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
        );
      })}
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
  const t = useT("commandCenter");
  const all = resource.data?.integrations ?? [];
  const configured = all.filter((entry) => entry.configuration_state === "configured");
  const notConfigured = all.length - configured.length;
  // Worst first, then by type — the shared severity order rather than an
  // alphabetical sort of the status words.
  const sorted = [...configured].sort(
    (a, b) =>
      compareTone(toneForHealth(a.observed_state), toneForHealth(b.observed_state)) ||
      a.integration_type.localeCompare(b.integration_type),
  );
  const stateOf = (entry: IntegrationHealth): string =>
    entry.configuration_state === "configured"
      ? t.dyn("token", entry.observed_state, humanize(entry.observed_state))
      : t("integrations.notConnected");

  return (
    <div data-testid="integrations-panel" className="flex min-w-0 flex-col">
      <PanelHeader
        flush
        title={t("integrations.title")}
        description={t("integrations.description")}
        actions={
          <Link
            href="/integrations"
            className="rounded text-caption font-medium text-brand hover:underline"
          >
            {t("integrations.manage")}
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
            <div className="border-b border-border px-7 py-6">
              <div className="flex items-end justify-between gap-4">
                <p>
                  <span data-tabular className="text-[2.5rem] leading-none font-semibold tracking-[-0.04em] text-ink">
                    {configured.length}
                  </span>
                  <span className="text-2xl font-semibold tracking-tight text-ink-muted">/{all.length}</span>
                  <span className="ml-2 text-caption text-ink-muted">{t("integrations.connected")}</span>
                </p>
                <span className="text-caption text-ink-muted">
                  <span data-tabular className="font-semibold text-ink">
                    {configured.filter((entry) => entry.observed_state === "ok").length}
                  </span>{" "}
                  {t("integrations.reportingOk")}
                </span>
              </div>
              {/* Every provider as an avatar: connected ones lit with their
                  state ring, the rest visibly dormant — not a grey pie. */}
              <ul className="mt-5 flex flex-wrap gap-3" aria-label={t("integrations.byState")}>
                {[...configured, ...all.filter((entry) => entry.configuration_state !== "configured")].map((entry) => {
                  const isConfigured = entry.configuration_state === "configured";
                  const entrySpec = toneSpec(isConfigured ? toneForHealth(entry.observed_state) : "not-applicable");
                  const name = humanize(entry.integration_type);
                  return (
                    <li
                      key={`${entry.integration_type}:${entry.scope.ref}`}
                      title={t("integrations.providerState", { name, state: stateOf(entry) })}
                      className="relative"
                    >
                      <span
                        className={`flex h-12 w-12 items-center justify-center rounded-full text-caption font-semibold ${
                          isConfigured
                            ? "bg-brand text-ink-inverse shadow-panel"
                            : "border border-dashed border-border bg-surface-2 text-ink-muted"
                        }`}
                      >
                        {name.slice(0, 2)}
                      </span>
                      <span
                        aria-hidden
                        className={`absolute -right-0.5 -bottom-0.5 h-3.5 w-3.5 rounded-full ring-2 ring-surface ${isConfigured ? entrySpec.dot : "bg-surface-3"}`}
                      />
                      <span className="sr-only">
                        {t("integrations.providerStateShort", { name, state: stateOf(entry) })}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
          <ul className="divide-y divide-border">
            {sorted.map((integration) => (
              <li
                key={`${integration.integration_type}:${integration.scope.ref}`}
                className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-7 py-4"
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
                    label={stateOf(integration)}
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
                title={t("integrations.emptyTitle")}
                description={t("integrations.emptyDescription")}
              />
            </div>
          ) : null}
          {notConfigured > 0 ? (
            <p className="border-t border-border px-4 py-2 text-micro text-ink-muted">
              {t("integrations.notConfigured", { count: notConfigured })}
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
