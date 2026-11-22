"use client";

/**
 * Incident list.
 *
 * Filters are selects over a fixed vocabulary the API publishes; there is
 * no free-text search box, because there is no free-text filter behind it.
 * Loading, empty, permission-denied and error are four different screens,
 * since "you cannot see this" and "there is nothing here" are very
 * different things to tell an operator at 3am.
 */

import {
  Activity,
  CheckCircle2,
  Eye,
  HeartPulse,
  Inbox,
  Siren,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Fragment, Suspense, useMemo } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { CountTile, ListFooter, useCursorList, useTotal, useUrlFilters } from "@/components/ops/ListKit";
import {
  AppTag,
  BandCard,
  DayBars,
  Figure,
  Legend,
  ListDivider,
  RankBar,
  dayAxis,
  dayIndex,
  median,
  useProjectIndex,
  useSample,
  type ProjectRef,
} from "@/components/ops/Visuals";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import {
  CardTitle,
  EmptyHero,
  FactPill,
  PillSelect,
  RowBubble,
  StatePad,
  Toolbar,
} from "@/components/incidents/OpsKit";
import {
  IncidentStateBadge,
  ReasonLabel,
} from "@/components/incidents/primitives";
import { HealthBadge } from "@/components/service-health/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel } from "@/components/ui/Panel";
import type { StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import {
  INCIDENT_STATES,
  OPENED_WINDOWS,
  STATE_LABELS,
  durationSeconds,
  incidentListPath,
  type IncidentSource,
  type IncidentState,
  type IncidentSummary,
  type OpenedWindow,
} from "@/lib/incidents";

const STATE_VISUAL: Record<
  IncidentState,
  { icon: LucideIcon; tone: StatusTone }
> = {
  open: { icon: Siren, tone: "critical" },
  acknowledged: { icon: Eye, tone: "warning" },
  resolved: { icon: CheckCircle2, tone: "success" },
};

const RAIL: Record<IncidentState, string> = {
  open: "var(--status-critical)",
  acknowledged: "var(--status-warning)",
  resolved: "var(--status-success)",
};

const SOURCES: IncidentSource[] = ["service_health", "alert", "rule", "slo", "protection", "agent"];
const SOURCE_COLOUR: Record<IncidentSource | "other", string> = {
  service_health: "var(--series-1)",
  alert: "var(--series-4)",
  rule: "var(--series-3)",
  slo: "var(--series-2)",
  protection: "var(--series-5)",
  agent: "var(--series-6)",
  other: "var(--text-muted)",
};

function SourceChip({ source }: { source: IncidentSource | undefined }) {
  const t = useT("incidents");
  const key = source && SOURCES.includes(source) ? source : "other";
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2 py-px text-[11px] font-medium text-ink-secondary">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: SOURCE_COLOUR[key] }} />
      {t(`list.source.${key}`)}
    </span>
  );
}

/**
 * One incident. The leading bubble carries its lifecycle state; the title,
 * severity and state lead; workload and reason follow; current health,
 * opened time and duration sit on the right. The whole row opens it.
 */
