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
import {
  INCIDENT_STATES,
  OPENED_WINDOWS,
  STATE_LABELS,
  formatDuration,
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
            label={incident.severity}
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
              acknowledged{" "}
              <time className="font-mono">{incident.acknowledged_at}</time>
            </span>
          ) : null}
        </div>
      </div>
      <div className="col-span-2 flex flex-wrap items-center gap-4 md:col-span-1 md:justify-end">
        {incident.current_health ? (
          <HealthBadge status={incident.current_health.status} />
        ) : (
          <span className="text-caption text-ink-muted">health unknown</span>
        )}
        <div className="text-right text-micro leading-4 text-ink-muted">
          <div className="font-semibold text-ink-secondary" data-tabular>
            {formatDuration(incident.opened_at, incident.resolved_at)}
          </div>
          <div>
            opened <time className="font-mono">{incident.opened_at}</time>
          </div>
        </div>
      </div>
    </li>
  );
}

/** The lifecycle rule the header used to spell out, as three small steps. */
function LifecycleRule() {
  const steps: {
    icon: LucideIcon;
    tone: StatusTone;
    title: string;
    detail: string;
  }[] = [
    {
      icon: Siren,
      tone: "critical",
      title: "Opens",
      detail: "after two consecutive trustworthy critical evaluations",
    },
    {
      icon: Eye,
      tone: "warning",
      title: "Acknowledged",
      detail: "someone is on it — monitoring continues",
    },
    {
      icon: HeartPulse,
      tone: "success",
      title: "Closes",
      detail: "on its own when the service reports healthy twice",
    },
  ];
  return (
    <Panel>
      <CardTitle icon={Layers} title="How incidents move" />
      <ol className="relative space-y-5">
        <span
          aria-hidden
          className="absolute top-5 bottom-5 left-[1.1875rem] w-px bg-border"
        />
        {steps.map((step) => {
          const Icon = step.icon;
          return (
            <li key={step.title} className="relative flex items-start gap-3.5">
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
        A datasource outage never opens an incident.
      </p>
    </Panel>
  );
}

function IncidentTable() {
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
          ? `${total} of ${page.data.total} shown`
          : undefined
      }
    >
      <PillSelect
        label="State"
        value={state}
        placeholder="Any"
        options={INCIDENT_STATES.map((value) => ({
          value,
          label: STATE_LABELS[value],
        }))}
        onChange={(value) => setState(value as IncidentState | "")}
      />
      <PillSelect
        label="Severity"
        value={severity}
        placeholder="Any"
        options={[{ value: "critical" as const, label: "critical" }]}
        onChange={(value) => setSeverity(value as "critical" | "")}
      />
      <PillSelect
        label="Opened within"
        value={openedWithin}
        placeholder="Any time"
        options={OPENED_WINDOWS.map((value) => ({ value, label: value }))}
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
            label="Open"
            value={openCount}
            icon={Siren}
            tone="critical"
            share={total > 0 ? openCount / total : null}
            caption="nobody has acknowledged yet"
          />
          <KpiTile
            data-testid="incident-kpi-acknowledged"
            label="Acknowledged"
            value={ackCount}
            icon={Eye}
            tone="warning"
            share={total > 0 ? ackCount / total : null}
            caption="someone is on it"
          />
          <KpiTile
            data-testid="incident-kpi-resolved"
            label="Resolved"
            value={resolvedCount}
            icon={CheckCircle2}
            tone="success"
            share={total > 0 ? resolvedCount / total : null}
            caption="closed on a real recovery"
          />
          <KpiTile
            data-testid="incident-kpi-shown"
            label="Shown"
            value={total}
            icon={Inbox}
            tone="info"
            share={page.data.total > 0 ? total / page.data.total : null}
            caption={`of ${page.data.total} in your authorized scope`}
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
              title="Incident queue"
              description="Worst first, as the processor recorded them"
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
                    description="Your current scope does not include incidents."
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
                title="No incidents"
                description="Nothing matches these filters in your authorized scope."
              >
                <FactPill>
                  State · {state ? STATE_LABELS[state] : "any"}
                </FactPill>
                <FactPill>Severity · {severity || "any"}</FactPill>
                <FactPill>Opened · {openedWithin || "any time"}</FactPill>
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
                  Showing {items.length} of {page.data.total} incidents in your
                  authorized scope.
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
                title="By state"
                description="Incidents on this page"
              />
              <BreakdownRing
                label="Incidents on this page by state"
                centerCaption="on this page"
                slices={[
                  { name: "Open", value: openCount, tone: "critical" },
                  { name: "Acknowledged", value: ackCount, tone: "warning" },
                  { name: "Resolved", value: resolvedCount, tone: "success" },
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
  return (
    <PageFrame>
      <PageHeader
        title="Incidents"
        description="Opened and closed by Drake from trustworthy health evaluations — never by a datasource outage."
      />
      <Suspense fallback={<DataState kind="loading" />}>
        <IncidentTable />
      </Suspense>
    </PageFrame>
  );
}
