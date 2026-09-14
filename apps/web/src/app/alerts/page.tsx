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
 * The columns exist so three different facts stay separate: whether the
 * alert is firing, whether it warranted an incident, and whether anyone has
 * suppressed the notification. Collapsing them is how "somebody silenced
 * it" comes to read as "somebody handled it".
 */

import Link from "next/link";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import {
  CountChip,
  MappingBadge,
  PriorityBadge,
  SeverityBadge,
  StatusPill,
} from "@/components/alerting/primitives";
import { useApi } from "@/components/catalog/primitives";
import { StackedBar } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { FilterBar, Select } from "@/components/ui/controls";
import {
  MAPPING_EXPLANATIONS,
  alertListPath,
  formatAge,
  type AlertInstance,
  type AlertStatus,
  type AlertSummary,
  type Page,
  type Priority,
} from "@/lib/alerting";

const STATUSES: AlertStatus[] = ["firing", "resolved"];
const PRIORITIES: Priority[] = ["P1", "P2", "P3", "P4"];

/**
 * One alert, as a card: the alert name and where it maps leads, status/
 * severity/priority read as a chip row rather than three grid columns, and
 * the incident/notification/owner facts trail on the right — three separate
 * claims, never collapsed into one tick (see the file header).
 */
function AlertRow({ alert }: { alert: AlertInstance }) {
  return (
    <li
      data-testid={`alert-row-${alert.alert_name}`}
      className="flex flex-wrap items-start gap-4 px-6 py-5 transition-colors hover:bg-surface-hover"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/alerts/${alert.id}`}
            className="text-body font-semibold text-ink hover:underline"
          >
            {alert.alert_name}
          </Link>
          <StatusPill status={alert.status} />
          <SeverityBadge severity={alert.severity} />
          <PriorityBadge priority={alert.priority} />
        </div>
        <span className="mt-1 block font-mono text-micro text-ink-muted">
          {alert.mapping_state === "mapped"
            ? [alert.project_key, alert.environment_key, alert.service_key]
                .filter(Boolean)
                .join("/")
            : "no catalog match"}
        </span>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-secondary">
          {/* An alert without an incident is not a failure of anything: P3
              and P4 are recorded and shown, and never page. */}
          {alert.incident ? (
            <Link href={`/incidents/${alert.incident.id}`} className="text-ink hover:underline">
              {alert.incident.state}
              {alert.incident.acknowledged_at ? " · acknowledged" : ""}
            </Link>
          ) : (
            <span className="italic text-ink-muted">no incident</span>
          )}
          {alert.silenced ? (
            <StatusBadge status="maintenance" label="Silenced" size="compact" />
          ) : (
            <span className="text-ink-muted">notifying</span>
          )}
          {alert.owner_team ? <span>{alert.owner_team}</span> : null}
          {alert.slo_key ? <span className="font-mono text-micro">{alert.slo_key}</span> : null}
        </div>
      </div>
      <div className="shrink-0 text-right text-micro text-ink-muted">
        <div>{formatAge(alert.last_seen_at)}</div>
        {/* Drake's own receipt time, kept visible so a late delivery is not
            mistaken for a late outage. */}
        <div>received {formatAge(alert.ingested_at)}</div>
      </div>
    </li>
  );
}

function AlertsInner() {
  const [status, setStatus] = useState<string>("firing");
  const [priority, setPriority] = useState<string>("");
  const [summary] = useApi<AlertSummary>("/v1/alerts/summary");
  const [page, retry] = useApi<Page<AlertInstance>>(alertListPath({ status, priority }));

  return (
    <PageFrame>
      <PageHeader
        title="Alerts"
        description="Alertmanager decides when a condition is true. Drake records what it decided, which service it belongs to, and what happened next."
      />
      <div className="space-y-5">
        <Panel
          data-testid="alerts-now"
          className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
        >
          <PanelHeader title="Now" />
          {summary.state === "loading" ? (
            <DataState kind="loading" />
          ) : summary.state === "error" ? (
            <DataState kind="error" description={summary.message} />
          ) : (
            <div className="flex flex-wrap items-center gap-6" data-testid="alert-summary">
              <span className="flex items-baseline gap-2">
                <span data-tabular className="text-metric font-semibold text-critical">
                  {summary.data.firing}
                </span>
                <span className="text-caption text-ink-muted">firing</span>
              </span>
              <div className="min-w-48 flex-1">
                {/* Priority is ordered, so it reads left-to-right rather than
                    around a circle. "Other" is what is firing outside P1/P2 —
                    computed, not assumed, and never negative. */}
                <StackedBar
                  label="Firing alerts by priority"
                  segments={[
                    { name: "P1", value: summary.data.p1, tone: "critical" },
                    { name: "P2", value: summary.data.p2, tone: "warning" },
                    {
                      name: "Other priorities",
                      value: Math.max(0, summary.data.firing - summary.data.p1 - summary.data.p2),
                      tone: "info",
                    },
                  ]}
                />
                <div className="mt-2 flex flex-wrap gap-2">
                  <CountChip label="Silenced" count={summary.data.silenced} tone="maintenance" />
                  <CountChip label="Unmapped" count={summary.data.unmapped} tone="warning" />
                  <CountChip
                    label="With incident"
                    count={summary.data.with_incident}
                    tone="maintenance"
                  />
                </div>
              </div>
            </div>
          )}
        </Panel>

        <Panel
          flush
          className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
        >
          <div className="border-b border-border px-6 py-4">
            {/* Fixed vocabularies only. There is no free-text field here, and
                no way to type a matcher, a regex or a PromQL fragment. */}
            <FilterBar>
              <Select
                label="Status"
                value={status}
                placeholder="Any status"
                options={STATUSES.map((value) => ({ value, label: value }))}
                onChange={setStatus}
              />
              <Select
                label="Priority"
                value={priority}
                placeholder="Any priority"
                options={PRIORITIES.map((value) => ({ value, label: value }))}
                onChange={setPriority}
              />
            </FilterBar>
          </div>

          {page.state === "loading" ? (
            <div className="px-6 py-5">
              <DataState kind="loading" />
            </div>
          ) : page.state === "error" ? (
            <div className="px-6 py-3">
              {page.notFound ? (
                <DataState kind="permission-denied" />
              ) : (
                <DataState kind="error" description={page.message} onRetry={retry} />
              )}
            </div>
          ) : page.data.items.length === 0 ? (
            <div className="px-6 py-3">
              <DataState
                kind="empty"
                title="No alerts match"
                description="Nothing in your scope matches these filters. This is not a claim that nothing is wrong — only that no alert reached Drake."
              />
            </div>
          ) : (
            <>
              <ul className="divide-y divide-border">
                {page.data.items.map((alert) => (
                  <AlertRow key={alert.id} alert={alert} />
                ))}
              </ul>
              {page.data.items.some((alert) => alert.mapping_state !== "mapped") ? (
                <div
                  className="space-y-1.5 border-t border-border px-6 py-4"
                  data-testid="unmapped-note"
                >
                  <p className="text-caption font-medium text-ink">Unmapped alerts</p>
                  {page.data.items
                    .filter((alert) => alert.mapping_state !== "mapped")
                    .map((alert) => (
                      <div key={alert.id} className="flex items-center gap-2">
                        <MappingBadge state={alert.mapping_state} />
                        <span className="text-caption text-ink-secondary">
                          {MAPPING_EXPLANATIONS[alert.mapping_error_code ?? ""] ??
                            "Drake could not place this alert in the catalog, so it opened no incident."}
                        </span>
                      </div>
                    ))}
                </div>
              ) : null}
            </>
          )}
        </Panel>
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
