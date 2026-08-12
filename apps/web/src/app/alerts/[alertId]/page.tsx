"use client";

/**
 * Alert detail.
 *
 * The chain, in order, with the gaps visible:
 *
 *   alert → catalog mapping → incident → ownership → notification state
 *
 * What this screen never shows: the raw webhook body, an authorization
 * header, `generatorURL`, `externalURL`, an annotation URL, or a provider
 * exception. The API has no field for most of them, and the rest never
 * leave the server.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageFrame } from "@/components/shell/AppShell";

import {
  LabelChips,
  MappingBadge,
  PriorityBadge,
  SeverityBadge,
  SilenceBadge,
  StatusPill,
} from "@/components/alerting/primitives";
import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import {
  MAPPING_EXPLANATIONS,
  formatAge,
  type AlertDetail,
  type AlertEvent,
} from "@/lib/alerting";

const EVENT_LABELS: Record<string, string> = {
  firing: "Started firing",
  resolved: "Alertmanager reported resolved",
  reopened: "Started firing again",
  suppressed: "Suppressed",
  silenced: "Silenced",
  inhibited: "Inhibited by another alert",
};

export default function AlertDetailPage() {
  const { alertId } = useParams<{ alertId: string }>();
  const [alert, retry] = useApi<AlertDetail>(`/v1/alerts/${alertId}`);
  const [events] = useApi<{ events: AlertEvent[] }>(`/v1/alerts/${alertId}/events`);

  return (
    <PageFrame>
      <div className="space-y-5">
      <LoadGate value={alert} retry={retry}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h1 className="text-xl font-semibold text-ink">{data.alert_name}</h1>
                <p className="mt-1 font-mono text-xs text-ink-muted">
                  {data.mapping_state === "mapped"
                    ? [data.project_key, data.environment_key, data.service_key]
                        .filter(Boolean)
                        .join("/")
                    : "not mapped to the catalog"}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill status={data.status} />
                <SeverityBadge severity={data.severity} />
                <PriorityBadge priority={data.priority} />
              </div>
            </div>

            {data.mapping_state !== "mapped" ? (
              <Card title="Catalog mapping">
                <div className="space-y-2" data-testid="mapping-explanation">
                  <MappingBadge state={data.mapping_state} />
                  <p className="text-sm text-ink-secondary">
                    {MAPPING_EXPLANATIONS[data.mapping_error_code ?? ""] ??
                      "Drake could not resolve this alert into the catalog."}
                  </p>
                  <p className="text-xs text-ink-muted">
                    Drake kept the alert as integration evidence and opened no incident.
                    Filing it against a guessed project would send it to the wrong team.
                  </p>
                </div>
              </Card>
            ) : null}

            <div className="grid gap-5 md:grid-cols-2">
              <Card title="What Alertmanager said">
                <div className="space-y-2">
                  <MetaRow label="Started">{formatAge(data.starts_at)}</MetaRow>
                  <MetaRow label="Ended">
                    {data.ends_at ? formatAge(data.ends_at) : "still firing"}
                  </MetaRow>
                  <MetaRow label="Last seen">{formatAge(data.last_seen_at)}</MetaRow>
                  {/* Provider time and Drake time, side by side and never
                      merged: a late delivery is late, not a late outage. */}
                  <MetaRow label="Received by Drake">{formatAge(data.ingested_at)}</MetaRow>
                  <MetaRow label="Firing episodes">{String(data.occurrence)}</MetaRow>
                  {data.severity === "unknown" ? (
                    <p className="text-xs text-warning">
                      This alert carried a severity Drake does not recognise. It was
                      treated as P3 rather than guessed upward or downward.
                    </p>
                  ) : null}
                </div>
                <div className="mt-3">
                  <LabelChips labels={data.labels} />
                </div>
                {Object.keys(data.annotations).length > 0 ? (
                  <div className="mt-3 space-y-1">
                    {Object.entries(data.annotations).map(([key, value]) => (
                      <p key={key} className="text-xs text-ink-secondary">
                        <span className="font-mono text-ink-muted">{key}:</span> {value}
                      </p>
                    ))}
                  </div>
                ) : null}
              </Card>

              <Card title="Incident">
                {data.incident ? (
                  <div className="space-y-2" data-testid="alert-incident">
                    <Link
                      href={`/incidents/${data.incident.id}`}
                      className="text-sm font-medium text-ink hover:underline"
                    >
                      {data.incident.title}
                    </Link>
                    <MetaRow label="State">{data.incident.state}</MetaRow>
                    <MetaRow label="Acknowledged">
                      {data.incident.acknowledged_at
                        ? formatAge(data.incident.acknowledged_at)
                        : "not acknowledged"}
                    </MetaRow>
                    <MetaRow label="Assigned">
                      {data.incident.assigned_at
                        ? formatAge(data.incident.assigned_at)
                        : "unassigned"}
                    </MetaRow>
                    <p className="text-xs text-ink-muted">
                      Acknowledging says a human has seen this. It does not stop the
                      alert and does not close the incident.
                    </p>
                  </div>
                ) : (
                  <DataState
                    kind="empty"
                    title="No incident"
                    description={
                      data.priority === "P3" || data.priority === "P4"
                        ? "This priority is recorded and filterable, but does not page anyone."
                        : "No incident has been opened for this alert."
                    }
                  />
                )}
              </Card>
            </div>

            <Card title="Notification suppression">
              {data.silences.length === 0 ? (
                <DataState
                  kind="empty"
                  title="Not silenced"
                  description="Alertmanager is notifying normally for this alert."
                />
              ) : (
                <ul className="space-y-2" data-testid="alert-silences">
                  {data.silences.map((silence) => (
                    <li key={silence.id} className="flex flex-wrap items-center gap-2">
                      <SilenceBadge state={silence.state} />
                      <span className="text-xs text-ink-secondary">
                        {silence.reason_code}
                        {silence.reason_note ? ` — ${silence.reason_note}` : ""}
                      </span>
                      {silence.state === "failed" && silence.error_code ? (
                        <span className="font-mono text-[11px] text-critical">
                          {silence.error_code}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 text-xs text-ink-muted">
                A silence suppresses Alertmanager notifications for a bounded time. It
                does not acknowledge the incident, does not resolve it, does not delete
                alert history, and does not make an SLO healthy.
              </p>
            </Card>

            <Card title="Timeline">
              {events.state === "loading" ? (
                <DataState kind="loading" />
              ) : events.state === "error" ? (
                <DataState kind="error" description={events.message} />
              ) : events.data.events.length === 0 ? (
                <DataState kind="empty" title="No transitions recorded" />
              ) : (
                <ol className="space-y-2" data-testid="alert-timeline">
                  {events.data.events.map((event) => (
                    <li
                      key={`${event.source_event_at}-${event.event_type}-${event.occurrence}`}
                      className="flex flex-wrap items-baseline gap-2 text-xs"
                    >
                      <span className="font-medium text-ink">
                        {EVENT_LABELS[event.event_type] ?? event.event_type}
                      </span>
                      <span className="text-ink-secondary">
                        {formatAge(event.source_event_at)}
                      </span>
                      <span className="text-ink-muted">
                        episode {event.occurrence}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          </>
        )}
      </LoadGate>
      </div>
    </PageFrame>
  );
}
