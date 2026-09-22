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
  type AlertDetail,
  type AlertEvent,
  type SilenceRequest,
} from "@/lib/alerting";
import type { StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";

function SilenceRow({ silence }: { silence: SilenceRequest }) {
  const fmt = useFormat();
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
        {fmt.relative(silence.requested_at)}
      </time>
    </li>
  );
}

export default function AlertDetailPage() {
  const t = useT("alerting");
  const fmt = useFormat();
  const { alertId } = useParams<{ alertId: string }>();
  const [alert, retry] = useApi<AlertDetail>(`/v1/alerts/${alertId}`);
  const [events] = useApi<{ events: AlertEvent[] }>(`/v1/alerts/${alertId}/events`);

  return (
    <PageFrame>
      <LoadGate value={alert} retry={retry}>
        {(data) => {
          const incidentState = data.incident
            ? t.dyn("incidentState", data.incident.state, data.incident.state)
            : null;
          const chain: { key: string; label: string; done: boolean; tone: StatusTone; caption?: string }[] = [
            {
              key: "firing",
              label: t("detail.chain.firing"),
              done: true,
              tone: data.status === "firing" ? "critical" : "success",
              caption:
                data.status === "firing" ? t("detail.chain.stillFiring") : t("detail.chain.resolved"),
            },
            {
              key: "mapped",
              label: t("detail.chain.mapped"),
              done: data.mapping_state === "mapped",
              tone: data.mapping_state === "mapped" ? "success" : "warning",
              caption:
                data.mapping_state === "mapped"
                  ? data.service_key ?? undefined
                  : t("detail.chain.noMatch"),
            },
            {
              key: "incident",
              label: t("detail.chain.incident"),
              done: Boolean(data.incident),
              tone: data.incident
                ? data.incident.state === "resolved"
                  ? "success"
                  : "critical"
                : "neutral",
              caption: incidentState ?? t("detail.chain.noneOpened"),
            },
            {
              key: "notification",
              label: t("detail.chain.notifying"),
              done: true,
              tone: data.silenced ? "neutral" : "success",
              caption: data.silenced
                ? t("detail.chain.silenced")
                : t("detail.chain.notifyingCaption"),
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
                      {t("detail.back")}
                    </Link>
                    <span className="font-mono">
                      {data.mapping_state === "mapped"
                        ? [data.project_key, data.environment_key, data.service_key]
                            .filter(Boolean)
                            .join("/")
                        : t("detail.notMapped")}
                    </span>
                  </>
                }
              />

              <Panel className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]">
                <PanelHeader
                  title={t("detail.chain.title")}
                  description={t("detail.chain.description")}
                />
                <AlertChain steps={chain} />
              </Panel>

              {data.mapping_state !== "mapped" ? (
                <Panel tone="warning">
                  <PanelHeader title={t("detail.mapping.title")} />
                  <div className="space-y-2" data-testid="mapping-explanation">
                    <MappingBadge state={data.mapping_state} />
                    <p className="text-body text-ink-secondary">
                      {t.dyn(
                        "mappingExplanation",
                        data.mapping_error_code,
                        MAPPING_EXPLANATIONS[data.mapping_error_code ?? ""] ??
                          t("detail.mapping.fallback"),
                      )}
                    </p>
                    <p className="text-caption text-ink-muted">{t("detail.mapping.kept")}</p>
                  </div>
                </Panel>
              ) : null}

              <div className="grid gap-6 md:grid-cols-2">
                <Panel className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:40ms]">
                  <PanelHeader title={t("detail.said.title")} />
                  <dl className="divide-y divide-border">
                    <MetaRow label={t("detail.said.started")}>{fmt.relative(data.starts_at)}</MetaRow>
                    <MetaRow label={t("detail.said.ended")}>
                      {data.ends_at ? fmt.relative(data.ends_at) : t("detail.said.stillFiring")}
                    </MetaRow>
                    <MetaRow label={t("detail.said.lastSeen")}>{fmt.relative(data.last_seen_at)}</MetaRow>
                    {/* Provider time and Drake time, side by side and never
                        merged: a late delivery is late, not a late outage. */}
                    <MetaRow label={t("detail.said.received")}>{fmt.relative(data.ingested_at)}</MetaRow>
                    <MetaRow label={t("detail.said.episodes")}>{fmt.number(data.occurrence)}</MetaRow>
                  </dl>
                  {data.severity === "unknown" ? (
                    <p className="text-caption text-warning">{t("detail.said.unknownSeverity")}</p>
                  ) : null}
                  <div>
                    <p className="mb-1.5 text-caption font-medium text-ink">
                      {t("detail.said.labels")}
                    </p>
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
                  <PanelHeader title={t("detail.incident.title")} />
                  {data.incident ? (
                    <div className="space-y-2" data-testid="alert-incident">
                      <Link
                        href={`/incidents/${data.incident.id}`}
                        className="text-body font-semibold text-ink hover:underline"
                      >
                        {data.incident.title}
                      </Link>
                      <dl className="divide-y divide-border">
                        <MetaRow label={t("detail.incident.state")}>{incidentState}</MetaRow>
                        <MetaRow label={t("detail.incident.acknowledged")}>
                          {data.incident.acknowledged_at
                            ? fmt.relative(data.incident.acknowledged_at)
                            : t("detail.incident.notAcknowledged")}
                        </MetaRow>
                        <MetaRow label={t("detail.incident.assigned")}>
                          {data.incident.assigned_at
                            ? fmt.relative(data.incident.assigned_at)
                            : t("detail.incident.unassigned")}
                        </MetaRow>
                      </dl>
                      <p className="text-caption text-ink-muted">{t("detail.incident.note")}</p>
                    </div>
                  ) : (
                    <DataState
                      kind="empty"
                      title={t("detail.incident.emptyTitle")}
                      description={
                        data.priority === "P3" || data.priority === "P4"
                          ? t("detail.incident.lowPriority")
                          : t("detail.incident.none")
                      }
                    />
                  )}
                </Panel>
              </div>

              <Panel
                flush
                className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:80ms]"
              >
                <PanelHeader flush title={t("detail.silences.title")} />
                {data.silences.length === 0 ? (
                  <div className="px-7 py-8">
                    <DataState
                      kind="empty"
                      title={t("detail.silences.emptyTitle")}
                      description={t("detail.silences.emptyDescription")}
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
                  {t("detail.silences.note")}
                </p>
              </Panel>

              <Panel
                flush
                className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:100ms]"
              >
                <PanelHeader flush title={t("detail.timeline.title")} />
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
