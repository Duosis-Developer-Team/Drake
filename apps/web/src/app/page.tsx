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

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";

import { ClusterCard } from "@/components/clusters/ClusterCard";
import { AttentionQueue } from "@/components/command-center/AttentionQueue";
import { AppsBoard, PlatformPulse } from "@/components/command-center/Platform";
import { EvidenceCoverage } from "@/components/command-center/EvidenceCoverage";
import { IntegrationsBoard } from "@/components/command-center/IntegrationsBoard";
import { VerdictPanel } from "@/components/command-center/VerdictPanel";
import { CapacityRiskBoard } from "@/components/data-viz/CapacityRiskBoard";
import { HealthMatrix } from "@/components/data-viz/HealthMatrix";
import { OperationalTimeline } from "@/components/data-viz/OperationalTimeline";
import { SignalBoard } from "@/components/service-health/SignalBoard";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { Modal } from "@/components/ui/overlay";
import { FreshnessIndicator } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  NotConfiguredState,
} from "@/components/ui/states";
import { alertListPath, type AlertInstance, type AlertSummary, type Page } from "@/lib/alerting";
import type { CatalogContext, Cluster, IntegrationHealth } from "@/lib/catalog";
import { toneForHealth, toneSpec } from "@/lib/design/status";
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

  // The Overview's timeline is always the last 24 hours, the same window
  // its alert and deployment reads ask for.
  const timelineWindow = useMemo(() => {
    const end = Date.now();
    return { start: end - 24 * 3_600_000, end };
    // Re-anchored whenever the reads behind it refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentAlerts.data, recentDeployments.data, incidents.data]);
  const projectIds = useMemo(
    () => new Map((services.data?.items ?? []).map((row) => [row.project_key, row.project_id])),
    [services.data],
  );

  const timelineSection = (
    <Panel data-testid="correlation-timeline">
      <PanelHeader title={t("timeline.title")} description={t("timeline.description")} />
      {isNarrow ? (
        <TimelineSummary lanes={timelineLanes} onExpand={() => setTimelineDialogOpen(true)} />
      ) : (
        <OperationalTimeline lanes={timelineLanes} window={timelineWindow} />
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

  const attentionQueueSection = (
    <AttentionQueue
      items={attention}
      loading={anyLoading}
      projectIds={projectIds}
      checked={sources.map(({ key, label, resource }) => ({
        key,
        label,
        answered: Boolean(resource.data) && !resource.error,
      }))}
    />
  );

  const hasBound = boundIds(services.data?.items ?? []).length > 0;
  const signalSection = hasBound ? (
    <SignalBoard rows={services.data?.items ?? []} resource={signals} limit={6} />
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
              <Panel flush><IntegrationsBoard resource={integrations} /></Panel>
            </div>
          </>
        ) : (
          <>
            {/* What to act on leads, across the page: its cards sit two to
                a row, and the timeline below is what to read them against. */}
            {attentionQueueSection}
            {timelineSection}
            {/* Every service on one board: the map spans the page, the worst
                tiles three to a row. */}
            {signalSection}
            <div className="grid grid-cols-1 items-stretch gap-5 xl:grid-cols-2" data-testid="estate-overview">
              <Panel flush><FleetPanel resource={clusters} /></Panel>
              <Panel flush><IntegrationsBoard resource={integrations} /></Panel>
            </div>
            {/* Where each service lives, beside what may run out under it. */}
            <div className="grid grid-cols-1 items-stretch gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
              <div className="[&>*]:h-full">{healthMatrixSection}</div>
              <div className="[&>*]:h-full">{capacityRiskSection}</div>
            </div>
            {evidenceCoverageSection}
          </>
        )}
      </div>

      <Modal
        open={timelineDialogOpen}
        onClose={() => setTimelineDialogOpen(false)}
        title={t("timeline.title")}
      >
        <OperationalTimeline lanes={timelineLanes} window={timelineWindow} />
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
    <div data-testid="fleet-panel" className="flex h-full min-w-0 flex-col">
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
        <ul className="flex flex-1 flex-col gap-4 px-5 pb-5" aria-label={t("fleet.title")}>
          {clusters.map((cluster) => (
            <ClusterCard key={cluster.id} cluster={cluster} className="flex-1" />
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Integration health.
 *
 * An operational list, not a marketplace. Configured-and-failing sorts above
 * configured-and-fine, and not-configured sits at the bottom in muted type,
 * because "you have not connected this" is not a problem to be triaged.
 */
