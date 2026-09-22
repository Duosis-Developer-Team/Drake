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
  Link2Off,
  Siren,
  VolumeX,
  Zap,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import {
  MappingBadge,
  PriorityBadge,
  SeverityBadge,
  StatusPill,
} from "@/components/alerting/primitives";
import { useApi } from "@/components/catalog/primitives";
import {
  CardTitle,
  EmptyHero,
  FactPill,
  KpiTile,
  PillSelect,
  RowBubble,
  ShareBar,
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
  type Page,
  type Priority,
} from "@/lib/alerting";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";

const STATUSES: AlertStatus[] = ["firing", "resolved"];
const PRIORITIES: Priority[] = ["P1", "P2", "P3", "P4"];

/**
 * One alert. The whole row opens the alert; the incident link sits above the
 * row's stretched link so it stays its own target. Status, severity and
 * priority are chips; incident, notification and owner trail as three
 * separate facts, never collapsed into one tick (see the file header).
 */
function AlertRow({ alert }: { alert: AlertInstance }) {
  const t = useT("alerting");
  const fmt = useFormat();
  const firing = alert.status === "firing";
  return (
    <li
      data-testid={`alert-row-${alert.alert_name}`}
      className="relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 px-7 py-4 transition-colors hover:bg-surface-hover md:grid-cols-[auto_minmax(0,1fr)_auto]"
    >
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
          <span className="font-mono text-micro text-ink-muted">
            {alert.mapping_state === "mapped"
              ? [alert.project_key, alert.environment_key, alert.service_key]
                  .filter(Boolean)
                  .join("/")
              : t("row.noCatalogMatch")}
          </span>
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
        <div className="ml-2 w-24 text-right text-micro leading-4 text-ink-muted">
          <div className="font-medium text-ink-secondary">
            {fmt.relative(alert.last_seen_at)}
          </div>
          {/* Drake's own receipt time, kept visible so a late delivery is not
              mistaken for a late outage. */}
          <div>{t("row.received", { when: fmt.relative(alert.ingested_at) })}</div>
        </div>
      </div>
    </li>
  );
}

/** One priority lane in the side breakdown: label, bar, count. */
function PriorityLane({
  label,
  count,
  total,
  tone,
}: {
  label: string;
  count: number;
  total: number;
  tone: StatusTone;
}) {
  return (
    <li className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2 text-caption font-medium text-ink-secondary">
          <span
            aria-hidden
            className={`h-2 w-2 shrink-0 rounded-full ${toneSpec(tone).dot}`}
          />
          <span className="truncate">{label}</span>
        </span>
        <span data-tabular className="text-body font-semibold text-ink">
          {count}
        </span>
      </div>
      <ShareBar
        share={total > 0 ? count / total : null}
        tone={tone}
        className="h-2"
      />
    </li>
  );
}

