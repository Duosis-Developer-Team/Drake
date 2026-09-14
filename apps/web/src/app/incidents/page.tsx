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

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import { IncidentStateBadge, ReasonLabel } from "@/components/incidents/primitives";
import { StackedBar } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { FilterBar, Select } from "@/components/ui/controls";
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
import { HealthBadge } from "@/components/service-health/primitives";
import { ToneAvatar } from "@/components/ui/StatusBadge";

/**
 * One incident, as a scannable card rather than a row squeezed into an
 * 8-column table. Severity carries the leading avatar's colour; state,
 * reason and current health read as a second line of chips rather than
 * three more grid columns competing with the title for width.
 */
function IncidentRow({ incident }: { incident: IncidentSummary }) {
  return (
    <li
      data-testid={`incident-row-${incident.service_key}`}
      className="flex flex-wrap items-start gap-4 px-6 py-5 transition-colors hover:bg-surface-hover"
    >
      <ToneAvatar status="critical" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/incidents/${incident.id}`}
            className="text-body font-semibold text-ink hover:underline"
          >
            {incident.title}
          </Link>
          <StatusBadge status="critical" label={incident.severity} size="compact" />
          <IncidentStateBadge state={incident.state} />
        </div>
        <span className="mt-1 block font-mono text-micro text-ink-muted">
          {incident.binding.cluster_ref}/{incident.binding.namespace}/
          {incident.binding.workload_name}
        </span>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-secondary">
          <span>
            <ReasonLabel reason={incident.primary_reason} />
          </span>
          {incident.acknowledged_at ? (
            <span className="text-ink-muted">
              acknowledged <time className="font-mono">{incident.acknowledged_at}</time>
            </span>
          ) : null}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5 text-right">
        {incident.current_health ? (
          <HealthBadge status={incident.current_health.status} />
        ) : (
          <span className="text-caption italic text-ink-muted">unknown</span>
        )}
        <span className="text-micro text-ink-muted">
          opened <time className="font-mono">{incident.opened_at}</time>
        </span>
        <span className="text-micro text-ink-muted">
          {formatDuration(incident.opened_at, incident.resolved_at)}
        </span>
      </div>
    </li>
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

  return (
    <div className="space-y-4">
      <div role="group" aria-label="Filters">
        <FilterBar>
          <Select
            label="State"
            value={state}
            placeholder="Any"
            options={INCIDENT_STATES.map((value) => ({ value, label: STATE_LABELS[value] }))}
            onChange={(value) => setState(value as IncidentState | "")}
          />
          <Select
            label="Severity"
            value={severity}
            placeholder="Any"
            options={[{ value: "critical" as const, label: "critical" }]}
            onChange={(value) => setSeverity(value as "critical" | "")}
          />
          <Select
            label="Opened within"
            value={openedWithin}
            placeholder="Any time"
            options={OPENED_WINDOWS.map((value) => ({ value, label: value }))}
            onChange={(value) => setOpenedWithin(value as OpenedWindow | "")}
          />
        </FilterBar>
      </div>

      {page.state === "loading" ? <DataState kind="loading" /> : null}

      {page.state === "error" ? (
        <Card>
          {/* Permission denied and "request failed" are different answers
              and get different screens. */}
          {page.notFound ? (
            <DataState
              kind="permission-denied"
              description="Your current scope does not include incidents."
            />
          ) : (
            <DataState kind="error" description={page.message} onRetry={retry} />
          )}
        </Card>
      ) : null}

      {page.state === "ready" && page.data.items.length === 0 ? (
        <Card>
          <DataState
            kind="empty"
            title="No incidents"
            description="Nothing matches these filters in your authorized scope."
          />
        </Card>
      ) : null}

      {page.state === "ready" && page.data.items.length > 0 ? (
        <Panel
          data-testid="incident-summary"
          className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
        >
          <PanelHeader
            title="In this view"
            description="Computed from the rows on this page — not the full authorized set."
          />
          <div className="flex flex-wrap items-center gap-6">
            {(
              [
                {
                  label: "Open",
                  className: "text-critical",
                  count: page.data.items.filter((item) => item.state === "open").length,
                },
                {
                  label: "Acknowledged",
                  className: "text-warning",
                  count: page.data.items.filter((item) => item.state === "acknowledged").length,
                },
                {
                  label: "Resolved",
                  className: "text-healthy",
                  count: page.data.items.filter((item) => item.state === "resolved").length,
                },
              ] as const
            ).map((entry) => (
              <div key={entry.label} className="flex items-baseline gap-2">
                <span data-tabular className={`text-metric font-semibold ${entry.className}`}>
                  {entry.count}
                </span>
                <span className="text-caption text-ink-muted">{entry.label}</span>
              </div>
            ))}
            <div className="min-w-48 flex-1">
              <StackedBar
                label="Incidents on this page by state"
                segments={[
                  {
                    name: "Open",
                    value: page.data.items.filter((item) => item.state === "open").length,
                    tone: "critical",
                  },
                  {
                    name: "Acknowledged",
                    value: page.data.items.filter((item) => item.state === "acknowledged").length,
                    tone: "warning",
                  },
                  {
                    name: "Resolved",
                    value: page.data.items.filter((item) => item.state === "resolved").length,
                    tone: "success",
                  },
                ]}
              />
            </div>
          </div>
        </Panel>
      ) : null}

      {page.state === "ready" && page.data.items.length > 0 ? (
        <Panel
          flush
          className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
        >
          <ul className="divide-y divide-border" data-testid="incident-table">
            {page.data.items.map((incident) => (
              <IncidentRow key={incident.id} incident={incident} />
            ))}
          </ul>
          <p className="border-t border-border px-6 py-4 text-micro text-ink-muted">
            Showing {page.data.items.length} of {page.data.total} incidents in your
            authorized scope.
          </p>
        </Panel>
      ) : null}
    </div>
  );
}

export default function IncidentsPage() {
  return (
    <PageFrame>
      <PageHeader
        title="Incidents"
        description="Opened by Drake after two consecutive trustworthy critical evaluations, and closed automatically when the service reports healthy twice. A datasource outage never opens one."
      />
      <div className="space-y-5">
        <Suspense fallback={<DataState kind="loading" />}>
          <IncidentTable />
        </Suspense>
      </div>
    </PageFrame>
  );
}
