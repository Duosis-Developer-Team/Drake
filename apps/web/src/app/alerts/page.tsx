"use client";

/**
 * Alerts.
 *
 * What Alertmanager decided, placed in Drake's catalog and joined to the
 * incident it produced. Drake does not re-evaluate the condition, re-group
 * anything, or decide whether the receiver should have been called — this
 * screen is the business context around a decision Alertmanager already
 * made.
 *
 * The row keeps three different facts apart: whether the alert is firing,
 * whether it warranted an incident, and whether anyone has suppressed the
 * notification. Collapsing them is how "somebody silenced it" comes to read
 * as "somebody handled it".
 */

import {
  BellOff,
  BellRing,
  CheckCircle2,
  Siren,
  VolumeX,
} from "lucide-react";
import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import {
  MappingBadge,
  PriorityBadge,
  SeverityBadge,
  StatusPill,
} from "@/components/alerting/primitives";
import { useApi } from "@/components/catalog/primitives";
import { CountTile, ListFooter, useCursorList, useUrlFilters } from "@/components/ops/ListKit";
import {
  AppTag,
  BandCard,
  APP_PALETTE,
  Chip,
  Figure,
  Legend,
  RankBar,
  ShareBar,
  SpanTimeline,
  median,
  useProjectIndex,
  useSample,
  type ProjectRef,
} from "@/components/ops/Visuals";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { durationSeconds } from "@/lib/incidents";
import {
  CardTitle,
  EmptyHero,
  FactPill,
  PillSelect,
  RowBubble,
  StatePad,
  Toolbar,
} from "@/components/incidents/OpsKit";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel } from "@/components/ui/Panel";
import {
  MAPPING_EXPLANATIONS,
  PRIORITY_LABELS,
  alertListPath,
  type AlertInstance,
  type AlertStatus,
  type AlertSummary,
  type Priority,
} from "@/lib/alerting";
import { useFormat, useT } from "@/lib/i18n";

const STATUSES: AlertStatus[] = ["firing", "resolved"];
const PRIORITIES: Priority[] = ["P1", "P2", "P3", "P4"];

/**
 * One alert. The whole row opens the alert; the incident link sits above the
 * row's stretched link so it stays its own target. Status, severity and
 * priority are chips; incident, notification and owner trail as three
 * separate facts, never collapsed into one tick (see the file header).
 */
const PRIORITY_COLOUR: Record<Priority, string> = {
  P1: "var(--status-critical)",
  P2: "var(--status-warning)",
  P3: "var(--status-info)",
  P4: "var(--text-muted)",
};

/** Labels worth reading in a row: where it fired, not what it is called. */
const NOISE_LABELS = new Set(["alertname", "severity", "priority", "cluster", "job", "endpoint", "service", "instance"]);

function LabelChips({ labels }: { labels: Record<string, string> }) {
  const shown = Object.entries(labels)
    // The scraping server's own label says nothing about where it fired.
    .filter(([key]) => !NOISE_LABELS.has(key) && !key.startsWith("prom"))
    .slice(0, 3);
  if (shown.length === 0) return null;
  return (
    <>
      {shown.map(([key, value]) => (
        <span
          key={key}
          title={`${key}=${value}`}
          className="inline-flex max-w-[16rem] items-center gap-1 truncate rounded-md border border-border bg-surface-2 px-1.5 py-px font-mono text-[10.5px]"
        >
          <span className="text-ink-muted">{key}</span>
          <span className="truncate text-ink-secondary">{value}</span>
        </span>
      ))}
    </>
  );
}