function MiniStat({
  icon: Icon,
  label,
  count,
  tone,
  "data-testid": testId,
}: {
  icon: LucideIcon;
  label: string;
  count: number;
  tone: StatusTone;
  "data-testid": string;
}) {
  return (
    <div
      data-testid={testId}
      className="flex items-center gap-3 rounded-2xl border border-border bg-surface-2 px-4 py-3"
    >
      <span
        aria-hidden
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
          count > 0 ? toneSpec(tone).chip : "bg-surface-3 text-ink-muted"
        }`}
      >
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1 truncate text-caption text-ink-secondary">
        {label}
      </span>
      <span data-tabular className="text-title font-semibold text-ink">
        {count}
      </span>
    </div>
  );
}

function AlertsInner() {
  const t = useT("alerting");
  const [status, setStatus] = useState<string>("firing");
  const [priority, setPriority] = useState<string>("");
  const [summary] = useApi<AlertSummary>("/v1/alerts/summary");
  const [page, retry] = useApi<Page<AlertInstance>>(
    alertListPath({ status, priority }),
  );

  const firing = summary.state === "ready" ? summary.data.firing : 0;
  const shareOfFiring = (count: number) => (firing > 0 ? count / firing : null);
  const ofFiring = firing > 0 ? t("list.kpi.ofFiring", { count: firing }) : t("list.kpi.nothingFiring");

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
          <div
            className="page-grid motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
            data-testid="alerts-now"
          >
            <KpiTile
              data-testid="alerting-kpi-firing"
              label={t("list.kpi.firing")}
              value={summary.data.firing}
              icon={BellRing}
              tone="critical"
              share={firing > 0 ? summary.data.with_incident / firing : null}
              caption={t("list.kpi.firingCaption", { count: summary.data.with_incident })}
            />
            <KpiTile
              data-testid="alerting-kpi-p1"
              label={t("list.kpi.p1")}
              value={summary.data.p1}
              icon={Zap}
              tone="critical"
              share={shareOfFiring(summary.data.p1)}
              caption={ofFiring}
            />
            <KpiTile
              data-testid="alerting-kpi-p2"
              label={t("list.kpi.p2")}
              value={summary.data.p2}
              icon={Siren}
              tone="warning"
              share={shareOfFiring(summary.data.p2)}
              caption={ofFiring}
            />
            <KpiTile
              data-testid="alerting-kpi-unmapped"
              label={t("list.kpi.unmapped")}
              value={summary.data.unmapped}
              icon={Link2Off}
              tone="warning"
              share={shareOfFiring(summary.data.unmapped)}
              caption={t("list.kpi.unmappedCaption")}
            />
          </div>
        )}

        {/* Fixed vocabularies only. There is no free-text field here, and
            no way to type a matcher, a regex or a PromQL fragment. */}
        <Toolbar
          summary={
            page.state === "ready"
              ? t("list.shown", { shown: page.data.items.length, total: page.data.total })
              : undefined
          }
        >
          <PillSelect
            label={t("list.filter.status")}
            value={status}
            placeholder={t("list.filter.anyStatus")}
            options={STATUSES.map((value) => ({ value, label: t.dyn("status", value, value) }))}
            onChange={setStatus}
          />
          <PillSelect
            label={t("list.filter.priority")}
            value={priority}
            placeholder={t("list.filter.anyPriority")}
            options={PRIORITIES.map((value) => ({ value, label: PRIORITY_LABELS[value] }))}
            onChange={setPriority}
          />
        </Toolbar>

        <div className="page-split">
          <div className="page-main">
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
              {page.state === "loading" ? (
                <StatePad>
                  <DataState kind="loading" />
                </StatePad>
              ) : page.state === "error" ? (
                <StatePad>
                  {page.notFound ? (
                    <DataState kind="permission-denied" />
                  ) : (
                    <DataState
                      kind="error"
                      description={page.message}
                      onRetry={retry}
                    />
                  )}
                </StatePad>
              ) : page.data.items.length === 0 ? (
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
                  <ul className="divide-y divide-border">
                    {page.data.items.map((alert) => (
                      <AlertRow key={alert.id} alert={alert} />
                    ))}
                  </ul>
                  {page.data.items.some(
                    (alert) => alert.mapping_state !== "mapped",
                  ) ? (
                    <div
                      className="space-y-2 border-t border-border bg-surface-2/60 px-7 py-5"
                      data-testid="unmapped-note"
                    >
                      <p className="text-caption font-semibold text-ink">
                        {t("list.unmapped.title")}
                      </p>
                      {page.data.items
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
                </>
              )}
            </Panel>
          </div>

          {summary.state === "ready" ? (
            <div className="page-aside">
              <Panel className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:80ms]">
                <CardTitle
                  icon={Siren}
                  title={t("list.now.title")}
                  description={t("list.now.description")}
                />
                <div
                  data-testid="alert-summary"
                  className="flex flex-col gap-6"
                >
                  {/* Priority is ordered, so it reads top to bottom. "Other" is
                    what is firing outside P1/P2 — computed, never negative. */}
                  <ul
                    className="space-y-4"
                    aria-label={t("list.now.ringLabel")}
                  >
                    <PriorityLane
                      label={PRIORITY_LABELS.P1}
                      count={summary.data.p1}
                      total={firing}
                      tone="critical"
                    />
                    <PriorityLane
                      label={PRIORITY_LABELS.P2}
                      count={summary.data.p2}
                      total={firing}
                      tone="warning"
                    />
                    <PriorityLane
                      label={t("list.now.other")}
                      count={Math.max(
                        0,
                        firing - summary.data.p1 - summary.data.p2,
                      )}
                      total={firing}
                      tone="info"
                    />
                  </ul>
                  <div className="grid gap-2.5">
                    <MiniStat
                      data-testid="count-silenced"
                      icon={VolumeX}
                      label={t("list.now.silenced")}
                      count={summary.data.silenced}
                      tone="neutral"
                    />
                    <MiniStat
                      data-testid="count-unmapped"
                      icon={Link2Off}
                      label={t("list.now.unmapped")}
                      count={summary.data.unmapped}
                      tone="warning"
                    />
                    <MiniStat
                      data-testid="count-with-incident"
                      icon={Siren}
                      label={t("list.now.withIncident")}
                      count={summary.data.with_incident}
                      tone="info"
                    />
                  </div>
                </div>
              </Panel>
            </div>
          ) : null}
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