function IncidentRow({ incident, projects }: { incident: IncidentSummary; projects: Map<string, ProjectRef> }) {
  const t = useT("incidents");
  const fmt = useFormat();
  const visual = STATE_VISUAL[incident.state];
  return (
    <li
      data-testid={`incident-row-${incident.service_key}`}
      className="relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 py-4 pr-5 pl-6 transition-colors hover:bg-surface-hover md:grid-cols-[auto_minmax(0,1fr)_auto]"
    >
      <span aria-hidden className="absolute inset-y-3 left-0 w-[3px] rounded-r-full" style={{ background: RAIL[incident.state] }} />
      <RowBubble icon={visual.icon} tone={visual.tone} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Link
            href={`/incidents/${incident.id}`}
            className="truncate text-body font-semibold text-ink after:absolute after:inset-0 after:content-['']"
          >
            {incident.title}
          </Link>
          <StatusBadge
            status="critical"
            label={t.dyn("severity", incident.severity, incident.severity)}
            size="compact"
          />
          <IncidentStateBadge state={incident.state} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <AppTag
            projectKey={incident.project_key}
            environmentKey={incident.environment_key}
            projects={projects}
            outside={t("row.outside")}
          />
          <SourceChip source={incident.source} />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-secondary">
          <span className="font-mono text-micro text-ink-muted">
            {incident.binding
              ? `${incident.binding.cluster_ref}/${incident.binding.namespace}/${incident.binding.workload_name}`
              : t("row.noWorkload")}
          </span>
          <span aria-hidden className="h-1 w-1 rounded-full bg-border-strong" />
          <span className="inline-flex items-center gap-1.5">
            <Activity aria-hidden className="h-3.5 w-3.5 text-ink-muted" />
            <ReasonLabel reason={incident.primary_reason} />
          </span>
          {incident.acknowledged_at ? (
            <span className="text-micro text-ink-muted">
              {t("row.acknowledged")}{" "}
              <time dateTime={incident.acknowledged_at} title={fmt.utc(incident.acknowledged_at)}>
                {fmt.relative(incident.acknowledged_at)}
              </time>
            </span>
          ) : null}
        </div>
      </div>
      <div className="col-span-2 flex flex-wrap items-center gap-4 md:col-span-1 md:justify-end">
        {incident.current_health ? (
          <HealthBadge status={incident.current_health.status} />
        ) : (
          <span className="text-caption text-ink-muted">{t("row.healthUnknown")}</span>
        )}
        <div className="text-right text-micro leading-4 text-ink-muted">
          <div className="font-semibold text-ink-secondary" data-tabular>
            {fmt.duration(durationSeconds(incident.opened_at, incident.resolved_at))}
          </div>
          <div>
            {t("row.opened")}{" "}
            <time dateTime={incident.opened_at} title={fmt.utc(incident.opened_at)}>
              {fmt.relative(incident.opened_at)}
            </time>
          </div>
        </div>
      </div>
    </li>
  );
}

