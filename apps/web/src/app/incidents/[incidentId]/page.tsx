"use client";

/**
 * Incident detail.
 *
 * Renders the backend's lifecycle: its state, the reasons it opened with,
 * and the immutable timeline. Acknowledge sends a version and nothing
 * else, so two responders pressing it at once produce one acknowledgement
 * and one clear message rather than a silent overwrite.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import {
  IncidentLifecycle,
  IncidentStateBadge,
  IncidentTimeline,
  ReasonList,
  SeverityBadge,
} from "@/components/incidents/primitives";
import { HealthBadge } from "@/components/service-health/primitives";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import {
  acknowledgeIncident,
  formatDuration,
  type IncidentDetail,
  type IncidentEvent,
} from "@/lib/incidents";

type Notice =
  | { kind: "none" }
  | { kind: "done"; message: string }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string };

export default function IncidentDetailPage() {
  const { incidentId } = useParams<{ incidentId: string }>();
  const [incident, retryIncident] = useApi<IncidentDetail>(`/v1/incidents/${incidentId}`);
  const [events, retryEvents] = useApi<{ events: IncidentEvent[] }>(
    `/v1/incidents/${incidentId}/events`,
  );
  const { state: session } = useSession();
  const csrfToken = session.status === "authenticated" ? session.me.csrf_token : null;
  const [notice, setNotice] = useState<Notice>({ kind: "none" });
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    retryIncident();
    retryEvents();
  }, [retryIncident, retryEvents]);

  const acknowledge = async (detail: IncidentDetail) => {
    if (!csrfToken) return;
    setBusy(true);
    setNotice({ kind: "none" });
    try {
      const result = await acknowledgeIncident(csrfToken, detail.id, detail.version);
      setNotice({
        kind: "done",
        message: result.changed
          ? "Acknowledged. Monitoring continues — this incident closes only on a real recovery."
          : "Already acknowledged.",
      });
      refresh();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setNotice({
          kind: "conflict",
          message:
            "This incident changed while you were looking at it — someone else acted on it. Refresh to see the current state before trying again.",
        });
      } else {
        setNotice({
          kind: "error",
          message: error instanceof ApiError ? error.message : "request failed",
        });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageFrame>
      <LoadGate value={incident} retry={retryIncident}>
        {(detail) => {
          const active = detail.state !== "resolved";
          return (
            <div className="space-y-6">
              <PageHeader
                title={detail.title}
                status={
                  <>
                    <SeverityBadge severity={detail.severity} />
                    <IncidentStateBadge state={detail.state} />
                  </>
                }
                meta={
                  <>
                    <Link href="/incidents" className="hover:text-ink">
                      Incidents
                    </Link>
                    <span className="font-mono">
                      {detail.project_key}/{detail.environment_key}/{detail.service_key}
                    </span>
                    <span className="font-mono">
                      {detail.binding.cluster_ref}/{detail.binding.namespace}/
                      {detail.binding.workload_name}
                    </span>
                  </>
                }
                actions={
                  active && detail.can_acknowledge ? (
                    <button
                      type="button"
                      disabled={busy || detail.state === "acknowledged"}
                      onClick={() => acknowledge(detail)}
                      className="rounded-full bg-accent px-5 py-2.5 text-body font-medium text-ink-inverse transition-opacity disabled:opacity-50"
                    >
                      {detail.state === "acknowledged" ? "Acknowledged" : "Acknowledge"}
                    </button>
                  ) : undefined
                }
              />

              {notice.kind === "conflict" ? (
                <div role="alert" data-testid="ack-conflict">
                  <Panel tone="critical">
                    <DataState
                      kind="error"
                      title="Version conflict"
                      description={notice.message}
                      onRetry={refresh}
                    />
                  </Panel>
                </div>
              ) : null}
              {notice.kind === "error" ? (
                <div role="alert">
                  <Panel tone="critical">
                    <DataState kind="error" description={notice.message} onRetry={refresh} />
                  </Panel>
                </div>
              ) : null}
              {notice.kind === "done" ? (
                <p
                  role="status"
                  data-testid="ack-notice"
                  className="rounded-full bg-surface-2 px-4 py-2 text-caption text-ink-secondary"
                >
                  {notice.message}
                </p>
              ) : null}

              {active && !detail.can_acknowledge ? (
                <Panel>
                  <DataState
                    kind="permission-denied"
                    description="Acknowledging an incident needs incident.ack in this scope."
                  />
                </Panel>
              ) : null}

              <Panel
                className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
                data-testid="incident-lifecycle-panel"
              >
                <PanelHeader
                  title="Lifecycle"
                  description="Opened, acknowledged, resolved — as Drake recorded it, one honest timestamp at a time."
                />
                <IncidentLifecycle
                  openedAt={detail.opened_at}
                  acknowledgedAt={detail.acknowledged_at}
                  resolvedAt={detail.resolved_at}
                />
                <p className="text-micro text-ink-muted">
                  {active
                    ? "Acknowledging does not close the incident. It resolves on its own after two consecutive healthy evaluations."
                    : "Resolved — see below for how."}
                </p>
              </Panel>

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <Panel className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:40ms]">
                  <PanelHeader
                    title="Why it opened"
                    description="Opened after two consecutive trustworthy critical evaluations. A partial, stale, or last-known reading is never one of them."
                  />
                  <ReasonList reasons={detail.opening_reasons} />
                </Panel>

                <Panel className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:40ms]">
                  <PanelHeader title="Current health" />
                  {detail.current_health ? (
                    <div className="space-y-3">
                      <HealthBadge status={detail.current_health.status} />
                      <ReasonList reasons={detail.current_health.reasons} />
                      <p className="text-micro text-ink-muted">
                        Last observed{" "}
                        <time className="font-mono">
                          {detail.current_health.last_observed_at ?? "—"}
                        </time>
                      </p>
                      <Link
                        href={`/service-health/${detail.binding.id}`}
                        className="inline-block text-caption font-medium text-ink-secondary underline hover:text-ink"
                      >
                        Open service health
                      </Link>
                    </div>
                  ) : (
                    <DataState
                      kind="unknown"
                      description="No health state has been recorded for this binding yet."
                    />
                  )}
                </Panel>
              </div>

              <Panel
                flush
                className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:80ms]"
              >
                <PanelHeader
                  flush
                  title="Lifecycle events"
                  description="Append-only. Nothing on this screen edits or removes an entry."
                />
                <div className="px-7 py-5">
                  <LoadGate value={events} retry={retryEvents}>
                    {(payload) => <IncidentTimeline events={payload.events} />}
                  </LoadGate>
                </div>
              </Panel>

              <Panel
                flush
                className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:100ms]"
              >
                <PanelHeader flush title="Workload & timing" />
                <dl className="divide-y divide-border px-7 pb-2">
                  <MetaRow label="Workload">
                    <span className="font-mono text-xs">
                      {detail.binding.cluster_ref}/{detail.binding.namespace}/
                      {detail.binding.workload_kind}/{detail.binding.workload_name}
                    </span>
                  </MetaRow>
                  <MetaRow label="Opened">
                    <span className="font-mono text-xs">{detail.opened_at}</span>
                  </MetaRow>
                  <MetaRow label="Duration">
                    <span className="font-mono text-xs">
                      {formatDuration(detail.opened_at, detail.resolved_at)}
                    </span>
                  </MetaRow>
                  <MetaRow label="Last critical">
                    <span className="font-mono text-xs">{detail.last_critical_at}</span>
                  </MetaRow>
                  <MetaRow label="Acknowledged">
                    <span className="text-xs">
                      {detail.acknowledged_at ? (
                        <>
                          <time className="font-mono">{detail.acknowledged_at}</time>
                          {detail.acknowledged_by
                            ? ` · ${detail.acknowledged_by.display_name}`
                            : null}
                        </>
                      ) : (
                        <span className="italic text-ink-muted">not yet</span>
                      )}
                    </span>
                  </MetaRow>
                  <MetaRow label="Resolved">
                    <span className="text-xs">
                      {detail.resolved_at ? (
                        <>
                          <time className="font-mono">{detail.resolved_at}</time>
                          {detail.resolution_source === "health_recovered"
                            ? " · health recovered"
                            : null}
                        </>
                      ) : (
                        <span className="italic text-ink-muted">still active</span>
                      )}
                    </span>
                  </MetaRow>
                </dl>
              </Panel>
            </div>
          );
        }}
      </LoadGate>
    </PageFrame>
  );
}
