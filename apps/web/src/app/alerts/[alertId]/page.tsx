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
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import {
  AlertChain,
  AlertTimeline,
  LabelChips,
  MappingBadge,
  PriorityBadge,
  SeverityBadge,
  SilenceBadge,
  StatusPill,
} from "@/components/alerting/primitives";
import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  MAPPING_EXPLANATIONS,
  formatAge,
  type AlertDetail,
  type AlertEvent,
  type SilenceRequest,
} from "@/lib/alerting";
import type { StatusTone } from "@/lib/design/status";

function SilenceRow({ silence }: { silence: SilenceRequest }) {
  return (
    <li className="flex flex-wrap items-center gap-3 px-7 py-4">
      <SilenceBadge state={silence.state} />
      <span className="min-w-0 flex-1 text-caption text-ink-secondary">
        {silence.reason_code}
        {silence.reason_note ? ` — ${silence.reason_note}` : ""}
      </span>
      {silence.state === "failed" && silence.error_code ? (
        <span className="font-mono text-micro text-critical">{silence.error_code}</span>
      ) : null}
      <time className="ml-auto font-mono text-micro text-ink-muted">
        {formatAge(silence.requested_at)}
      </time>
    </li>
  );
}

export default function AlertDetailPage() {
  const { alertId } = useParams<{ alertId: string }>();
  const [alert, retry] = useApi<AlertDetail>(`/v1/alerts/${alertId}`);
  const [events] = useApi<{ events: AlertEvent[] }>(`/v1/alerts/${alertId}/events`);

  return (
    <PageFrame>
      <LoadGate value={alert} retry={retry}>
        {(data) => {
          const chain: { key: string; label: string; done: boolean; tone: StatusTone; caption?: string }[] = [
            {
              key: "firing",
              label: "Firing",
              done: true,
              tone: data.status === "firing" ? "critical" : "success",
              caption: data.status === "firing" ? "still firing" : "resolved",
            },
            {
              key: "mapped",
              label: "Mapped",
              done: data.mapping_state === "mapped",
              tone: data.mapping_state === "mapped" ? "success" : "warning",
              caption: data.mapping_state === "mapped" ? data.service_key ?? undefined : "no match",
            },
            {
              key: "incident",
              label: "Incident",
              done: Boolean(data.incident),
              tone: data.incident
                ? data.incident.state === "resolved"
                  ? "success"
                  : "critical"
                : "neutral",
              caption: data.incident ? data.incident.state : "none opened",
            },
            {
              key: "notification",
              label: "Notifying",
              done: true,
              tone: data.silenced ? "neutral" : "success",
              caption: data.silenced ? "silenced" : "notifying",
            },
          ];

          return (
            <div className="space-y-6">
              <PageHeader
                title={data.alert_name}
                status={
                  <>
                    <StatusPill status={data.status} />
                    <SeverityBadge severity={data.severity} />
                    <PriorityBadge priority={data.priority} />
                  </>
                }
                meta={
                  <>
                    <Link href="/alerts" className="hover:text-ink">
                      Alerts
                    </Link>
                    <span className="font-mono">
                      {data.mapping_state === "mapped"
                        ? [data.project_key, data.environment_key, data.service_key]
                            .filter(Boolean)
                            .join("/")
                        : "not mapped to the catalog"}
                    </span>
                  </>
                }
              />

              <Panel className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]">
                <PanelHeader
                  title="Chain"
                  description="Where this alert stands right now, step by step. An absent step is an honest state, not a fault — a P3 with no incident and an unsilenced firing alert are both expected."
                />
                <AlertChain steps={chain} />
              </Panel>

              {data.mapping_state !== "mapped" ? (
                <Panel tone="warning">
                  <PanelHeader title="Catalog mapping" />
                  <div className="space-y-2" data-testid="mapping-explanation">
                    <MappingBadge state={data.mapping_state} />
                    <p className="text-body text-ink-secondary">
                      {MAPPING_EXPLANATIONS[data.mapping_error_code ?? ""] ??
                        "Drake could not resolve this alert into the catalog."}
                    </p>
                    <p className="text-caption text-ink-muted">
                      Drake kept the alert as integration evidence and opened no incident.
                      Filing it against a guessed project would send it to the wrong team.
                    </p>
                  </div>
                </Panel>
              ) : null}

              <div className="grid gap-6 md:grid-cols-2">
                <Panel className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:40ms]">
                  <PanelHeader title="What Alertmanager said" />
                  <dl className="divide-y divide-border">
                    <MetaRow label="Started">{formatAge(data.starts_at)}</MetaRow>
                    <MetaRow label="Ended">
                      {data.ends_at ? formatAge(data.ends_at) : "still firing"}
                    </MetaRow>
                    <MetaRow label="Last seen">{formatAge(data.last_seen_at)}</MetaRow>
                    {/* Provider time and Drake time, side by side and never
                        merged: a late delivery is late, not a late outage. */}
                    <MetaRow label="Received by Drake">{formatAge(data.ingested_at)}</MetaRow>
                    <MetaRow label="Firing episodes">{String(data.occurrence)}</MetaRow>
                  </dl>
                  {data.severity === "unknown" ? (
                    <p className="text-caption text-warning">
                      This alert carried a severity Drake does not recognise. It was
                      treated as P3 rather than guessed upward or downward.
                    </p>
                  ) : null}
                  <div>
                    <p className="mb-1.5 text-caption font-medium text-ink">Labels</p>
                    <LabelChips labels={data.labels} />
                  </div>
                  {Object.keys(data.annotations).length > 0 ? (
                    <div className="space-y-1 border-t border-border pt-3">
                      {Object.entries(data.annotations).map(([key, value]) => (
                        <p key={key} className="text-caption text-ink-secondary">
                          <span className="font-mono text-ink-muted">{key}:</span> {value}
                        </p>
                      ))}
                    </div>
                  ) : null}
                </Panel>

                <Panel className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:40ms]">
                  <PanelHeader title="Incident" />
                  {data.incident ? (
                    <div className="space-y-2" data-testid="alert-incident">
                      <Link
                        href={`/incidents/${data.incident.id}`}
                        className="text-body font-semibold text-ink hover:underline"
                      >
                        {data.incident.title}
                      </Link>
                      <dl className="divide-y divide-border">
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
                      </dl>
                      <p className="text-caption text-ink-muted">
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
                </Panel>
              </div>

              <Panel
                flush
                className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:80ms]"
              >
                <PanelHeader flush title="Notification suppression" />
                {data.silences.length === 0 ? (
                  <div className="px-7 py-8">
                    <DataState
                      kind="empty"
                      title="Not silenced"
                      description="Alertmanager is notifying normally for this alert."
                    />
                  </div>
                ) : (
                  <ul className="divide-y divide-border" data-testid="alert-silences">
                    {data.silences.map((silence) => (
                      <SilenceRow key={silence.id} silence={silence} />
                    ))}
                  </ul>
                )}
                <p className="border-t border-border px-7 py-4 text-micro text-ink-muted">
                  A silence suppresses Alertmanager notifications for a bounded time. It
                  does not acknowledge the incident, does not resolve it, does not delete
                  alert history, and does not make an SLO healthy.
                </p>
              </Panel>

              <Panel
                flush
                className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:100ms]"
              >
                <PanelHeader flush title="Timeline" />
                <div className="px-7 py-5">
                  {events.state === "loading" ? <DataState kind="loading" /> : null}
                  {events.state === "error" ? (
                    <DataState kind="error" description={events.message} />
                  ) : null}
                  {events.state === "ready" ? <AlertTimeline events={events.data.events} /> : null}
                </div>
              </Panel>
            </div>
          );
        }}
      </LoadGate>
    </PageFrame>
  );
}