/** The lifecycle rule the header used to spell out, as three small steps. */
function LifecycleRule() {
  const t = useT("incidents");
  const steps: {
    key: string;
    icon: LucideIcon;
    tone: StatusTone;
    title: string;
    detail: string;
  }[] = [
    {
      key: "opens",
      icon: Siren,
      tone: "critical",
      title: t("list.lifecycle.opens"),
      detail: t("list.lifecycle.opensDetail"),
    },
    {
      key: "acknowledged",
      icon: Eye,
      tone: "warning",
      title: t("list.lifecycle.acknowledged"),
      detail: t("list.lifecycle.acknowledgedDetail"),
    },
    {
      key: "closes",
      icon: HeartPulse,
      tone: "success",
      title: t("list.lifecycle.closes"),
      detail: t("list.lifecycle.closesDetail"),
    },
  ];
  return (
    <div className="space-y-4">
      <ol className="relative space-y-5">
        <span
          aria-hidden
          className="absolute top-5 bottom-5 left-[1.1875rem] w-px bg-border"
        />
        {steps.map((step) => {
          const Icon = step.icon;
          return (
            <li key={step.key} className="relative flex items-start gap-3.5">
              <RowBubble icon={Icon} tone={step.tone} />
              <div className="min-w-0 pt-0.5">
                <p className="text-caption font-semibold text-ink">
                  {step.title}
                </p>
                <p className="text-micro text-ink-muted">{step.detail}</p>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="rounded-2xl bg-surface-2 px-4 py-3 text-micro text-ink-secondary">
        {t("list.lifecycle.note")}
      </p>
    </div>
  );
}

/**
 * The last 30 days in one card: incidents opened per day by where the signal
 * came from, how quickly they were acknowledged and resolved (medians of the
 * window), and what is open right now, per app. Drawn from a bounded sample;
 * the tiles above keep the server's exact totals.
 */
function IncidentBand({
  scope,
  projects,
}: {
  scope: { projectId?: string; environmentId?: string };
  projects: Map<string, ProjectRef>;
}) {
  const t = useT("incidents");
  const fmt = useFormat();
  const window = useSample<IncidentSummary>(incidentListPath({ ...scope, openedWithin: "30d" }), { max: 200 });
  const open = useSample<IncidentSummary>(incidentListPath({ ...scope, state: "open" }), { max: 100 });
  const axis = useMemo(() => dayAxis(30), []);
  const counts = useMemo(() => {
    const days = axis.map(() => ({}) as Record<string, number>);
    for (const incident of window.items ?? []) {
      const i = dayIndex(axis, incident.opened_at);
      if (i < 0) continue;
      const key = incident.source && SOURCES.includes(incident.source) ? incident.source : "other";
      days[i][key] = (days[i][key] ?? 0) + 1;
    }
    return days;
  }, [axis, window.items]);
  const series = [...SOURCES, "other" as const]
    .filter((key) => counts.some((day) => day[key]))
    .map((key) => ({ key, label: t(`list.source.${key}`), colour: SOURCE_COLOUR[key] }));
  const items = window.items ?? [];
  const ack = median(
    items.flatMap((i) => (i.acknowledged_at ? [durationSeconds(i.opened_at, i.acknowledged_at) ?? 0] : [])),
  );
  const resolve = median(items.flatMap((i) => (i.resolved_at ? [durationSeconds(i.opened_at, i.resolved_at) ?? 0] : [])));
  const resolved = items.filter((i) => i.resolved_at).length;
  const byApp = new Map<string, { key: string; env: Set<string>; count: number }>();
  for (const incident of open.items ?? []) {
    const entry = byApp.get(incident.project_key) ?? { key: incident.project_key, env: new Set<string>(), count: 0 };
    entry.count += 1;
    entry.env.add(incident.environment_key);
    byApp.set(incident.project_key, entry);
  }
  const apps = [...byApp.values()].sort((a, b) => b.count - a.count);
  const peak = Math.max(1, ...apps.map((a) => a.count));

  return (
    <BandCard
      data-testid="incident-band"
      title={t("list.band.title")}
      note={window.items && !window.complete ? t("list.band.truncated", { count: items.length }) : t("list.band.note")}
    >
      {window.failed ? (
        <p className="text-caption text-ink-muted">{t("list.band.failed")}</p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-3">
            <DayBars axis={axis} series={series} counts={counts} label={t("list.band.note")} />
            <Legend items={series.map((s) => ({ label: s.label, colour: s.colour }))} />
          </div>
          <div className="flex min-w-0 flex-col gap-5 lg:border-l lg:border-border lg:pl-6">
            <div className="grid grid-cols-3 gap-4">
              <Figure
                label={t("list.band.mtta")}
                value={ack === null ? "—" : fmt.duration(ack, { compact: true })}
                note={ack === null ? t("list.band.noSample") : t("list.band.median")}
              />
              <Figure
                label={t("list.band.mttr")}
                value={resolve === null ? "—" : fmt.duration(resolve, { compact: true })}
                note={resolve === null ? t("list.band.noSample") : t("list.band.median")}
              />
              <Figure
                label={t("list.band.resolvedShare")}
                value={items.length ? `${fmt.number((resolved / items.length) * 100, { maximumFractionDigits: 0 })}%` : "—"}
                note={t("list.band.resolvedNote", { resolved, total: items.length })}
                tone={items.length && resolved === items.length ? "success" : undefined}
              />
            </div>
            <div>
              <p className="mb-2.5 text-[11px] font-medium text-ink-muted">{t("list.band.openByApp")}</p>
              {apps.length === 0 ? (
                <p className="text-caption text-ink-secondary">{open.items ? t("list.band.noneOpen") : "…"}</p>
              ) : (
                <ul className="flex flex-col gap-2.5">
                  {apps.slice(0, 4).map((app) => {
                    const project = projects.get(app.key);
                    return (
                      <RankBar
                        key={app.key}
                        value={app.count}
                        peak={peak}
                        colour="var(--status-critical)"
                        label={
                          <span className="inline-flex items-center gap-1.5">
                            {project ? <ProjectLogo projectId={project.id} name={project.name} size="sm" variant="mark" /> : null}
                            <span className="font-medium">{project?.name ?? app.key}</span>
                            <span className="font-mono text-[10.5px] text-ink-muted">{[...app.env].join(" · ")}</span>
                          </span>
                        }
                      />
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </BandCard>
  );
}

const FILTER_KEYS = ["state", "severity", "opened_within", "project_id", "environment_id"] as const;


function IncidentTable() {
  const t = useT("incidents");
  const [filters, update] = useUrlFilters(FILTER_KEYS);
  const state = (INCIDENT_STATES as readonly string[]).includes(filters.state) ? (filters.state as IncidentState) : "";
  const severity: "critical" | "" = filters.severity === "critical" ? "critical" : "";
  const openedWithin = (OPENED_WINDOWS as readonly string[]).includes(filters.opened_within)
    ? (filters.opened_within as OpenedWindow)
    : "";
  const scope = {
    severity: severity || undefined,
    openedWithin: openedWithin || undefined,
    projectId: filters.project_id || undefined,
    environmentId: filters.environment_id || undefined,
  };
  const [list, retry] = useCursorList<IncidentSummary>(incidentListPath({ ...scope, state: state || undefined }));
  // Server totals for each state under the same filters — not a tally of
  // whatever page is loaded.
  const countPath = (value?: IncidentState) => {
    const path = incidentListPath({ ...scope, state: value });
    return `${path}${path.includes("?") ? "&" : "?"}limit=1`;
  };
  const openTotal = useTotal(countPath("open"));
  const ackTotal = useTotal(countPath("acknowledged"));
  const resolvedTotal = useTotal(countPath("resolved"));
  const allTotal = useTotal(countPath());
  const toggleState = (value: IncidentState | "") => update({ state: state === value ? "" : value });
  const projects = useProjectIndex();

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4" data-testid="incident-summary">
        <CountTile
          data-testid="incident-kpi-open"
          label={t("list.kpi.open")}
          value={openTotal}
          tone="critical"
          active={state === "open"}
          onClick={() => toggleState("open")}
          caption={t("list.kpi.openCaption")}
        />
        <CountTile
          data-testid="incident-kpi-acknowledged"
          label={t("list.kpi.acknowledged")}
          value={ackTotal}
          tone="warning"
          active={state === "acknowledged"}
          onClick={() => toggleState("acknowledged")}
          caption={t("list.kpi.acknowledgedCaption")}
        />
        <CountTile
          data-testid="incident-kpi-resolved"
          label={t("list.kpi.resolved")}
          value={resolvedTotal}
          tone="success"
          active={state === "resolved"}
          onClick={() => toggleState("resolved")}
          caption={t("list.kpi.resolvedCaption")}
        />
        <CountTile
          data-testid="incident-kpi-all"
          label={t("list.kpi.all")}
          value={allTotal}
          tone="neutral"
          active={state === ""}
          onClick={() => update({ state: "" })}
          caption={t("list.kpi.allCaption")}
        />
      </div>

      <IncidentBand scope={{ projectId: scope.projectId, environmentId: scope.environmentId }} projects={projects} />

      <Toolbar summary={list.state === "ready" && list.total !== null ? t("list.shown", { shown: list.items.length, total: list.total }) : undefined}>
        <PillSelect
          label={t("list.filter.state")}
          value={state}
          placeholder={t("list.filter.any")}
          options={INCIDENT_STATES.map((value) => ({
            value,
            label: t.dyn("state", value, STATE_LABELS[value]),
          }))}
          onChange={(value) => update({ state: value })}
        />
        <PillSelect
          label={t("list.filter.severity")}
          value={severity}
          placeholder={t("list.filter.any")}
          options={[{ value: "critical" as const, label: t("severity.critical") }]}
          onChange={(value) => update({ severity: value })}
        />
        <PillSelect
          label={t("list.filter.openedWithin")}
          value={openedWithin}
          placeholder={t("list.filter.anyTime")}
          options={OPENED_WINDOWS.map((value) => ({
            value,
            label: t.dyn("window", value, value),
          }))}
          onChange={(value) => update({ opened_within: value })}
        />
      </Toolbar>

      <Panel flush>
        <CardTitle
          flush
          icon={Siren}
          title={t("list.queue.title")}
          description={t("list.queue.description")}
        />
        {list.state === "loading" ? (
          <StatePad>
            <DataState kind="loading" />
          </StatePad>
        ) : null}
        {list.state === "error" ? (
          <StatePad>
            {/* Permission denied and "request failed" are different answers
                and get different screens. */}
            {list.denied ? (
              <DataState kind="permission-denied" description={t("list.forbidden")} />
            ) : (
              <DataState kind="error" description={list.message} onRetry={retry} />
            )}
          </StatePad>
        ) : null}
        {list.state === "ready" && list.items.length === 0 ? (
          <EmptyHero icon={Inbox} title={t("list.empty.title")} description={t("list.empty.description")}>
            <FactPill>
              {t("list.empty.state", {
                value: state ? t.dyn("state", state, STATE_LABELS[state]) : t("list.empty.any"),
              })}
            </FactPill>
            <FactPill>
              {t("list.empty.severity", {
                value: severity ? t("severity.critical") : t("list.empty.any"),
              })}
            </FactPill>
            <FactPill>
              {t("list.empty.opened", {
                value: openedWithin ? t.dyn("window", openedWithin, openedWithin) : t("list.empty.anyTime"),
              })}
            </FactPill>
          </EmptyHero>
        ) : null}
        {list.state === "ready" && list.items.length > 0 ? (
          <>
            <ul className="divide-y divide-border" data-testid="incident-table">
              {list.items.map((incident, i) => {
                // Without a state filter, what is still going on leads and
                // what is over follows under its own heading.
                const active = incident.state !== "resolved";
                const previous = i > 0 ? list.items[i - 1].state !== "resolved" : null;
                const heading =
                  state === "" && active !== previous
                    ? active
                      ? t("list.section.active")
                      : t("list.section.resolved")
                    : null;
                return (
                  <Fragment key={incident.id}>
                    {heading ? <ListDivider label={heading} /> : null}
                    <IncidentRow incident={incident} projects={projects} />
                  </Fragment>
                );
              })}
            </ul>
            <ListFooter
              shown={list.items.length}
              summary={t("list.footer", { shown: list.items.length, total: list.total ?? list.items.length })}
              hasMore={list.hasMore}
              loadingMore={list.loadingMore}
              moreError={list.moreError}
              onMore={list.loadMore}
              moreLabel={t("list.more")}
              loadingLabel={t("list.loadingMore")}
              errorLabel={t("list.moreError")}
            />
          </>
        ) : null}
      </Panel>

      <details className="rounded-panel border border-border bg-surface px-5 py-3">
        <summary className="cursor-pointer text-caption font-medium text-ink-secondary">{t("list.lifecycle.title")}</summary>
        <div className="pt-4">
          <LifecycleRule />
        </div>
      </details>
    </div>
  );
}

export default function IncidentsPage() {
  const t = useT("incidents");
  return (
    <PageFrame>
      <PageHeader
        title={t("list.title")}
        description={t("list.description")}
      />
      <Suspense fallback={<DataState kind="loading" />}>
        <IncidentTable />
      </Suspense>
    </PageFrame>
  );
}
