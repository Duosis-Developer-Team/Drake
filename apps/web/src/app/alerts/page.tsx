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
import { Suspense } from "react";
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



const FILTER_KEYS = ["status", "priority", "mapping_state"] as const;

function AlertsInner() {
  const t = useT("alerting");
  const [filters, update] = useUrlFilters(FILTER_KEYS);
  // Firing is the default view; "any" is an explicit choice in the URL.
  const status = filters.status === "any" ? "" : (STATUSES as readonly string[]).includes(filters.status) ? filters.status : "firing";
  const priority = (PRIORITIES as readonly string[]).includes(filters.priority) ? filters.priority : "";
  const mapping = filters.mapping_state === "unmapped" ? "unmapped" : "";
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
                  <ul className="divide-y divide-border">
                    {list.items.map((alert) => (
                      <AlertRow key={alert.id} alert={alert} />
                    ))}
                  </ul>
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