function AlertRow({ alert, projects }: { alert: AlertInstance; projects: Map<string, ProjectRef> }) {
  const t = useT("alerting");
  const fmt = useFormat();
  const firing = alert.status === "firing";
  const lasted = durationSeconds(alert.starts_at, alert.ends_at ?? alert.resolved_at);
  return (
    <li
      data-testid={`alert-row-${alert.alert_name}`}
      className="relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 py-4 pr-6 pl-7 transition-colors hover:bg-surface-hover md:grid-cols-[auto_minmax(0,1fr)_auto]"
    >
      <span aria-hidden className="absolute inset-y-3 left-0 w-[3px] rounded-r-full" style={{ background: PRIORITY_COLOUR[alert.priority] ?? "var(--text-muted)" }} />
      <RowBubble
        icon={firing ? BellRing : CheckCircle2}
        tone={firing ? "critical" : "success"}
      />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Link
            href={`/alerts/${alert.id}`}
            className="truncate text-body font-semibold text-ink after:absolute after:inset-0 after:content-['']"
          >
            {alert.alert_name}
          </Link>
          {alert.occurrence > 1 ? (
            <span
              title={t("row.occurrencesTitle", { count: alert.occurrence })}
              className="rounded-full bg-surface-3 px-1.5 py-px font-mono text-[10.5px] font-semibold text-ink-secondary"
            >
              {t("row.occurrences", { count: alert.occurrence })}
            </span>
          ) : null}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5">
          {alert.mapping_state === "mapped" ? (
            <AppTag
              projectKey={alert.project_key}
              environmentKey={alert.environment_key}
              projects={projects}
              outside={t("row.noCatalogMatch")}
            />
          ) : (
            <span className="text-micro text-ink-muted">{t("row.noCatalogMatch")}</span>
          )}
          {alert.service_key ? <span className="font-mono text-micro text-ink-muted">{alert.service_key}</span> : null}
          <LabelChips labels={alert.labels ?? {}} />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-secondary">
          {/* An alert without an incident is not a failure of anything: P3
              and P4 are recorded and shown, and never page. */}
          {alert.incident ? (
            <Link
              href={`/incidents/${alert.incident.id}`}
              className="relative z-10 inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-0.5 text-ink hover:bg-surface-3"
            >
              <Siren aria-hidden className="h-3.5 w-3.5 text-ink-muted" />
              {t.dyn("incidentState", alert.incident.state, alert.incident.state)}
              {alert.incident.acknowledged_at ? ` · ${t("row.acknowledged")}` : ""}
            </Link>
          ) : (
            <span className="text-ink-muted">{t("row.noIncident")}</span>
          )}
          <span aria-hidden className="h-1 w-1 rounded-full bg-border-strong" />
          {alert.silenced ? (
            <StatusBadge status="maintenance" label={t("row.silenced")} size="compact" />
          ) : (
            <span className="text-ink-muted">{t("row.notifying")}</span>
          )}
          {alert.owner_team ? (
            <>
              <span
                aria-hidden
                className="h-1 w-1 rounded-full bg-border-strong"
              />
              <span>{alert.owner_team}</span>
            </>
          ) : null}
          {alert.slo_key ? (
            <span className="font-mono text-micro text-ink-muted">
              {alert.slo_key}
            </span>
          ) : null}
        </div>
      </div>
      <div className="col-span-2 flex flex-wrap items-center gap-2 md:col-span-1 md:justify-end">
        <StatusPill status={alert.status} />
        <SeverityBadge severity={alert.severity} />
        <PriorityBadge priority={alert.priority} />
        <div className="ml-2 w-28 text-right text-micro leading-4 text-ink-muted">
          <div className="font-medium text-ink-secondary">
            {fmt.relative(alert.last_seen_at)}
          </div>
          {lasted !== null ? (
            <div data-tabular>{t("row.for", { duration: fmt.duration(lasted, { compact: true }) })}</div>
          ) : null}
          {/* Drake's own receipt time, kept visible so a late delivery is not
              mistaken for a late outage. */}
          <div>{t("row.received", { when: fmt.relative(alert.ingested_at) })}</div>
        </div>
      </div>
    </li>
  );
}

/**
 * The last 7 days in one card. Each alert sits on its app's lane from when
 * it started to when it ended (still-firing ones run to now, hatched),
 * coloured by priority — which app, how long and how often in one picture.
 * A quiet week reads as a few marks where they happened instead of one bar
 * in an empty chart. Beside it: the noisiest rules and where alerts came
 * from. Drawn from a bounded sample of the window — the tiles above keep the
 * server's exact counts of what is firing.
 */
