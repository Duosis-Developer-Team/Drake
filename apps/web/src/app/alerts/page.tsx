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
import { PageFrame } from "@/components/shell/AppShell";

import {
  CountChip,
  MappingBadge,
  PriorityBadge,
  SeverityBadge,
  StatusPill,
} from "@/components/alerting/primitives";
import { useApi } from "@/components/catalog/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
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

const SELECT_CLASS =
  "rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-ink";

const STATUSES: AlertStatus[] = ["firing", "resolved"];
const PRIORITIES: Priority[] = ["P1", "P2", "P3", "P4"];

function AlertRow({ alert }: { alert: AlertInstance }) {
  return (
    <tr
      className="border-t border-border align-top"
      data-testid={`alert-row-${alert.alert_name}`}
    >
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-1">
          <Link
            href={`/alerts/${alert.id}`}
            className="text-sm font-medium text-ink hover:underline"
          >
            {alert.alert_name}
          </Link>
          <span className="font-mono text-[11px] text-ink-muted">
            {alert.mapping_state === "mapped"
              ? [alert.project_key, alert.environment_key, alert.service_key]
                  .filter(Boolean)
                  .join("/")
              : "no catalog match"}
          </span>
        </div>
      </td>
      <td className="py-2.5 pr-3">
        <StatusPill status={alert.status} />
      </td>
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-1">
          <SeverityBadge severity={alert.severity} />
          <PriorityBadge priority={alert.priority} />
        </div>
      </td>
      <td className="py-2.5 pr-3">
        {/* An alert without an incident is not a failure of anything: P3 and
            P4 are recorded and shown, and never page. */}
        {alert.incident ? (
          <Link
            href={`/incidents/${alert.incident.id}`}
            className="text-xs text-ink hover:underline"
          >
            {alert.incident.state}
            {alert.incident.acknowledged_at ? " · acknowledged" : ""}
          </Link>
        ) : (
          <span className="text-[11px] italic text-ink-muted">no incident</span>
        )}
      </td>
      <td className="py-2.5 pr-3">
        {alert.silenced ? (
          <StatusBadge status="maintenance" label="Silenced" />
        ) : (
          <span className="text-[11px] text-ink-muted">notifying</span>
        )}
      </td>
      <td className="py-2.5 pr-3 text-xs text-ink-secondary">
        <div>{alert.owner_team ?? "—"}</div>
        {alert.slo_key ? (
          <div className="font-mono text-[11px] text-ink-muted">{alert.slo_key}</div>
        ) : null}
      </td>
      <td className="py-2.5 text-xs text-ink-secondary">
        <div>{formatAge(alert.last_seen_at)}</div>
        {/* Drake's own receipt time, kept visible so a late delivery is not
            mistaken for a late outage. */}
        <div className="text-[11px] text-ink-muted">
          received {formatAge(alert.ingested_at)}
        </div>
      </td>
    </tr>
  );
}

function AlertsInner() {
  const [status, setStatus] = useState<string>("firing");
  const [priority, setPriority] = useState<string>("");
  const [summary] = useApi<AlertSummary>("/v1/alerts/summary");
  const [page, retry] = useApi<Page<AlertInstance>>(alertListPath({ status, priority }));

  return (
    <PageFrame>
      <div className="space-y-5">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-ink">Alerts</h1>
        <p className="text-sm text-ink-secondary">
          Alertmanager decides when a condition is true. Drake records what it decided,
          which service it belongs to, and what happened next.
        </p>
      </header>

      <Card title="Now">
        {summary.state === "loading" ? (
          <DataState kind="loading" />
        ) : summary.state === "error" ? (
          <DataState kind="error" description={summary.message} />
        ) : (
          <div className="flex flex-wrap gap-2.5" data-testid="alert-summary">
            <CountChip label="Firing" count={summary.data.firing} tone="critical" />
            <CountChip label="P1" count={summary.data.p1} tone="critical" />
            <CountChip label="P2" count={summary.data.p2} tone="warning" />
            <CountChip label="Silenced" count={summary.data.silenced} tone="maintenance" />
            <CountChip label="Unmapped" count={summary.data.unmapped} tone="warning" />
          </div>
        )}
      </Card>

      <Card title="Alerts">
        <div className="mb-3 flex flex-wrap gap-2">
          {/* Fixed vocabularies only. There is no free-text field here, and
              no way to type a matcher, a regex or a PromQL fragment. */}
          <select
            aria-label="Status"
            className={SELECT_CLASS}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">Any status</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <select
            aria-label="Priority"
            className={SELECT_CLASS}
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
          >
            <option value="">Any priority</option>
            {PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>

        {page.state === "loading" ? (
          <DataState kind="loading" />
        ) : page.state === "error" ? (
          page.notFound ? (
            <DataState kind="permission-denied" />
          ) : (
            <DataState kind="error" description={page.message} onRetry={retry} />
          )
        ) : page.data.items.length === 0 ? (
          <DataState
            kind="empty"
            title="No alerts match"
            description="Nothing in your scope matches these filters. This is not a claim that nothing is wrong — only that no alert reached Drake."
          />
        ) : (
          <>
            <div className="w-full min-w-0 max-w-full overflow-x-auto [contain:paint]">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-ink-muted">
                <tr>
                  <th className="pb-2 pr-3 font-medium">Alert</th>
                  <th className="pb-2 pr-3 font-medium">Status</th>
                  <th className="pb-2 pr-3 font-medium">Severity</th>
                  <th className="pb-2 pr-3 font-medium">Incident</th>
                  <th className="pb-2 pr-3 font-medium">Notification</th>
                  <th className="pb-2 pr-3 font-medium">Owner / SLO</th>
                  <th className="pb-2 font-medium">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((alert) => (
                  <AlertRow key={alert.id} alert={alert} />
                ))}
              </tbody>
            </table>
            </div>
            {page.data.items.some((alert) => alert.mapping_state !== "mapped") ? (
              <div className="mt-4 space-y-1.5" data-testid="unmapped-note">
                <p className="text-xs font-medium text-ink">Unmapped alerts</p>
                {page.data.items
                  .filter((alert) => alert.mapping_state !== "mapped")
                  .map((alert) => (
                    <div key={alert.id} className="flex items-center gap-2">
                      <MappingBadge state={alert.mapping_state} />
                      <span className="text-xs text-ink-secondary">
                        {MAPPING_EXPLANATIONS[alert.mapping_error_code ?? ""] ??
                          "Drake could not place this alert in the catalog, so it opened no incident."}
                      </span>
                    </div>
                  ))}
              </div>
            ) : null}
          </>
        )}
      </Card>
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
