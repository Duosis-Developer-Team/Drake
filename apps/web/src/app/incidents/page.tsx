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
import { PageFrame } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import { IncidentStateBadge, ReasonLabel } from "@/components/incidents/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
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

const SELECT_CLASS =
  "rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-ink";

function IncidentRow({ incident }: { incident: IncidentSummary }) {
  return (
    <tr
      className="border-t border-border align-top"
      data-testid={`incident-row-${incident.service_key}`}
    >
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-1">
          <Link
            href={`/incidents/${incident.id}`}
            className="text-sm font-medium text-ink hover:underline"
          >
            {incident.title}
          </Link>
          <span className="font-mono text-[11px] text-ink-muted">
            {incident.project_key}/{incident.environment_key}/{incident.service_key}
          </span>
        </div>
      </td>
      <td className="py-2.5 pr-3">
        <StatusBadge status="critical" label={incident.severity} />
      </td>
      <td className="py-2.5 pr-3">
        <IncidentStateBadge state={incident.state} />
        {incident.acknowledged_at ? (
          <span className="mt-1 block text-[11px] text-ink-muted">
            acknowledged <time className="font-mono">{incident.acknowledged_at}</time>
          </span>
        ) : null}
      </td>
      <td className="py-2.5 pr-3">
        <span className="font-mono text-[11px] text-ink-secondary">
          {incident.binding.cluster_ref}/{incident.binding.namespace}/
          {incident.binding.workload_name}
        </span>
      </td>
      <td className="py-2.5 pr-3 text-xs text-ink-secondary">
        <ReasonLabel reason={incident.primary_reason} />
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap text-xs text-ink-secondary">
        <time className="font-mono">{incident.opened_at}</time>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap text-xs text-ink-secondary">
        {formatDuration(incident.opened_at, incident.resolved_at)}
      </td>
      <td className="py-2.5">
        {incident.current_health ? (
          <HealthBadge status={incident.current_health.status} />
        ) : (
          <span className="text-xs italic text-ink-muted">unknown</span>
        )}
      </td>
    </tr>
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
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          State
          <select
            className={SELECT_CLASS}
            value={state}
            onChange={(event) => setState(event.target.value as IncidentState | "")}
          >
            <option value="">Any</option>
            {INCIDENT_STATES.map((value) => (
              <option key={value} value={value}>
                {STATE_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          Severity
          <select
            className={SELECT_CLASS}
            value={severity}
            onChange={(event) => setSeverity(event.target.value as "critical" | "")}
          >
            <option value="">Any</option>
            <option value="critical">critical</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          Opened within
          <select
            className={SELECT_CLASS}
            value={openedWithin}
            onChange={(event) => setOpenedWithin(event.target.value as OpenedWindow | "")}
          >
            <option value="">Any time</option>
            {OPENED_WINDOWS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
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
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-left" data-testid="incident-table">
              <thead>
                <tr className="text-caption text-ink-secondary">
                  <th className="pb-2 pr-3 font-medium">Incident</th>
                  <th className="pb-2 pr-3 font-medium">Severity</th>
                  <th className="pb-2 pr-3 font-medium">State</th>
                  <th className="pb-2 pr-3 font-medium">Workload</th>
                  <th className="pb-2 pr-3 font-medium">Primary reason</th>
                  <th className="pb-2 pr-3 font-medium">Opened</th>
                  <th className="pb-2 pr-3 font-medium">Duration</th>
                  <th className="pb-2 font-medium">Current health</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((incident) => (
                  <IncidentRow key={incident.id} incident={incident} />
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11px] text-ink-muted">
            Showing {page.data.items.length} of {page.data.total} incidents in your
            authorized scope.
          </p>
        </Card>
      ) : null}
    </div>
  );
}

export default function IncidentsPage() {
  return (
    <PageFrame>
      <div className="space-y-5">
      <div>
        <h1 className="text-title font-semibold text-ink">Incidents</h1>
        <p className="mt-1 max-w-3xl text-caption text-ink-secondary">
          Opened by Drake after two consecutive trustworthy critical evaluations, and closed
          automatically when the service reports healthy twice. A datasource outage never
          opens one.
        </p>
      </div>
      <Suspense fallback={<DataState kind="loading" />}>
        <IncidentTable />
      </Suspense>
      </div>
    </PageFrame>
  );
}
