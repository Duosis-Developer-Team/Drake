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

import { ArrowRight, Boxes, FolderKanban, Layers, RefreshCw, Server } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { RingProgress } from "@/components/charts/visuals";
import { AttentionQueue } from "@/components/command-center/AttentionQueue";
import { EvidenceCoverage } from "@/components/command-center/EvidenceCoverage";
import { VerdictPanel } from "@/components/command-center/VerdictPanel";
import { CapacityRiskBoard } from "@/components/data-viz/CapacityRiskBoard";
import { HealthMatrix } from "@/components/data-viz/HealthMatrix";
import { OperationalTimeline } from "@/components/data-viz/OperationalTimeline";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge, StatusDot } from "@/components/ui/StatusBadge";
import { Button } from "@/components/ui/controls";
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
import { useT } from "@/lib/i18n";
import type { IncidentSummary } from "@/lib/incidents";
import {
  alertItems,
  clusterItems,
  incidentItems,
  integrationItems,
  serviceItems,
  sortAttention,
  tallyByTone,
} from "@/lib/overview";
import { certificateRiskItems, pvcRiskItems } from "@/lib/view-models/capacity-risk";
import { buildHealthMatrix } from "@/lib/view-models/health-matrix";
import { buildTimeline, type TimelineLane } from "@/lib/view-models/timeline";
import { buildVerdict } from "@/lib/view-models/verdict";
import { useClusterInventorySummaries } from "@/lib/clusterSummaries";
import type { InventorySummary } from "@/lib/inventory";
import { useMediaQuery } from "@/lib/useMediaQuery";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import { resourceStatus, useResource, type Resource } from "@/lib/useResource";

const REFRESH_MS = 60_000;

export default function CommandCenterPage() {
  const t = useT("commandCenter");
  const common = useT("common");
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
  // Additive reads for the correlation timeline only — already-used
  // endpoints, not part of the verdict's own sources-answered accounting.
  const recentAlerts = useResource<Page<AlertInstance>>(alertListPath({ window: "24h" }), {
    refreshMs: REFRESH_MS,
  });
  const recentDeployments = useResource<DeploymentPage>(
    deploymentListPath({ startedWithin: "24h" }),
    { refreshMs: REFRESH_MS },
  );

  const sources = [
    { key: "incidents", label: t("source.incidents"), resource: incidents },
    { key: "alerts", label: t("source.alerts"), resource: alerts },
    { key: "clusters", label: t("source.clusters"), resource: clusters },
    { key: "services", label: t("source.services"), resource: services },
    { key: "integrations", label: t("source.integrations"), resource: integrations },
  ] as const;

  const anyLoading = sources.some(({ resource }) => resource.loading && !resource.data);
  const refreshing = sources.some(({ resource }) => resource.refreshing);
  const answered = sources.filter(({ resource }) => resource.data !== null);

  const attention = sortAttention([
    ...(incidents.data ? incidentItems(incidents.data.items) : []),
    ...(alerts.data ? alertItems(alerts.data) : []),
    ...(clusters.data ? clusterItems(clusters.data.clusters) : []),
    ...(services.data ? serviceItems(services.data.items) : []),
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
            <span>{t("page.sourcesAnswered", { answered: answered.length, total: sources.length })}</span>
            {refreshing ? <span>{t("page.refreshing")}</span> : null}
          </>
        }
        actions={
          <Button icon={RefreshCw} onClick={reloadAll} disabled={refreshing}>
            {common("action.refresh")}
          </Button>
        }
      />

      {isNarrow ? (
        <div className="flex flex-col gap-6">
          <div className="motion-safe:animate-[scale-in_360ms_var(--ease-entrance)_backwards]">
            {verdict}
          </div>
          <Reveal delay={80}>{attentionQueueSection}</Reveal>
          <Reveal delay={140}>{timelineSection}</Reveal>
          <Reveal delay={200}>{healthMatrixSection}</Reveal>
          <Reveal delay={260}>{capacityRiskSection}</Reveal>
          <Reveal delay={320}>{evidenceCoverageSection}</Reveal>
        </div>
      ) : (
        /* Wide, airy rows: the lead verdict beside two summary cards, then
           the working panels two-up at full height, never squeezed into a
           narrow side column. */
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2 2xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)]">
            <div className="h-full motion-safe:animate-[scale-in_360ms_var(--ease-entrance)_backwards] lg:col-span-2 2xl:col-span-1 [&>*]:h-full">
              {verdict}
            </div>
            <Reveal delay={80} className="[&>*]:h-full">
              <Panel flush>
                <ServiceHealthPanel resource={services} />
              </Panel>
            </Reveal>
            <Reveal delay={140} className="[&>*]:h-full">
              <Panel flush>
                <CatalogPanel resource={context} />
              </Panel>
            </Reveal>
          </div>
          <div className="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-2">
            <Reveal delay={200} className="[&>*]:h-full">{attentionQueueSection}</Reveal>
            <Reveal delay={260} className="[&>*]:h-full">{timelineSection}</Reveal>
          </div>
          <div className="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-2">
            <Reveal delay={320} className="[&>*]:h-full">{healthMatrixSection}</Reveal>
            <Reveal delay={380} className="[&>*]:h-full">{capacityRiskSection}</Reveal>
          </div>
          <div className="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-2">
            <Reveal delay={440} className="[&>*]:h-full">
              <Panel flush data-testid="estate-overview">
                <FleetPanel resource={clusters} />
              </Panel>
            </Reveal>
            <Reveal delay={500} className="[&>*]:h-full">
              <Panel flush>
                <IntegrationsPanel resource={integrations} />
              </Panel>
            </Reveal>
          </div>
          <Reveal delay={560}>{evidenceCoverageSection}</Reveal>
        </div>
      )}

      <Modal
        open={timelineDialogOpen}
        onClose={() => setTimelineDialogOpen(false)}
        title={t("timeline.title")}
      >
        <OperationalTimeline lanes={timelineLanes} />
      </Modal>

      {isNarrow ? (
        <div className="mt-6 flex flex-col gap-6" data-testid="estate-overview">
          <Panel flush><FleetPanel resource={clusters} /></Panel>
          <Panel flush><IntegrationsPanel resource={integrations} /></Panel>
          <Panel flush><CatalogPanel resource={context} /></Panel>
          <Panel flush><ServiceHealthPanel resource={services} /></Panel>
        </div>
      ) : null}
    </PageFrame>
  );
}

