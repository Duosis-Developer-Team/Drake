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
import { Suspense } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { CountTile, ListFooter, useCursorList, useTotal, useUrlFilters } from "@/components/ops/ListKit";
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

/**
 * One incident. The leading bubble carries its lifecycle state; the title,
 * severity and state lead; workload and reason follow; current health,
 * opened time and duration sit on the right. The whole row opens it.
 */
function IncidentRow({ incident }: { incident: IncidentSummary }) {
  const t = useT("incidents");
  const fmt = useFormat();
  const visual = STATE_VISUAL[incident.state];
  return (
    <li
      data-testid={`incident-row-${incident.service_key}`}
      className="relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 px-5 py-3.5 transition-colors hover:bg-surface-hover md:grid-cols-[auto_minmax(0,1fr)_auto]"
    >
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
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-secondary">
          <span className="font-mono text-micro text-ink-muted">
            {incident.binding.cluster_ref}/{incident.binding.namespace}/
            {incident.binding.workload_name}
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
              {list.items.map((incident) => (
                <IncidentRow key={incident.id} incident={incident} />
              ))}
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
