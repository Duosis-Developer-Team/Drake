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

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import {
  IncidentStateBadge,
  IncidentTimeline,
  ReasonList,
  SeverityBadge,
} from "@/components/incidents/primitives";
import { HealthBadge } from "@/components/service-health/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
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
    <div className="mx-auto max-w-5xl space-y-5">
      <LoadGate value={incident} retry={retryIncident}>
        {(detail) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-xs text-ink-muted">
                  <Link href="/incidents" className="hover:text-ink">
                    Incidents
                  </Link>{" "}
                  / <span className="font-mono">{detail.project_key}</span> /{" "}
                  <span className="font-mono">{detail.environment_key}</span> /{" "}
                  <span className="font-mono">{detail.service_key}</span>
                </p>
                <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
                  {detail.title}
                </h1>
              </div>
              <div className="flex items-center gap-2">
                <SeverityBadge severity={detail.severity} />
                <IncidentStateBadge state={detail.state} />
              </div>
            </div>

            {notice.kind === "conflict" ? (
              <div role="alert" data-testid="ack-conflict">
                <DataState
                  kind="error"
                  title="Version conflict"
                  description={notice.message}
                  onRetry={refresh}
                />
              </div>
            ) : null}
            {notice.kind === "error" ? (
              <div role="alert">
                <DataState kind="error" description={notice.message} onRetry={refresh} />
              </div>
            ) : null}
            {notice.kind === "done" ? (
              <p role="status" data-testid="ack-notice" className="text-xs text-ink-secondary">
                {notice.message}
              </p>
            ) : null}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card title="Why it opened">
                <ReasonList reasons={detail.opening_reasons} />
                <div className="mt-3 border-t border-border pt-3">
                  <p className="text-[11px] text-ink-muted">
                    Opened after two consecutive trustworthy critical evaluations. A partial,
                    stale, or last-known reading is never one of them.
                  </p>
                </div>
              </Card>

              <Card title="Current health">
                {detail.current_health ? (
                  <div className="space-y-2">
                    <HealthBadge status={detail.current_health.status} />
                    <ReasonList reasons={detail.current_health.reasons} />
                    <p className="text-[11px] text-ink-muted">
                      Last observed{" "}
                      <time className="font-mono">
                        {detail.current_health.last_observed_at ?? "—"}
                      </time>
                    </p>
                    <Link
                      href={`/service-health/${detail.binding.id}`}
                      className="inline-block text-xs font-medium text-ink-secondary underline hover:text-ink"
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
              </Card>
            </div>

            <Card title="Context">
              <dl className="divide-y divide-border">
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

              {detail.state !== "resolved" ? (
                <div className="mt-4 border-t border-border pt-3">
                  {detail.can_acknowledge ? (
                    <button
                      type="button"
                      disabled={busy || detail.state === "acknowledged"}
                      onClick={() => acknowledge(detail)}
                      className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                    >
                      {detail.state === "acknowledged" ? "Acknowledged" : "Acknowledge"}
                    </button>
                  ) : (
                    <DataState
                      kind="permission-denied"
                      description="Acknowledging an incident needs incident.ack in this scope."
                    />
                  )}
                  <p className="mt-2 text-[11px] text-ink-muted">
                    Acknowledging does not close the incident. It resolves on its own after
                    two consecutive healthy evaluations.
                  </p>
                </div>
              ) : null}
            </Card>

            <Card title="Lifecycle">
              <LoadGate value={events} retry={retryEvents}>
                {(payload) => <IncidentTimeline events={payload.events} />}
              </LoadGate>
            </Card>
          </>
        )}
      </LoadGate>
    </div>
  );
}
