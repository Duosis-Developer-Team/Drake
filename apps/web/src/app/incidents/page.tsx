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
  Layers,
  Siren,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import {
  BreakdownRing,
  CardTitle,
  EmptyHero,
  FactPill,
  KpiTile,
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
  type IncidentPage,
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
      className="relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 px-7 py-4 transition-colors hover:bg-surface-hover md:grid-cols-[auto_minmax(0,1fr)_auto]"
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
              <time className="font-mono">{incident.acknowledged_at}</time>
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
            {t("row.opened")} <time className="font-mono">{incident.opened_at}</time>
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
    <Panel>
      <CardTitle icon={Layers} title={t("list.lifecycle.title")} />
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
    </Panel>
  );
}

function IncidentTable() {
  const t = useT("incidents");
  const params = useSearchParams();
  const [state, setState] = useState<IncidentState | "">(
    (params.get("state") as IncidentState) ?? "",
  );
  const [openedWithin, setOpenedWithin] = useState<OpenedWindow | "">("");
  const [severity, setSeverity] = useState<"critical" | "">("");

  const path = incidentListPath({
    state: state || undefined,
    severity: severity || undefined,
    openedWithin: openedWithin || undefined,
    projectId: params.get("project_id") ?? undefined,
    environmentId: params.get("environment_id") ?? undefined,
  });
  const [page, retry] = useApi<IncidentPage>(path);

  const items = page.state === "ready" ? page.data.items : [];
  const total = items.length;
  const openCount = items.filter((item) => item.state === "open").length;
  const ackCount = items.filter((item) => item.state === "acknowledged").length;
  const resolvedCount = items.filter(
    (item) => item.state === "resolved",
  ).length;

  const filters = (
    <Toolbar
      summary={
        page.state === "ready"
          ? t("list.shown", { shown: total, total: page.data.total })
          : undefined
      }
    >
      <PillSelect
        label={t("list.filter.state")}
        value={state}
        placeholder={t("list.filter.any")}
        options={INCIDENT_STATES.map((value) => ({
          value,
          label: t.dyn("state", value, STATE_LABELS[value]),
        }))}
        onChange={(value) => setState(value as IncidentState | "")}
      />
      <PillSelect
        label={t("list.filter.severity")}
        value={severity}
        placeholder={t("list.filter.any")}
        options={[{ value: "critical" as const, label: t("severity.critical") }]}
        onChange={(value) => setSeverity(value as "critical" | "")}
      />
      <PillSelect
        label={t("list.filter.openedWithin")}
        value={openedWithin}
        placeholder={t("list.filter.anyTime")}
        options={OPENED_WINDOWS.map((value) => ({
          value,
          label: t.dyn("window", value, value),
        }))}
        onChange={(value) => setOpenedWithin(value as OpenedWindow | "")}
      />
    </Toolbar>
  );

  return (
    <div className="flex flex-col gap-6">
      {page.state === "ready" ? (
        <div
          className="page-grid motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
          data-testid="incident-summary"
        >
          <KpiTile
            data-testid="incident-kpi-open"
            label={t("list.kpi.open")}
            value={openCount}
            icon={Siren}
            tone="critical"
            share={total > 0 ? openCount / total : null}
            caption={t("list.kpi.openCaption")}
          />
          <KpiTile
            data-testid="incident-kpi-acknowledged"
            label={t("list.kpi.acknowledged")}
            value={ackCount}
            icon={Eye}
            tone="warning"
            share={total > 0 ? ackCount / total : null}
            caption={t("list.kpi.acknowledgedCaption")}
          />
          <KpiTile
            data-testid="incident-kpi-resolved"
            label={t("list.kpi.resolved")}
            value={resolvedCount}
            icon={CheckCircle2}
            tone="success"
            share={total > 0 ? resolvedCount / total : null}
            caption={t("list.kpi.resolvedCaption")}
          />
          <KpiTile
            data-testid="incident-kpi-shown"
            label={t("list.kpi.shown")}
            value={total}
            icon={Inbox}
            tone="info"
            share={page.data.total > 0 ? total / page.data.total : null}
            caption={t("list.kpi.shownCaption", { total: page.data.total })}
          />
        </div>
      ) : null}

      {filters}

      <div className="page-split">
        <div className="page-main">
          <Panel
            flush
            className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
          >
            <CardTitle
              flush
              icon={Siren}
              title={t("list.queue.title")}
              description={t("list.queue.description")}
            />
            {page.state === "loading" ? (
              <StatePad>
                <DataState kind="loading" />
              </StatePad>
            ) : null}
            {page.state === "error" ? (
              <StatePad>
                {/* Permission denied and "request failed" are different answers
                  and get different screens. */}
                {page.notFound ? (
                  <DataState
                    kind="permission-denied"
                    description={t("list.forbidden")}
                  />
                ) : (
                  <DataState
                    kind="error"
                    description={page.message}
                    onRetry={retry}
                  />
                )}
              </StatePad>
            ) : null}
            {page.state === "ready" && total === 0 ? (
              <EmptyHero
                icon={Inbox}
                title={t("list.empty.title")}
                description={t("list.empty.description")}
              >
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
                    value: openedWithin
                      ? t.dyn("window", openedWithin, openedWithin)
                      : t("list.empty.anyTime"),
                  })}
                </FactPill>
              </EmptyHero>
            ) : null}
            {page.state === "ready" && total > 0 ? (
              <>
                <ul
                  className="divide-y divide-border"
                  data-testid="incident-table"
                >
                  {items.map((incident) => (
                    <IncidentRow key={incident.id} incident={incident} />
                  ))}
                </ul>
                <p className="border-t border-border px-7 py-4 text-micro text-ink-muted">
                  {t("list.footer", { shown: items.length, total: page.data.total })}
                </p>
              </>
            ) : null}
          </Panel>
        </div>

        <div className="page-aside">
          {page.state === "ready" ? (
            <Panel className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:80ms]">
              <CardTitle
                icon={Activity}
                title={t("list.byState.title")}
                description={t("list.byState.description")}
              />
              <BreakdownRing
                label={t("list.byState.ringLabel")}
                centerCaption={t("list.byState.center")}
                slices={[
                  { name: t("state.open"), value: openCount, tone: "critical" },
                  { name: t("state.acknowledged"), value: ackCount, tone: "warning" },
                  { name: t("state.resolved"), value: resolvedCount, tone: "success" },
                ]}
              />
            </Panel>
          ) : null}
          <LifecycleRule />
        </div>
      </div>
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