function AlertBand({ projects }: { projects: Map<string, ProjectRef> }) {
  const t = useT("alerting");
  const fmt = useFormat();
  const sample = useSample<AlertInstance>(alertListPath({ window: "7d" }), { paging: "offset", max: 250 });
  const [now] = useState(() => Date.now());
  const from = now - 7 * 86_400_000;
  const items = useMemo(() => sample.items ?? [], [sample.items]);
  const endOf = (a: AlertInstance): number | null => {
    const end = a.ends_at ?? a.resolved_at;
    if (end) return Date.parse(end);
    // Resolved without an end time: a moment, not an open span.
    return a.status === "firing" ? null : Date.parse(a.starts_at);
  };
  const resolvedSeconds = items.flatMap((a) => {
    const end = a.ends_at ?? a.resolved_at;
    const seconds = end ? durationSeconds(a.starts_at, end) : null;
    return seconds === null ? [] : [seconds];
  });
  const lasted = median(resolvedSeconds);
  const firing = items.filter((a) => a.status === "firing").length;
  const toIncident = items.filter((a) => a.incident).length;

  const lanes = useMemo(() => {
    const byPlace = new Map<string, { project: string | null; env: string | null; alerts: AlertInstance[] }>();
    for (const alert of items) {
      const key = alert.project_key ? `${alert.project_key}/${alert.environment_key ?? ""}` : "";
      const lane = byPlace.get(key) ?? { project: alert.project_key, env: alert.environment_key, alerts: [] };
      lane.alerts.push(alert);
      byPlace.set(key, lane);
    }
    return [...byPlace.entries()].sort((a, b) => (a[0] === "" ? 1 : b[0] === "" ? -1 : b[1].alerts.length - a[1].alerts.length));
  }, [items]);
  const present = PRIORITIES.filter((p) => items.some((a) => a.priority === p));

  const rules = new Map<string, number>();
  const apps = new Map<string, number>();
  for (const alert of items) {
    rules.set(alert.alert_name, (rules.get(alert.alert_name) ?? 0) + 1);
    const key = alert.project_key ?? "";
    apps.set(key, (apps.get(key) ?? 0) + 1);
  }
  const topRules = [...rules.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
  const appShares = [...apps.entries()].sort((a, b) => b[1] - a[1]);
  const rulePeak = Math.max(1, ...topRules.map(([, n]) => n));

  return (
    <BandCard
      data-testid="alert-band"
      title={t("list.band.title")}
      note={sample.items && !sample.complete ? t("list.band.truncated", { count: items.length }) : t("list.band.note")}
    >
      {sample.failed ? (
        <p className="text-caption text-ink-muted">{t("list.band.failed")}</p>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-4">
            <div className="grid grid-cols-3 gap-4">
              <Figure label={t("list.band.started")} value={sample.items ? fmt.number(items.length) : "—"} note={t("list.band.startedNote")} />
              <Figure
                label={t("list.band.duration")}
                value={lasted === null ? "—" : fmt.duration(lasted, { compact: true })}
                note={
                  lasted === null && firing > 0
                    ? t("list.band.durationOpen", { count: firing })
                    : t("list.band.durationNote")
                }
              />
              <Figure
                label={t("list.band.toIncident")}
                value={items.length ? `${fmt.number((toIncident / items.length) * 100, { maximumFractionDigits: 0 })}%` : "—"}
                note={t("list.band.toIncidentNote", { count: toIncident, total: items.length })}
              />
            </div>
            {sample.items && items.length === 0 ? (
              <p className="rounded-control border border-dashed border-border px-4 py-5 text-center text-caption text-ink-muted">
                {t("list.band.none")}
              </p>
            ) : (
              <>
                <SpanTimeline
                  from={from}
                  to={now}
                  tickDays={1}
                  label={t("list.band.note")}
                  lanes={lanes.slice(0, 6).map(([key, lane]) => ({
                    key: key || "outside",
                    label: (
                      <AppTag
                        projectKey={lane.project}
                        environmentKey={lane.env}
                        projects={projects}
                        outside={t("list.band.outside")}
                      />
                    ),
                    meta: fmt.number(lane.alerts.length),
                    spans: lane.alerts.map((a) => ({
                      id: a.id,
                      start: Date.parse(a.starts_at),
                      end: endOf(a),
                      colour: PRIORITY_COLOUR[a.priority],
                      title: `${a.alert_name} · ${PRIORITY_LABELS[a.priority]}`,
                    })),
                  }))}
                />
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Legend items={present.map((p) => ({ label: PRIORITY_LABELS[p], colour: PRIORITY_COLOUR[p] }))} />
                  <span className="text-micro text-ink-muted">{t("list.band.hatched")}</span>
                </div>
              </>
            )}
          </div>
          <div className="grid min-w-0 gap-5 sm:grid-cols-2 xl:grid-cols-1 xl:border-l xl:border-border xl:pl-6">
            <div className="min-w-0">
              <p className="mb-2.5 text-[11px] font-medium text-ink-muted">{t("list.band.noisiest")}</p>
              {topRules.length === 0 ? (
                <p className="text-caption text-ink-secondary">{sample.items ? t("list.band.none") : "…"}</p>
              ) : (
                <ul className="flex flex-col gap-2.5">
                  {topRules.map(([name, n]) => (
                    <RankBar key={name} value={n} peak={rulePeak} colour="var(--series-4)" label={<span className="font-mono text-[12px]">{name}</span>} />
                  ))}
                </ul>
              )}
            </div>
            <div className="min-w-0">
              <p className="mb-2.5 text-[11px] font-medium text-ink-muted">{t("list.band.byApp")}</p>
              {appShares.length === 0 ? (
                <p className="text-caption text-ink-secondary">{sample.items ? t("list.band.none") : "…"}</p>
              ) : (
                <ShareBar
                  segments={appShares.map(([key, n], i) => {
                    const project = key ? projects.get(key) : undefined;
                    return {
                      key: key || "outside",
                      count: n,
                      colour: key ? APP_PALETTE[i % APP_PALETTE.length] : "var(--text-muted)",
                      label: (
                        <span className="inline-flex min-w-0 items-center gap-1.5">
                          {project ? <ProjectLogo projectId={project.id} name={project.name} size="sm" variant="mark" /> : null}
                          <span className={key ? "truncate font-medium" : "truncate text-ink-muted"}>
                            {key ? (project?.name ?? key) : t("list.band.outside")}
                          </span>
                        </span>
                      ),
                    };
                  })}
                />
              )}
            </div>
          </div>
        </div>
      )}
    </BandCard>
  );
}

/** Rows of the same rule, under one header that says how many and how many fire. */
function RuleGroups({ items, projects }: { items: AlertInstance[]; projects: Map<string, ProjectRef> }) {
  const t = useT("alerting");
  const groups = new Map<string, AlertInstance[]>();
  for (const alert of items) groups.set(alert.alert_name, [...(groups.get(alert.alert_name) ?? []), alert]);
  return (
    <div className="divide-y divide-border">
      {[...groups.entries()].map(([name, alerts]) => {
        const firing = alerts.filter((a) => a.status === "firing").length;
        return (
          <details key={name} open className="group/rule">
            <summary className="flex cursor-pointer list-none items-center gap-3 bg-surface-2/50 px-7 py-2.5 hover:bg-surface-hover">
              <span aria-hidden className="text-ink-muted transition-transform group-open/rule:rotate-90">›</span>
              <span className="font-mono text-caption font-semibold text-ink">{name}</span>
              <span className="text-micro text-ink-muted">{t("list.group.alerts", { count: alerts.length })}</span>
              {firing ? (
                <span className="rounded-full bg-critical-soft px-2 py-px text-[11px] font-semibold text-critical">
                  {t("list.group.firing", { count: firing })}
                </span>
              ) : null}
            </summary>
            <ul className="divide-y divide-border">
              {alerts.map((alert) => (
                <AlertRow key={alert.id} alert={alert} projects={projects} />
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
}

const FILTER_KEYS = ["status", "priority", "mapping_state", "group"] as const;

function AlertsInner() {
  const t = useT("alerting");
  const [filters, update] = useUrlFilters(FILTER_KEYS);
  // Firing is the default view; "any" is an explicit choice in the URL.
  const status = filters.status === "any" ? "" : (STATUSES as readonly string[]).includes(filters.status) ? filters.status : "firing";
  const priority = (PRIORITIES as readonly string[]).includes(filters.priority) ? filters.priority : "";
  const mapping = filters.mapping_state === "unmapped" ? "unmapped" : "";
  const grouped = filters.group === "rule";
  const projects = useProjectIndex();
  const [summary] = useApi<AlertSummary>("/v1/alerts/summary");
  const [list, retry] = useCursorList<AlertInstance>(
    alertListPath({ status, priority, mapping_state: mapping }),
    { paging: "offset" },
  );

  const firing = summary.state === "ready" ? summary.data.firing : 0;
  const ofFiring = firing > 0 ? t("list.kpi.ofFiring", { count: firing }) : t("list.kpi.nothingFiring");
  const firingOnly = status === "firing" && !priority && !mapping;
  const pick = (patch: { priority?: string; mapping_state?: string }) =>
    update({ status: "firing", priority: "", mapping_state: "", ...patch });

  return (
    <PageFrame>
      <PageHeader
        title={t("list.title")}
        description={t("list.description")}
      />
      <div className="flex flex-col gap-6">
        {summary.state === "loading" ? (
          <Panel data-testid="alerts-now">
            <DataState kind="loading" />
          </Panel>
        ) : summary.state === "error" ? (
          <Panel data-testid="alerts-now">
            <DataState kind="error" description={summary.message} />
          </Panel>
        ) : (
          <div data-testid="alerts-now" className="flex flex-col gap-2">
            <div data-testid="alert-summary" className="flex flex-col gap-2">
              <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                <CountTile
                  data-testid="alerting-kpi-firing"
                  label={t("list.kpi.firing")}
                  value={summary.data.firing}
                  tone="critical"
                  active={firingOnly}
                  onClick={() => pick({})}
                  caption={t("list.kpi.firingCaption", { count: summary.data.with_incident })}
                />
                <CountTile
                  data-testid="alerting-kpi-p1"
                  label={t("list.kpi.p1")}
                  value={summary.data.p1}
                  tone="critical"
                  active={priority === "P1"}
                  onClick={() => pick({ priority: priority === "P1" ? "" : "P1" })}
                  caption={ofFiring}
                />
                <CountTile
                  data-testid="alerting-kpi-p2"
                  label={t("list.kpi.p2")}
                  value={summary.data.p2}
                  tone="warning"
                  active={priority === "P2"}
                  onClick={() => pick({ priority: priority === "P2" ? "" : "P2" })}
                  caption={ofFiring}
                />
                <CountTile
                  data-testid="alerting-kpi-unmapped"
                  label={t("list.kpi.unmapped")}
                  value={summary.data.unmapped}
                  tone="warning"
                  active={mapping === "unmapped"}
                  onClick={() => pick({ mapping_state: mapping ? "" : "unmapped" })}
                  caption={t("list.kpi.unmappedCaption")}
                />
              </div>
              <p className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-caption text-ink-muted">
                <span data-testid="count-silenced" className="inline-flex items-center gap-1.5">
                  <VolumeX className="h-3.5 w-3.5" aria-hidden />
                  {t("list.now.silenced")} <b className="font-semibold text-ink">{summary.data.silenced}</b>
                </span>
                <span data-testid="count-with-incident" className="inline-flex items-center gap-1.5">
                  <Siren className="h-3.5 w-3.5" aria-hidden />
                  {t("list.now.withIncident")} <b className="font-semibold text-ink">{summary.data.with_incident}</b>
                </span>
                <span className="inline-flex items-center gap-1.5">
                  {t("list.now.other")}{" "}
                  <b className="font-semibold text-ink">{Math.max(0, firing - summary.data.p1 - summary.data.p2)}</b>
                </span>
              </p>
            </div>
          </div>
        )}

        <AlertBand projects={projects} />

        {/* Fixed vocabularies only. There is no free-text field here, and
            no way to type a matcher, a regex or a PromQL fragment. */}
        <Toolbar
          summary={
            list.state === "ready" && list.total !== null
              ? t("list.shown", { shown: list.items.length, total: list.total })
              : undefined
          }
        >
          <PillSelect
            label={t("list.filter.status")}
            value={status}
            placeholder={t("list.filter.anyStatus")}
            options={STATUSES.map((value) => ({ value, label: t.dyn("status", value, value) }))}
            onChange={(value) => update({ status: value || "any" })}
          />
          <PillSelect
            label={t("list.filter.priority")}
            value={priority}
            placeholder={t("list.filter.anyPriority")}
            options={PRIORITIES.map((value) => ({ value, label: PRIORITY_LABELS[value] }))}
            onChange={(value) => update({ priority: value })}
          />
          <Chip active={grouped} onClick={() => update({ group: grouped ? "" : "rule" })}>
            {t("list.group.byRule")}
          </Chip>
        </Toolbar>

        <div>
          <div>
            <Panel
              flush
              className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
            >
              <CardTitle
                flush
                icon={BellRing}
                title={
                  status === "resolved"
                    ? t("list.heading.resolved")
                    : status === "firing"
                      ? t("list.heading.firing")
                      : t("list.heading.all")
                }
                description={t("list.heading.description")}
              />
              {list.state === "loading" ? (
                <StatePad>
                  <DataState kind="loading" />
                </StatePad>
              ) : list.state === "error" ? (
                <StatePad>
                  {list.denied ? (
                    <DataState kind="permission-denied" />
                  ) : (
                    <DataState kind="error" description={list.message} onRetry={retry} />
                  )}
                </StatePad>
              ) : list.items.length === 0 ? (
                <EmptyHero
                  icon={BellOff}
                  title={t("list.empty.title")}
                  description={t("list.empty.description")}
                >
                  <FactPill>
                    {t("list.empty.status", {
                      value: status ? t.dyn("status", status, status) : t("list.empty.any"),
                    })}
                  </FactPill>
                  <FactPill>
                    {t("list.empty.priority", { value: priority || t("list.empty.any") })}
                  </FactPill>
                </EmptyHero>
              ) : (
                <>
                  {grouped ? (
                    <RuleGroups items={list.items} projects={projects} />
                  ) : (
                    <ul className="divide-y divide-border">
                      {list.items.map((alert) => (
                        <AlertRow key={alert.id} alert={alert} projects={projects} />
                      ))}
                    </ul>
                  )}
                  {list.items.some(
                    (alert) => alert.mapping_state !== "mapped",
                  ) ? (
                    <div
                      className="space-y-2 border-t border-border bg-surface-2/60 px-7 py-5"
                      data-testid="unmapped-note"
                    >
                      <p className="text-caption font-semibold text-ink">
                        {t("list.unmapped.title")}
                      </p>
                      {list.items
                        .filter((alert) => alert.mapping_state !== "mapped")
                        .map((alert) => (
                          <div
                            key={alert.id}
                            className="flex flex-wrap items-center gap-2"
                          >
                            <MappingBadge state={alert.mapping_state} />
                            <span className="text-caption text-ink-secondary">
                              {t.dyn(
                                "mappingExplanation",
                                alert.mapping_error_code,
                                MAPPING_EXPLANATIONS[alert.mapping_error_code ?? ""] ??
                                  t("list.unmapped.fallback"),
                              )}
                            </span>
                          </div>
                        ))}
                    </div>
                  ) : null}
                  <ListFooter
                    shown={list.items.length}
                    summary={t("list.shown", { shown: list.items.length, total: list.total ?? list.items.length })}
                    hasMore={list.hasMore}
                    loadingMore={list.loadingMore}
                    moreError={list.moreError}
                    onMore={list.loadMore}
                    moreLabel={t("list.more")}
                    loadingLabel={t("list.loadingMore")}
                    errorLabel={t("list.moreError")}
                  />
                </>
              )}
            </Panel>
          </div>

        </div>
      </div>
    </PageFrame>
  );
}

export default function AlertsPage() {
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <AlertsInner />
    </Suspense>
  );
}