/**
 * A panel's entrance. One fade+rise pass, staggered by `delay` so a grid of
 * panels arrives as a cascade rather than a flat pop — never a loop, never
 * re-triggered on data refresh (this wraps the section once, not per render
 * of its contents). `backwards` holds each panel at its `from` frame until
 * its own delay elapses, so a later panel never flashes at full opacity
 * before its turn. `className` carries layout concerns (grid dividers) that
 * belong to the caller's arrangement, not to the entrance itself.
 */
function Reveal({
  delay = 0,
  className = "",
  children,
}: {
  delay?: number;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`min-w-0 motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] ${className}`}
      style={{ animationDelay: `${delay}ms` }}
    >
      {children}
    </div>
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

function CatalogPanel({ resource }: { resource: Resource<CatalogContext> }) {
  const t = useT("commandCenter");
  return (
    <div data-testid="catalog-counts" className="flex h-full min-w-0 flex-col gap-6 p-7">
      <PanelHeader title={t("catalog.title")} description={t("catalog.description")} />
      {resource.loading && !resource.data ? (
        <LoadingSkeleton rows={2} />
      ) : resource.denied ? (
        <DeniedState compact />
      ) : !resource.data ? (
        <ErrorState compact description={resource.error ?? undefined} onRetry={resource.reload} />
      ) : (
        /* A list of counts, not term/definition pairs: a <dl> whose children
           are links is both wrong markup and an axe violation. Drawn as the
           chain the estate actually is — projects hold environments, which
           run on clusters. */
        <ul className="relative my-auto grid grid-cols-3 gap-2">
          <span aria-hidden className="absolute top-[1.375rem] right-[16%] left-[16%] h-px bg-[repeating-linear-gradient(90deg,var(--border-strong)_0_4px,transparent_4px_8px)] opacity-60" />
          {(
            [
              [t("catalog.projects"), resource.data.projects, "/projects", FolderKanban],
              [t("catalog.environments"), resource.data.environments, null, Layers],
              [t("catalog.clusters"), resource.data.clusters, "/clusters", Boxes],
            ] as const
          ).map(([label, count, href, TileIcon]) => {
            const body = (
              <>
                <span aria-hidden className="relative mx-auto flex h-11 w-11 items-center justify-center rounded-full border border-border bg-surface text-ink shadow-panel transition-transform group-hover:scale-105">
                  <TileIcon className="h-[1.125rem] w-[1.125rem]" />
                </span>
                <span data-tabular className="mt-3 block text-center text-[1.75rem] leading-none font-semibold tracking-[-0.03em] text-ink">
                  {count}
                </span>
                <span className="mt-1 block text-center text-caption text-ink-muted">{label}</span>
              </>
            );
            return (
              <li key={label}>
                {href ? (
                  <Link href={href} className="group block rounded-[1rem] py-1 transition-colors hover:bg-surface-hover">
                    {body}
                  </Link>
                ) : (
                  <span className="group block py-1">{body}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ServiceHealthPanel({ resource }: { resource: Resource<{ items: ServiceHealthRow[] }> }) {
  const t = useT("commandCenter");
  const rows = resource.data?.items ?? [];
  const tally = tallyByTone(rows, (row) => toneForHealth(row.health.status));
  return (
    <div data-testid="service-health-rollup" className="flex h-full min-w-0 flex-col gap-6 p-7">
      <PanelHeader title={t("serviceHealth.title")} description={t("serviceHealth.description")} />
      {resource.loading && !resource.data ? (
        <LoadingSkeleton rows={2} />
      ) : resource.denied ? (
        <DeniedState compact />
      ) : !resource.data ? (
        <ErrorState compact description={resource.error ?? undefined} onRetry={resource.reload} />
      ) : rows.length === 0 ? (
        <NotConfiguredState
          compact
          title={t("serviceHealth.emptyTitle")}
          description={t("serviceHealth.emptyDescription")}
        />
      ) : (
        <>
          <div className="flex items-end justify-between gap-4">
            <p>
              <span data-tabular className="text-[2.5rem] leading-none font-semibold tracking-[-0.04em] text-ink">
                {rows.length}
              </span>
              <span className="ml-2 text-caption text-ink-muted">{t("serviceHealth.services", { count: rows.length })}</span>
            </p>
            <p className="text-right text-caption text-ink-muted">
              <span data-tabular className="font-semibold text-ink">
                {tally.find((entry) => entry.tone === "success")?.count ?? 0}
              </span>{" "}
              {t("serviceHealth.reportingHealthy")}
            </p>
          </div>
          {/* One tile per service, coloured by its own reported state: at
              this scale a count you can literally see beats a pie. */}
          <ul
            aria-label={t("serviceHealth.tilesLabel", {
              breakdown: tally.map((entry) => `${t(`tone.${entry.tone}`)} ${entry.count}`).join(", "),
            })}
            className="grid grid-cols-[repeat(auto-fill,minmax(1.75rem,1fr))] gap-1.5"
          >
            {/* Worst first — the shared severity order, not an alphabetical
                sort of whatever the status words happen to be in this locale. */}
            {[...rows]
              .sort((x, y) => compareTone(toneForHealth(x.health.status), toneForHealth(y.health.status)))
              .map((row) => {
                const rowTone = toneForHealth(row.health.status);
                const rowSpec = toneSpec(rowTone);
                const status = t(`tone.${rowTone}`);
                const name = row.display_name || row.service_key;
                return (
                  <li key={row.environment_service_id}>
                    <Link
                      href={`/service-health?project_id=${encodeURIComponent(row.project_id)}&environment_id=${encodeURIComponent(row.environment_id)}`}
                      title={t("serviceHealth.tileTitle", {
                        name,
                        project: row.project_key,
                        environment: row.environment_key,
                        status,
                      })}
                      aria-label={t("serviceHealth.tileLabel", { name, status })}
                      className={`block aspect-square rounded-[0.5rem] transition-transform hover:scale-110 ${rowSpec.chip}`}
                    />
                  </li>
                );
              })}
          </ul>
          <ul className="flex flex-wrap gap-2">
            {tally.map((entry) => (
              <li
                key={entry.tone}
                className="inline-flex items-center gap-2 rounded-full bg-surface-2 px-3 py-1.5 text-micro text-ink-secondary"
              >
                <span aria-hidden className={`h-2 w-2 rounded-full ${toneSpec(entry.tone).dot}`} />
                {t(`tone.${entry.tone}`)}
                <span data-tabular className="font-semibold text-ink">{entry.count}</span>
              </li>
            ))}
          </ul>
          <Link
            href="/service-health"
            className="mt-auto inline-flex items-center gap-1 self-start rounded-full border border-border px-3.5 py-2 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
          >
            {t("serviceHealth.open")}
            <ArrowRight className="h-3.5 w-3.5" aria-hidden />
          </Link>
        </>
      )}
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
